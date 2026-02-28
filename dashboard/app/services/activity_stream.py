"""LinkedPilot v2 — Server-Sent Events (SSE) activity stream.

Provides real-time activity updates to the dashboard via SSE.
"""

import asyncio
import json
from datetime import datetime

from app.database import get_lp_db


class ActivityStream:
    """Broadcasts activity events to SSE subscribers and persists them."""

    def __init__(self):
        self._queue: asyncio.Queue = asyncio.Queue()

    async def push(self, event_type: str, data: dict) -> None:
        """Push an event to the broadcast queue and store it in the DB."""
        db = await get_lp_db()
        await db.execute(
            """INSERT INTO activity_log
               (action_type, sender_name, lead_name, lead_url,
                campaign_id, status, details)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                event_type,
                data.get("sender_name", ""),
                data.get("lead_name", ""),
                data.get("lead_url", ""),
                data.get("campaign_id"),
                data.get("status", ""),
                data.get("details", ""),
            ),
        )
        await db.commit()

        # Attach the newly-created row id so subscribers see it immediately.
        cursor = await db.execute("SELECT last_insert_rowid()")
        row = await cursor.fetchone()
        row_id = row[0] if row else None

        event = {
            "id": row_id,
            "action_type": event_type,
            "sender_name": data.get("sender_name", ""),
            "lead_name": data.get("lead_name", ""),
            "lead_url": data.get("lead_url", ""),
            "campaign_id": data.get("campaign_id"),
            "status": data.get("status", ""),
            "details": data.get("details", ""),
            "created_at": datetime.utcnow().isoformat(),
        }

        await self._queue.put(event)

    async def subscribe(self):
        """Async generator that yields SSE-formatted events."""
        while True:
            event = await self._queue.get()
            yield f"event: activity\ndata: {json.dumps(event)}\n\n"


# Module-level singleton -------------------------------------------------------
activity_stream = ActivityStream()


async def get_recent_activities(limit: int = 15) -> list[dict]:
    """Return the last *limit* entries from the activity_log table."""
    db = await get_lp_db()
    cursor = await db.execute(
        """SELECT id, action_type, sender_name, lead_name, lead_url,
                  campaign_id, status, details, created_at
             FROM activity_log
         ORDER BY id DESC
            LIMIT ?""",
        (limit,),
    )
    rows = await cursor.fetchall()
    return [
        {
            "id": row[0],
            "action_type": row[1],
            "sender_name": row[2],
            "lead_name": row[3],
            "lead_url": row[4],
            "campaign_id": row[5],
            "status": row[6],
            "details": row[7],
            "created_at": row[8],
        }
        for row in rows
    ]


async def event_generator(last_id: int = 0):
    """Async generator that yields SSE-formatted activity events.

    1. Catches up by yielding every activity with id > *last_id*.
    2. Then polls the database every 2 seconds for new rows.
    """
    db = await get_lp_db()

    # --- catch-up phase --------------------------------------------------------
    cursor = await db.execute(
        """SELECT id, action_type, sender_name, lead_name, lead_url,
                  campaign_id, status, details, created_at
             FROM activity_log
            WHERE id > ?
         ORDER BY id ASC""",
        (last_id,),
    )
    rows = await cursor.fetchall()
    for row in rows:
        event = {
            "id": row[0],
            "action_type": row[1],
            "sender_name": row[2],
            "lead_name": row[3],
            "lead_url": row[4],
            "campaign_id": row[5],
            "status": row[6],
            "details": row[7],
            "created_at": row[8],
        }
        last_id = event["id"]
        yield f"event: activity\ndata: {json.dumps(event)}\n\n"

    # --- live polling phase ----------------------------------------------------
    while True:
        await asyncio.sleep(2)

        cursor = await db.execute(
            """SELECT id, action_type, sender_name, lead_name, lead_url,
                      campaign_id, status, details, created_at
                 FROM activity_log
                WHERE id > ?
             ORDER BY id ASC""",
            (last_id,),
        )
        rows = await cursor.fetchall()
        for row in rows:
            event = {
                "id": row[0],
                "action_type": row[1],
                "sender_name": row[2],
                "lead_name": row[3],
                "lead_url": row[4],
                "campaign_id": row[5],
                "status": row[6],
                "details": row[7],
                "created_at": row[8],
            }
            last_id = event["id"]
            yield f"event: activity\ndata: {json.dumps(event)}\n\n"
