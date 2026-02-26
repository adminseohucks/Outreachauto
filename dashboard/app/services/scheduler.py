"""
Campaign Scheduler for LinkedPilot v2.

Manages campaign execution using plain asyncio tasks (no APScheduler).
All senders can run simultaneously -- each campaign gets its own task.
"""

import asyncio
import logging
import random
from datetime import datetime, timedelta, timezone

from app.database import get_lp_db
from app.services.rate_limiter import (
    check_limit,
    check_work_hours,
    check_cooldown,
    increment_counter,
)

logger = logging.getLogger(__name__)

# IST is UTC+5:30
IST = timezone(timedelta(hours=5, minutes=30))

# Random delay bounds (seconds) between actions to look human
MIN_DELAY = 30
MAX_DELAY = 120


# ---------------------------------------------------------------------------
# Placeholder action executor
# ---------------------------------------------------------------------------

async def _execute_action(action: dict) -> bool:
    """
    Placeholder: execute a single like or comment action.

    In production this will call the browser-automation engine. For now it
    simulates success after a short delay.

    Args:
        action: Row dict from action_queue.

    Returns:
        True on success, False on failure.
    """
    logger.info(
        "Executing %s action (id=%s) on %s for sender %s",
        action.get("action_type"),
        action.get("id"),
        action.get("profile_url"),
        action.get("sender_id"),
    )
    # Simulate work
    await asyncio.sleep(random.uniform(2, 5))
    return True


# ---------------------------------------------------------------------------
# CampaignScheduler
# ---------------------------------------------------------------------------

class CampaignScheduler:
    """Manages asyncio tasks for running campaigns."""

    def __init__(self) -> None:
        # campaign_id -> asyncio.Task
        self._tasks: dict[int, asyncio.Task] = {}
        # campaign_id -> Event used to signal pause/stop
        self._pause_flags: dict[int, asyncio.Event] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def start_campaign(self, campaign_id: int) -> None:
        """Create an asyncio task to run the given campaign."""
        if campaign_id in self._tasks and not self._tasks[campaign_id].done():
            logger.warning("Campaign %s is already running.", campaign_id)
            return

        pause_event = asyncio.Event()
        pause_event.set()  # not paused by default
        self._pause_flags[campaign_id] = pause_event

        task = asyncio.create_task(
            self._run_campaign(campaign_id),
            name=f"campaign-{campaign_id}",
        )
        self._tasks[campaign_id] = task
        logger.info("Started campaign %s", campaign_id)

    async def pause_campaign(self, campaign_id: int) -> None:
        """Signal the campaign loop to stop after its current action."""
        event = self._pause_flags.get(campaign_id)
        if event is None:
            logger.warning("Campaign %s not found for pausing.", campaign_id)
            return
        event.clear()  # clearing causes the loop to wait
        logger.info("Pause requested for campaign %s", campaign_id)

        # Also update DB status
        db = await get_lp_db()
        await db.execute(
            "UPDATE campaigns SET status = 'paused' WHERE id = ?",
            (campaign_id,),
        )
        await db.commit()

    async def cancel_campaign(self, campaign_id: int) -> None:
        """Cancel the asyncio task for a campaign."""
        task = self._tasks.get(campaign_id)
        if task and not task.done():
            task.cancel()
            logger.info("Cancelled campaign %s", campaign_id)

        # Clean up
        self._tasks.pop(campaign_id, None)
        self._pause_flags.pop(campaign_id, None)

        # Update DB status
        db = await get_lp_db()
        await db.execute(
            "UPDATE campaigns SET status = 'cancelled' WHERE id = ?",
            (campaign_id,),
        )
        await db.commit()

    async def get_running_campaigns(self) -> list[int]:
        """Return a list of campaign IDs that are currently running."""
        running = []
        for cid, task in list(self._tasks.items()):
            if not task.done():
                running.append(cid)
            else:
                # Housekeeping: remove finished tasks
                self._tasks.pop(cid, None)
                self._pause_flags.pop(cid, None)
        return running

    # ------------------------------------------------------------------
    # Internal: main campaign loop
    # ------------------------------------------------------------------

    async def _run_campaign(self, campaign_id: int) -> None:
        """
        Main execution loop for a single campaign.

        Steps:
        1. Fetch campaign details.
        2. Fetch pending actions from action_queue.
        3. For each action:
           a. Check work hours (sleep if outside).
           b. Check rate limit for the sender.
           c. Check cooldown for the lead's profile_url.
           d. If all ok, mark 'running', execute, then mark 'done'/'failed'.
           e. Log to activity_log.
           f. Sleep a random delay.
        4. When all actions are done, mark the campaign completed.
        """
        db = await get_lp_db()

        try:
            # ---- 1. Get campaign details ----
            cursor = await db.execute(
                "SELECT * FROM campaigns WHERE id = ?", (campaign_id,)
            )
            campaign = await cursor.fetchone()
            if not campaign:
                logger.error("Campaign %s not found.", campaign_id)
                return

            # Mark campaign as active
            await db.execute(
                "UPDATE campaigns SET status = 'active', started_at = ? WHERE id = ?",
                (datetime.now(IST).isoformat(), campaign_id),
            )
            await db.commit()

            # ---- 2. Get pending actions ----
            cursor = await db.execute(
                """
                SELECT * FROM action_queue
                WHERE campaign_id = ? AND status = 'pending'
                ORDER BY id ASC
                """,
                (campaign_id,),
            )
            rows = await cursor.fetchall()

            # Convert to list of dicts
            if rows:
                columns = [desc[0] for desc in cursor.description]
                actions = [dict(zip(columns, row)) for row in rows]
            else:
                actions = []

            if not actions:
                logger.info("No pending actions for campaign %s.", campaign_id)
                await self._mark_campaign_completed(campaign_id)
                return

            logger.info(
                "Campaign %s: %d pending actions to process.", campaign_id, len(actions)
            )

            # ---- 3. Process each action ----
            for action in actions:
                # Check if paused
                pause_event = self._pause_flags.get(campaign_id)
                if pause_event and not pause_event.is_set():
                    logger.info("Campaign %s is paused. Stopping loop.", campaign_id)
                    return

                action_id = action["id"]
                sender_id = action.get("sender_id")
                action_type = action.get("action_type", "like")
                profile_url = action.get("profile_url", "")

                # ---- 3a. Check work hours ----
                while not await check_work_hours():
                    logger.info(
                        "Outside work hours. Campaign %s sleeping for 15 min...",
                        campaign_id,
                    )
                    await asyncio.sleep(900)  # 15 minutes
                    # Re-check pause flag
                    if pause_event and not pause_event.is_set():
                        logger.info("Campaign %s paused during work-hour wait.", campaign_id)
                        return

                # ---- 3b. Check rate limit ----
                limit_result = await check_limit(sender_id, action_type)
                if not limit_result["allowed"]:
                    logger.info(
                        "Rate limit hit for sender %s (%s): %s. Skipping action %s.",
                        sender_id, action_type, limit_result["reason"], action_id,
                    )
                    await db.execute(
                        "UPDATE action_queue SET status = 'skipped' WHERE id = ?",
                        (action_id,),
                    )
                    await db.commit()
                    await self._log_activity(
                        campaign_id, sender_id, action_type, profile_url,
                        "skipped", limit_result["reason"],
                    )
                    continue

                # ---- 3c. Check cooldown ----
                if profile_url and await check_cooldown(profile_url, sender_id):
                    logger.info(
                        "Cooldown active for %s. Skipping action %s.",
                        profile_url, action_id,
                    )
                    await db.execute(
                        "UPDATE action_queue SET status = 'skipped' WHERE id = ?",
                        (action_id,),
                    )
                    await db.commit()
                    await self._log_activity(
                        campaign_id, sender_id, action_type, profile_url,
                        "skipped", "Cooldown active",
                    )
                    continue

                # ---- 3d. Execute action ----
                await db.execute(
                    "UPDATE action_queue SET status = 'running' WHERE id = ?",
                    (action_id,),
                )
                await db.commit()

                success = False
                try:
                    success = await _execute_action(action)
                except Exception as exc:
                    logger.error("Action %s failed with exception: %s", action_id, exc)

                # ---- 3e. Update status ----
                new_status = "done" if success else "failed"
                await db.execute(
                    "UPDATE action_queue SET status = ?, completed_at = ? WHERE id = ?",
                    (new_status, datetime.now(IST).isoformat(), action_id),
                )
                await db.commit()

                if success:
                    await increment_counter(sender_id, action_type)

                # ---- 3e cont. Log to activity_log ----
                await self._log_activity(
                    campaign_id, sender_id, action_type, profile_url,
                    new_status, "",
                )

                # ---- 3f. Random delay ----
                delay = random.uniform(MIN_DELAY, MAX_DELAY)
                logger.debug("Sleeping %.1f seconds before next action.", delay)
                await asyncio.sleep(delay)

            # ---- 4. All actions processed ----
            await self._mark_campaign_completed(campaign_id)

        except asyncio.CancelledError:
            logger.info("Campaign %s task was cancelled.", campaign_id)
            raise
        except Exception as exc:
            logger.exception("Unexpected error in campaign %s: %s", campaign_id, exc)
            # Mark campaign as paused on error (can be retried)
            try:
                db = await get_lp_db()
                await db.execute(
                    "UPDATE campaigns SET status = 'paused' WHERE id = ?",
                    (campaign_id,),
                )
                await db.commit()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _mark_campaign_completed(self, campaign_id: int) -> None:
        """Mark a campaign as completed in the database."""
        db = await get_lp_db()
        await db.execute(
            "UPDATE campaigns SET status = 'completed', completed_at = ? WHERE id = ?",
            (datetime.now(IST).isoformat(), campaign_id),
        )
        await db.commit()
        logger.info("Campaign %s completed.", campaign_id)

        # Cleanup references
        self._tasks.pop(campaign_id, None)
        self._pause_flags.pop(campaign_id, None)

    async def _log_activity(
        self,
        campaign_id: int,
        sender_id: int,
        action_type: str,
        lead_url: str,
        status: str,
        details: str,
    ) -> None:
        """Insert a row into the activity_log table."""
        try:
            db = await get_lp_db()
            # Get sender name
            sender_name = ""
            if sender_id:
                cursor = await db.execute(
                    "SELECT name FROM senders WHERE id = ?", (sender_id,)
                )
                row = await cursor.fetchone()
                if row:
                    sender_name = row["name"]

            await db.execute(
                """
                INSERT INTO activity_log
                    (action_type, sender_id, sender_name, lead_url, campaign_id, status, details)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (action_type, sender_id, sender_name, lead_url, campaign_id, status, details),
            )
            await db.commit()
        except Exception as exc:
            logger.error("Failed to log activity: %s", exc)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

scheduler = CampaignScheduler()
