"""LinkedPilot v2 — Main dashboard router."""

from datetime import date

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.config import TEMPLATES_DIR
from app.database import get_lp_db
from app.services.openoutreach_reader import get_lead_stats

router = APIRouter()
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


@router.get("/", response_class=HTMLResponse)
async def dashboard_home(request: Request):
    """Main dashboard page with stats, activity log, and sender cards."""
    import asyncio

    db = await get_lp_db()
    today = date.today().isoformat()

    # --- Run CRM lead_stats concurrently with local DB queries ---
    lead_stats_task = asyncio.create_task(get_lead_stats())

    # --- Senders ---
    cursor = await db.execute("SELECT * FROM senders ORDER BY name")
    senders = [dict(row) for row in await cursor.fetchall()]
    active_senders = [s for s in senders if s["status"] == "active"]

    # --- Today's totals: likes + comments in one query ---
    cursor = await db.execute(
        "SELECT action_type, COALESCE(SUM(count), 0) AS total "
        "FROM daily_counters WHERE date = ? AND action_type IN ('like', 'comment') "
        "GROUP BY action_type",
        (today,),
    )
    totals = {row["action_type"]: row["total"] for row in await cursor.fetchall()}
    likes_today = totals.get("like", 0)
    comments_today = totals.get("comment", 0)

    # --- Active campaigns ---
    cursor = await db.execute(
        "SELECT COUNT(*) AS cnt FROM campaigns WHERE status = 'active'"
    )
    row = await cursor.fetchone()
    active_campaigns = row["cnt"] if row else 0

    # --- Last 15 activity log entries ---
    cursor = await db.execute(
        "SELECT * FROM activity_log ORDER BY created_at DESC LIMIT 15"
    )
    activities = [dict(row) for row in await cursor.fetchall()]

    # --- Per-sender today stats (single query for all senders) ---
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

    sender_cards = []
    for sender in senders:
        stats = sender_stats.get(sender["id"], {})
        sender_cards.append({
            **sender,
            "likes_today": stats.get("like", 0),
            "comments_today": stats.get("comment", 0),
            "connects_today": stats.get("connect", 0),
        })

    # --- Await CRM stats (ran concurrently) ---
    lead_stats = await lead_stats_task

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "lead_stats": lead_stats,
        "active_senders_count": len(active_senders),
        "likes_today": likes_today,
        "comments_today": comments_today,
        "active_campaigns": active_campaigns,
        "activities": activities,
        "sender_cards": sender_cards,
        "active_page": "dashboard",
    })
