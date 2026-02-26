"""LinkedPilot v2 — API routes and SSE activity stream."""

import asyncio
import json
from datetime import date, datetime

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from sse_starlette.sse import EventSourceResponse

from app.database import get_lp_db
from app.services.activity_stream import activity_stream, get_recent_activities

router = APIRouter()


@router.get("/api/activity-stream")
async def activity_stream_sse(request: Request, last_id: int = 0):
    """
    Server-Sent Events endpoint for real-time activity updates.

    1. On connect, send the last 15 activities (or catch up from last_id).
    2. Then poll activity_log every 2 seconds for new entries.
    3. New entries are sent as SSE events with event type "activity".
    """
    db = await get_lp_db()

    async def event_generator():
        nonlocal last_id

        # --- Phase 1: Catch-up / initial burst ---
        if last_id == 0:
            # Send last 15 activities on fresh connect
            recent = await get_recent_activities(limit=15)
            # Send in chronological order (oldest first)
            for activity in reversed(recent):
                if activity["id"] and activity["id"] > last_id:
                    last_id = activity["id"]
                yield {
                    "event": "activity",
                    "data": json.dumps(activity),
                }
        else:
            # Catch up from the provided last_id
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
                    "id": row["id"],
                    "action_type": row["action_type"],
                    "sender_name": row["sender_name"],
                    "lead_name": row["lead_name"],
                    "lead_url": row["lead_url"],
                    "campaign_id": row["campaign_id"],
                    "status": row["status"],
                    "details": row["details"],
                    "created_at": row["created_at"],
                }
                last_id = event["id"]
                yield {
                    "event": "activity",
                    "data": json.dumps(event),
                }

        # --- Phase 2: Live polling every 10 seconds ---
        missed_polls = 0
        while missed_polls < 6:  # Auto-close after ~60s of no new data
            await asyncio.sleep(10)

            # Check if client disconnected
            if await request.is_disconnected():
                break

            try:
                cursor = await db.execute(
                    """SELECT id, action_type, sender_name, lead_name, lead_url,
                              campaign_id, status, details, created_at
                         FROM activity_log
                        WHERE id > ?
                     ORDER BY id ASC
                     LIMIT 20""",
                    (last_id,),
                )
                rows = await cursor.fetchall()
            except Exception:
                break

            if not rows:
                missed_polls += 1
                # Send keepalive ping so browser knows connection is alive
                yield {"event": "ping", "data": ""}
                continue

            missed_polls = 0
            for row in rows:
                event = {
                    "id": row["id"],
                    "action_type": row["action_type"],
                    "sender_name": row["sender_name"],
                    "lead_name": row["lead_name"],
                    "lead_url": row["lead_url"],
                    "campaign_id": row["campaign_id"],
                    "status": row["status"],
                    "details": row["details"],
                    "created_at": row["created_at"],
                }
                last_id = event["id"]
                yield {
                    "event": "activity",
                    "data": json.dumps(event),
                }

    return EventSourceResponse(event_generator(), ping=30)


@router.get("/api/stats")
async def api_stats(request: Request):
    """
    Return JSON with current stats for HTMX polling.

    Returns likes_today, comments_today, active_campaigns, active_senders,
    and total_actions_pending.
    """
    db = await get_lp_db()
    today = date.today().isoformat()

    # Today's likes across all senders
    cursor = await db.execute(
        "SELECT COALESCE(SUM(count), 0) AS total "
        "FROM daily_counters WHERE date = ? AND action_type = 'like'",
        (today,),
    )
    row = await cursor.fetchone()
    likes_today = row["total"] if row else 0

    # Today's comments across all senders
    cursor = await db.execute(
        "SELECT COALESCE(SUM(count), 0) AS total "
        "FROM daily_counters WHERE date = ? AND action_type = 'comment'",
        (today,),
    )
    row = await cursor.fetchone()
    comments_today = row["total"] if row else 0

    # Active campaigns
    cursor = await db.execute(
        "SELECT COUNT(*) AS cnt FROM campaigns WHERE status = 'active'"
    )
    row = await cursor.fetchone()
    active_campaigns = row["cnt"] if row else 0

    # Active senders
    cursor = await db.execute(
        "SELECT COUNT(*) AS cnt FROM senders WHERE status = 'active'"
    )
    row = await cursor.fetchone()
    active_senders = row["cnt"] if row else 0

    # Pending actions in queue
    cursor = await db.execute(
        "SELECT COUNT(*) AS cnt FROM action_queue WHERE status IN ('pending', 'scheduled')"
    )
    row = await cursor.fetchone()
    total_actions_pending = row["cnt"] if row else 0

    return JSONResponse({
        "likes_today": likes_today,
        "comments_today": comments_today,
        "active_campaigns": active_campaigns,
        "active_senders": active_senders,
        "total_actions_pending": total_actions_pending,
        "timestamp": datetime.now().strftime("%H:%M:%S"),
    })


@router.get("/api/sender-status")
async def api_sender_status(request: Request):
    """
    Return JSON with all senders' current status and today's counters.

    Each sender includes: id, name, status, likes_today, comments_today,
    daily_like_limit, daily_comment_limit, and running_campaigns.
    """
    db = await get_lp_db()
    today = date.today().isoformat()

    cursor = await db.execute("SELECT * FROM senders ORDER BY name")
    senders = [dict(row) for row in await cursor.fetchall()]

    # Batch: all sender stats for today in one query
    cursor = await db.execute(
        "SELECT sender_id, action_type, COALESCE(SUM(count), 0) AS total "
        "FROM daily_counters WHERE date = ? GROUP BY sender_id, action_type",
        (today,),
    )
    sender_stats = {}
    for row in await cursor.fetchall():
        sid = row["sender_id"]
        if sid not in sender_stats:
            sender_stats[sid] = {"like": 0, "comment": 0, "connect": 0}
        sender_stats[sid][row["action_type"]] = row["total"]

    # Batch: all active campaigns grouped by sender
    cursor = await db.execute(
        "SELECT id, name, campaign_type, processed, total_leads, sender_id "
        "FROM campaigns WHERE status = 'active'"
    )
    campaigns_by_sender = {}
    for row in await cursor.fetchall():
        r = dict(row)
        sid = r.pop("sender_id")
        campaigns_by_sender.setdefault(sid, []).append(r)

    result = []
    for sender in senders:
        stats = sender_stats.get(sender["id"], {})
        result.append({
            "id": sender["id"],
            "name": sender["name"],
            "status": sender["status"],
            "likes_today": stats.get("like", 0),
            "comments_today": stats.get("comment", 0),
            "connects_today": stats.get("connect", 0),
            "daily_like_limit": sender["daily_like_limit"],
            "daily_comment_limit": sender["daily_comment_limit"],
            "daily_connect_limit": sender.get("daily_connect_limit", 25),
            "running_campaigns": campaigns_by_sender.get(sender["id"], []),
        })

    return JSONResponse({"senders": result})
