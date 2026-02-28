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
from app.config import (
    LIKE_MIN_DELAY,
    LIKE_MAX_DELAY,
    COMMENT_MIN_DELAY,
    COMMENT_MAX_DELAY,
    CONNECT_MIN_DELAY,
    CONNECT_MAX_DELAY,
    CONTACT_COOLDOWN_DAYS,
)
from app.services.rate_limiter import (
    check_limit,
    check_work_hours,
    check_cooldown,
    increment_counter,
)
from app.services.ai_comment import ask_ai_for_comment, generate_ai_comment

logger = logging.getLogger(__name__)

# IST is UTC+5:30
IST = timezone(timedelta(hours=5, minutes=30))


async def _delay_bounds(action_type: str) -> tuple[int, int]:
    """Return (min_delay, max_delay) in seconds for the given action type.

    Reads from DB settings first, falls back to config defaults.
    """
    from app.services.rate_limiter import _get_db_setting

    if action_type == "comment":
        mn = await _get_db_setting("comment_delay_min", "")
        mx = await _get_db_setting("comment_delay_max", "")
        return (int(mn) if mn else COMMENT_MIN_DELAY, int(mx) if mx else COMMENT_MAX_DELAY)
    elif action_type == "connect":
        mn = await _get_db_setting("connect_delay_min", "")
        mx = await _get_db_setting("connect_delay_max", "")
        return (int(mn) if mn else CONNECT_MIN_DELAY, int(mx) if mx else CONNECT_MAX_DELAY)
    mn = await _get_db_setting("like_delay_min", "")
    mx = await _get_db_setting("like_delay_max", "")
    return (int(mn) if mn else LIKE_MIN_DELAY, int(mx) if mx else LIKE_MAX_DELAY)


# ---------------------------------------------------------------------------
# Action executor — calls real browser automation
# ---------------------------------------------------------------------------

async def _execute_action(action: dict) -> bool:
    """Execute a single like, comment, or connect action via Playwright.

    Uses the browser_manager to get the sender's browser page, then
    delegates to the appropriate automation module.

    Returns True on success, False on failure.
    """
    from app.automation.browser import browser_manager

    action_type = action.get("action_type", "like")
    sender_id = action.get("sender_id")
    profile_url = action.get("profile_url", "")
    company_page = action.get("company_page_name")

    logger.info(
        "Executing %s action (id=%s) on %s for sender %s%s",
        action_type,
        action.get("id"),
        profile_url,
        sender_id,
        f" as page '{company_page}'" if company_page else "",
    )

    if not profile_url:
        logger.error("No profile_url for action %s", action.get("id"))
        return False

    # Get the sender's browser page
    if not browser_manager.is_open(sender_id):
        # Need to get sender's browser_profile to open context
        db = await get_lp_db()
        cursor = await db.execute(
            "SELECT browser_profile FROM senders WHERE id = ?", (sender_id,)
        )
        sender_row = await cursor.fetchone()
        if not sender_row:
            logger.error("Sender %s not found in DB", sender_id)
            return False
        await browser_manager.get_context(sender_id, sender_row["browser_profile"])

    try:
        page = await browser_manager.get_page(sender_id)
    except Exception as exc:
        logger.error("Could not get browser page for sender %s: %s", sender_id, exc)
        return False

    # Dispatch to the correct automation module
    if action_type == "like":
        from app.automation.linkedin_like import like_latest_post
        result = await like_latest_post(page, profile_url, company_page)
        return result.get("success", False)

    elif action_type == "comment":
        from app.automation.linkedin_comment import (
            comment_on_latest_post,
            extract_post_text,
        )
        from app.services.ai_comment import should_skip_post

        comment_text = action.get("comment_text", "")

        if not comment_text:
            # --- AI comment generation ---
            # 1. Extract post text + last 5 existing comments for tone context
            post_text = ""
            existing_comments: list[str] = []
            try:
                extract_result = await extract_post_text(page, profile_url)
                post_text = extract_result.get("post_text") or ""
                existing_comments = extract_result.get("existing_comments", [])[:5]

                if existing_comments:
                    logger.info(
                        "Extracted %d existing comments for AI tone context",
                        len(existing_comments),
                    )
                if post_text:
                    logger.info(
                        "Extracted post text (%d chars) for AI comment generation",
                        len(post_text),
                    )
            except Exception as exc:
                logger.warning("Post text extraction failed: %s", exc)

            if not post_text:
                logger.error(
                    "No post text extracted for action %s — cannot generate comment",
                    action.get("id"),
                )
                return False

            # 1b. Check if post should be skipped (hiring, recruitment, etc.)
            skip, skip_reason = should_skip_post(post_text)
            if skip:
                logger.info(
                    "SKIPPING comment action %s on %s — %s",
                    action.get("id"), profile_url, skip_reason,
                )
                print(f"  [Comment] SKIPPED: {skip_reason} — {profile_url}")
                return False

            # 2. Ask AI to generate an original comment
            ai_result = await generate_ai_comment(
                post_text=post_text,
                existing_comments=existing_comments,
                tone="professional",
            )
            comment_text = ai_result.get("comment_text", "")
            confidence = ai_result.get("confidence", 0.0)

            if not comment_text:
                logger.error(
                    "AI failed to generate comment for action %s", action.get("id")
                )
                return False

            logger.info(
                "AI generated comment (confidence=%.2f): %.80s...",
                confidence,
                comment_text,
            )

            # 3. Save the generated comment to action_queue
            db = await get_lp_db()
            await db.execute(
                "UPDATE action_queue SET comment_text = ? WHERE id = ?",
                (comment_text, action.get("id")),
            )
            await db.commit()

        result = await comment_on_latest_post(page, profile_url, comment_text, company_page)

        # Handle the "too few comments" skip case
        if result.get("skipped_low_comments"):
            logger.info("Comment skipped (low comment count) for %s", profile_url)
            return False

        return result.get("success", False)

    elif action_type == "connect":
        from app.automation.linkedin_connect import send_connection_request
        connect_note = action.get("connect_note")
        result = await send_connection_request(page, profile_url, connect_note)
        return result.get("success", False)

    else:
        logger.error("Unknown action_type: %s", action_type)
        return False


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

    async def resume_active_campaigns(self) -> None:
        """Resume any campaigns that are marked 'active' in the DB.

        Called at startup to recover campaigns that were running when
        the server was restarted.
        """
        db = await get_lp_db()
        cursor = await db.execute(
            "SELECT id FROM campaigns WHERE status = 'active'"
        )
        rows = await cursor.fetchall()
        for row in rows:
            campaign_id = row["id"] if isinstance(row, dict) else row[0]
            logger.info("Resuming active campaign %s from DB", campaign_id)
            await self.start_campaign(campaign_id)

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

            # ---- 2. Get pending actions (with company page name and lead info) ----
            cursor = await db.execute(
                """
                SELECT aq.*, cp.page_name AS company_page_name,
                       cll.profile_url, cll.full_name AS lead_name
                FROM action_queue aq
                LEFT JOIN company_pages cp ON aq.company_page_id = cp.id
                LEFT JOIN custom_list_leads cll ON aq.lead_id = cll.id
                WHERE aq.campaign_id = ? AND aq.status = 'pending'
                ORDER BY aq.id ASC
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
                _lead_name = action.get("lead_name", "")

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
                    print(
                        f"  [Campaign {campaign_id}] RATE LIMIT: {limit_result['reason']} "
                        f"(sender {sender_id}). Skipping."
                    )
                    await db.execute(
                        "UPDATE action_queue SET status = 'skipped' WHERE id = ?",
                        (action_id,),
                    )
                    await db.execute(
                        "UPDATE campaigns SET processed = processed + 1, skipped = skipped + 1 WHERE id = ?",
                        (campaign_id,),
                    )
                    await db.commit()
                    await self._log_activity(
                        campaign_id, sender_id, action_type, profile_url,
                        "skipped", limit_result["reason"], lead_name=_lead_name,
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
                    await db.execute(
                        "UPDATE campaigns SET processed = processed + 1, skipped = skipped + 1 WHERE id = ?",
                        (campaign_id,),
                    )
                    await db.commit()
                    await self._log_activity(
                        campaign_id, sender_id, action_type, profile_url,
                        "skipped", "Cooldown active", lead_name=_lead_name,
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

                # Update campaign progress counters
                stat_col = "successful" if success else "failed"
                await db.execute(
                    f"UPDATE campaigns SET processed = processed + 1, {stat_col} = {stat_col} + 1 WHERE id = ?",
                    (campaign_id,),
                )
                await db.commit()

                if success:
                    await increment_counter(sender_id, action_type)
                    # Record in global_contact_registry for cross-sender cooldown
                    await self._record_cooldown(profile_url, sender_id, action_type)

                    # Update lead status + flags in custom_list_leads
                    lead_id = action.get("lead_id")
                    if lead_id:
                        status_map = {"like": "liked", "comment": "commented", "connect": "connected"}
                        new_lead_status = status_map.get(action_type, "liked")
                        flag_updates = []
                        if action_type == "like":
                            flag_updates.append("is_liked = 1")
                        elif action_type == "comment":
                            flag_updates.append("is_commented = 1")
                        flag_sql = ", ".join(flag_updates) + ", " if flag_updates else ""
                        await db.execute(
                            f"UPDATE custom_list_leads SET {flag_sql}status = ?, last_action_at = ? WHERE id = ?",
                            (new_lead_status, datetime.now(IST).isoformat(), lead_id),
                        )
                        await db.commit()

                # ---- 3e cont. Log to activity_log ----
                company_page = action.get("company_page_name", "")
                lead_name = action.get("lead_name", "")
                detail_msg = f"as {company_page}" if company_page else ""
                await self._log_activity(
                    campaign_id, sender_id, action_type, profile_url,
                    new_status, detail_msg, lead_name=lead_name,
                )

                # ---- 3f. Human-like random delay between actions ----
                min_d, max_d = await _delay_bounds(action_type)
                # Add extra randomness: sometimes short, sometimes long
                delay = random.uniform(min_d, max_d)
                # Occasionally add a longer "think" pause (20% chance)
                if random.random() < 0.2:
                    delay += random.uniform(60, 180)
                delay_min = int(delay // 60)
                delay_sec = int(delay % 60)
                logger.info(
                    "Sleeping %dm %ds before next action (human-like delay).",
                    delay_min, delay_sec,
                )
                print(
                    f"  [Campaign {campaign_id}] Waiting {delay_min}m {delay_sec}s "
                    f"before next action..."
                )
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

    async def _record_cooldown(
        self,
        profile_url: str,
        sender_id: int,
        action_type: str,
    ) -> None:
        """Insert/update a row in global_contact_registry for cross-sender cooldown."""
        if not profile_url:
            return
        try:
            db = await get_lp_db()
            now = datetime.now(IST).isoformat()
            cooldown_until = (
                datetime.now(IST) + timedelta(days=CONTACT_COOLDOWN_DAYS)
            ).isoformat()

            await db.execute(
                """
                INSERT INTO global_contact_registry
                    (profile_url, sender_id, action_type, acted_at, cooldown_until)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(profile_url, sender_id, action_type)
                DO UPDATE SET acted_at = ?, cooldown_until = ?
                """,
                (profile_url, sender_id, action_type, now, cooldown_until,
                 now, cooldown_until),
            )
            await db.commit()
        except Exception as exc:
            logger.error("Failed to record cooldown: %s", exc)

    async def _log_activity(
        self,
        campaign_id: int,
        sender_id: int,
        action_type: str,
        lead_url: str,
        status: str,
        details: str,
        lead_name: str = "",
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
                    (action_type, sender_id, sender_name, lead_name, lead_url, campaign_id, status, details)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (action_type, sender_id, sender_name, lead_name, lead_url, campaign_id, status, details),
            )
            await db.commit()
        except Exception as exc:
            logger.error("Failed to log activity: %s", exc)


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

scheduler = CampaignScheduler()
