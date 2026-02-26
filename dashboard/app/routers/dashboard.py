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
    db = await get_lp_db()
    today = date.today().isoformat()

    # --- Lead stats from CRM (read-only) ---
    lead_stats = await get_lead_stats()

    # --- Active senders ---
    cursor = await db.execute("SELECT * FROM senders ORDER BY name")
    senders = [dict(row) for row in await cursor.fetchall()]
    active_senders = [s for s in senders if s["status"] == "active"]

    # --- Today's totals across all senders ---
    cursor = await db.execute(
        "SELECT COALESCE(SUM(count), 0) AS total "
        "FROM daily_counters WHERE date = ? AND action_type = 'like'",
        (today,),
    )
    row = await cursor.fetchone()
    likes_today = row["total"] if row else 0

    cursor = await db.execute(
        "SELECT COALESCE(SUM(count), 0) AS total "
        "FROM daily_counters WHERE date = ? AND action_type = 'comment'",
        (today,),
    )
    row = await cursor.fetchone()
    comments_today = row["total"] if row else 0

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

    # --- Per-sender today stats ---
    sender_cards = []
    for sender in senders:
        cursor = await db.execute(
            "SELECT COALESCE(SUM(count), 0) AS total "
            "FROM daily_counters WHERE date = ? AND sender_id = ? AND action_type = 'like'",
            (today, sender["id"]),
        )
        row = await cursor.fetchone()
        sender_likes = row["total"] if row else 0

        cursor = await db.execute(
            "SELECT COALESCE(SUM(count), 0) AS total "
            "FROM daily_counters WHERE date = ? AND sender_id = ? AND action_type = 'comment'",
            (today, sender["id"]),
        )
        row = await cursor.fetchone()
        sender_comments = row["total"] if row else 0

        sender_cards.append({
            **sender,
            "likes_today": sender_likes,
            "comments_today": sender_comments,
        })

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
