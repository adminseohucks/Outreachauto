"""LinkedPilot v2 — Extension API routes.

Provides JSON endpoints for the Chrome extension to:
- Fetch active campaigns and their leads
- Get next unprocessed lead for a campaign
- Mark a lead action as done/failed
- Fetch sender info
"""

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.database import get_lp_db

router = APIRouter(prefix="/api/ext", tags=["extension"])

IST = timezone(timedelta(hours=5, minutes=30))


@router.get("/campaigns")
async def ext_campaigns(request: Request):
    """Return active comment campaigns with lead counts for the extension."""
    db = await get_lp_db()
    cursor = await db.execute(
        """
        SELECT c.id, c.name, c.campaign_type, c.status, c.total_leads,
               c.processed, c.successful, c.failed, c.skipped,
               c.sender_id, c.company_page_id,
               cl.name AS list_name,
               s.name AS sender_name,
               cp.page_name AS company_page_name,
               COALESCE(pq.cnt, 0) AS pending_count
        FROM campaigns c
        LEFT JOIN custom_lists cl ON c.list_id = cl.id
        LEFT JOIN senders s ON c.sender_id = s.id
        LEFT JOIN company_pages cp ON c.company_page_id = cp.id
        LEFT JOIN (
            SELECT campaign_id, COUNT(*) AS cnt
            FROM action_queue WHERE status = 'pending'
            GROUP BY campaign_id
        ) pq ON pq.campaign_id = c.id
        WHERE c.status IN ('active', 'draft')
        AND c.campaign_type = 'comment'
        ORDER BY c.created_at DESC
        """
    )
    campaigns = [dict(row) for row in await cursor.fetchall()]

    return JSONResponse({"campaigns": campaigns})


@router.get("/campaigns/{campaign_id}/next-lead")
async def ext_next_lead(request: Request, campaign_id: int):
    """Return the next unprocessed lead for a campaign.

    Returns the lead's profile URL and metadata so the extension can
    navigate to their profile and read the latest post.
    """
    db = await get_lp_db()

    # Get campaign info first
    cursor = await db.execute(
        """
        SELECT c.*, s.name AS sender_name, cp.page_name AS company_page_name
        FROM campaigns c
        LEFT JOIN senders s ON c.sender_id = s.id
        LEFT JOIN company_pages cp ON c.company_page_id = cp.id
        WHERE c.id = ?
        """,
        (campaign_id,),
    )
    campaign = await cursor.fetchone()
    if not campaign:
        return JSONResponse({"error": "Campaign not found"}, status_code=404)

    # Get next pending action with lead info
    cursor = await db.execute(
        """
        SELECT aq.id AS action_id, aq.status,
               cll.id AS lead_id, cll.full_name, cll.first_name,
               cll.headline, cll.company, cll.profile_url
        FROM action_queue aq
        JOIN custom_list_leads cll ON aq.lead_id = cll.id
        WHERE aq.campaign_id = ? AND aq.status = 'pending'
        ORDER BY aq.id ASC
        LIMIT 1
        """,
        (campaign_id,),
    )
    action = await cursor.fetchone()
    if not action:
        return JSONResponse({
            "done": True,
            "message": "No more pending leads in this campaign",
        })

    return JSONResponse({
        "done": False,
        "action_id": action["action_id"],
        "lead": {
            "id": action["lead_id"],
            "full_name": action["full_name"],
            "first_name": action["first_name"],
            "headline": action["headline"],
            "company": action["company"],
            "profile_url": action["profile_url"],
        },
        "sender_name": dict(campaign)["sender_name"],
        "company_page_name": dict(campaign)["company_page_name"],
    })


@router.post("/campaigns/{campaign_id}/action/{action_id}/complete")
async def ext_action_complete(
    request: Request, campaign_id: int, action_id: int
):
    """Mark an action as completed by the extension."""
    db = await get_lp_db()
    body = await request.json()
    success = body.get("success", False)
    comment_text = body.get("comment_text", "")
    error_message = body.get("error", "")

    now = datetime.now(IST).isoformat()
    new_status = "done" if success else "failed"

    # Update action queue
    await db.execute(
        """
        UPDATE action_queue
        SET status = ?, comment_text = ?, error_message = ?, completed_at = ?
        WHERE id = ? AND campaign_id = ?
        """,
        (new_status, comment_text, error_message, now, action_id, campaign_id),
    )

    # Update campaign counters
    stat_col = "successful" if success else "failed"
    await db.execute(
        f"UPDATE campaigns SET processed = processed + 1, {stat_col} = {stat_col} + 1 WHERE id = ?",
        (campaign_id,),
    )

    # Update lead status if successful
    if success:
        cursor = await db.execute(
            "SELECT lead_id FROM action_queue WHERE id = ?", (action_id,)
        )
        row = await cursor.fetchone()
        if row:
            await db.execute(
                "UPDATE custom_list_leads SET is_commented = 1, last_action_at = ? WHERE id = ?",
                (now, row["lead_id"]),
            )

    # Log activity
    cursor = await db.execute(
        """
        SELECT aq.action_type, cll.full_name, cll.profile_url,
               s.name AS sender_name
        FROM action_queue aq
        JOIN custom_list_leads cll ON aq.lead_id = cll.id
        LEFT JOIN senders s ON aq.sender_id = s.id
        WHERE aq.id = ?
        """,
        (action_id,),
    )
    info = await cursor.fetchone()
    if info:
        info = dict(info)
        await db.execute(
            """
            INSERT INTO activity_log
                (action_type, sender_id, sender_name, lead_name, lead_url,
                 campaign_id, status, details)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                info.get("action_type", "comment"),
                body.get("sender_id"),
                info.get("sender_name", ""),
                info.get("full_name", ""),
                info.get("profile_url", ""),
                campaign_id,
                new_status,
                f"via extension | {comment_text[:60]}" if comment_text else error_message,
            ),
        )

    await db.commit()

    # Check if campaign is now complete
    cursor = await db.execute(
        "SELECT COUNT(*) AS cnt FROM action_queue WHERE campaign_id = ? AND status = 'pending'",
        (campaign_id,),
    )
    row = await cursor.fetchone()
    remaining = row["cnt"] if row else 0

    if remaining == 0:
        await db.execute(
            "UPDATE campaigns SET status = 'completed', completed_at = ? WHERE id = ?",
            (now, campaign_id),
        )
        await db.commit()

    return JSONResponse({
        "ok": True,
        "remaining": remaining,
    })


@router.get("/settings")
async def ext_settings(request: Request):
    """Return settings needed by the extension (VPS URL, delays, etc)."""
    db = await get_lp_db()

    # Get VPS config from settings table or env
    from app.config import (
        VPS_AI_URL, VPS_API_KEY,
        COMMENT_MIN_DELAY, COMMENT_MAX_DELAY,
        WORK_HOUR_START, WORK_HOUR_END,
    )

    # Derive VPS base URL from VPS_AI_URL
    vps_base = ""
    if VPS_AI_URL:
        # VPS_AI_URL is like https://IP:8443/api/suggest-comment
        # We need https://IP:8443
        parts = VPS_AI_URL.split("/api/")
        if parts:
            vps_base = parts[0]

    return JSONResponse({
        "vps_base_url": vps_base,
        "vps_api_key": VPS_API_KEY,
        "comment_min_delay": COMMENT_MIN_DELAY,
        "comment_max_delay": COMMENT_MAX_DELAY,
        "work_hour_start": WORK_HOUR_START,
        "work_hour_end": WORK_HOUR_END,
    })
