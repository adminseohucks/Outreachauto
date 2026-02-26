"""LinkedPilot v2 — Campaigns router."""

import random
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.config import TEMPLATES_DIR
from app.database import get_lp_db

router = APIRouter()
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


@router.get("/campaigns", response_class=HTMLResponse)
async def campaigns_page(request: Request):
    """Show all campaigns with progress stats."""
    db = await get_lp_db()
    cursor = await db.execute(
        """
        SELECT c.*, cl.name AS list_name, s.name AS sender_name,
               cp.page_name AS company_page_name
        FROM campaigns c
        LEFT JOIN custom_lists cl ON c.list_id = cl.id
        LEFT JOIN senders s ON c.sender_id = s.id
        LEFT JOIN company_pages cp ON c.company_page_id = cp.id
        ORDER BY c.created_at DESC
        """
    )
    campaigns = [dict(row) for row in await cursor.fetchall()]

    # Get lists and senders for creation form
    cursor = await db.execute(
        "SELECT id, name FROM custom_lists ORDER BY name"
    )
    lists = [dict(row) for row in await cursor.fetchall()]

    cursor = await db.execute(
        "SELECT id, name FROM senders WHERE status = 'active' ORDER BY name"
    )
    senders = [dict(row) for row in await cursor.fetchall()]

    # Get company pages grouped by sender for the "Act As" dropdown
    cursor = await db.execute(
        """
        SELECT cp.id, cp.sender_id, cp.page_name, s.name AS sender_name
        FROM company_pages cp
        JOIN senders s ON cp.sender_id = s.id
        WHERE cp.is_active = 1 AND s.status = 'active'
        ORDER BY s.name, cp.page_name
        """
    )
    company_pages = [dict(row) for row in await cursor.fetchall()]

    # Get connection notes for connect campaign form
    cursor = await db.execute(
        "SELECT * FROM connection_notes ORDER BY id"
    )
    connection_notes = [dict(row) for row in await cursor.fetchall()]

    return templates.TemplateResponse("campaigns.html", {
        "request": request,
        "campaigns": campaigns,
        "lists": lists,
        "senders": senders,
        "company_pages": company_pages,
        "connection_notes": connection_notes,
        "active_page": "campaigns",
    })


@router.post("/campaigns/create")
async def create_campaign(
    request: Request,
    name: str = Form(...),
    list_id: int = Form(...),
    sender_id: int = Form(...),
    campaign_type: str = Form(...),
    company_page_id: int = Form(0),
):
    """Create a new campaign (draft status)."""
    db = await get_lp_db()

    # company_page_id=0 means personal profile (no company page)
    page_id = company_page_id if company_page_id > 0 else None

    # Get total leads in the list
    cursor = await db.execute(
        "SELECT COUNT(*) AS cnt FROM custom_list_leads WHERE list_id = ?",
        (list_id,),
    )
    row = await cursor.fetchone()
    total_leads = row["cnt"] if row else 0

    cursor = await db.execute(
        """
        INSERT INTO campaigns (name, list_id, sender_id, company_page_id, campaign_type, total_leads)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (name, list_id, sender_id, page_id, campaign_type, total_leads),
    )
    await db.commit()
    campaign_id = cursor.lastrowid

    # For connect campaigns, fetch active connection notes
    connection_notes = []
    if campaign_type == "connect":
        cursor = await db.execute(
            "SELECT id, text FROM connection_notes WHERE is_active = 1"
        )
        connection_notes = [dict(r) for r in await cursor.fetchall()]

    # Pre-populate action queue from list leads
    cursor = await db.execute(
        "SELECT id, first_name, full_name FROM custom_list_leads WHERE list_id = ?",
        (list_id,),
    )
    lead_rows = await cursor.fetchall()
    for lead_row in lead_rows:
        lead_id = lead_row["id"]
        connect_note = None

        # For connect campaigns, pick random note and personalize with name
        if campaign_type == "connect" and connection_notes:
            note_template = random.choice(connection_notes)
            first_name = lead_row["first_name"] or lead_row["full_name"].split()[0]
            connect_note = note_template["text"].replace("{first_name}", first_name)

        await db.execute(
            """
            INSERT INTO action_queue
                (campaign_id, lead_id, sender_id, company_page_id, action_type, connect_note, status)
            VALUES (?, ?, ?, ?, ?, ?, 'pending')
            """,
            (campaign_id, lead_id, sender_id, page_id, campaign_type, connect_note),
        )
    await db.commit()

    return RedirectResponse(url="/campaigns", status_code=303)


@router.post("/campaigns/{campaign_id}/start")
async def start_campaign(request: Request, campaign_id: int):
    """Start (activate) a campaign."""
    from app.services.scheduler import scheduler

    db = await get_lp_db()
    now = datetime.utcnow().isoformat()
    await db.execute(
        "UPDATE campaigns SET status = 'active', started_at = ? WHERE id = ? AND status IN ('draft', 'paused')",
        (now, campaign_id),
    )
    await db.commit()

    # Notify the scheduler
    await scheduler.start_campaign(campaign_id)

    return RedirectResponse(url=f"/campaigns/{campaign_id}", status_code=303)


@router.post("/campaigns/{campaign_id}/pause")
async def pause_campaign(request: Request, campaign_id: int):
    """Pause an active campaign."""
    from app.services.scheduler import scheduler

    db = await get_lp_db()
    await db.execute(
        "UPDATE campaigns SET status = 'paused' WHERE id = ? AND status = 'active'",
        (campaign_id,),
    )
    await db.commit()

    await scheduler.pause_campaign(campaign_id)

    return RedirectResponse(url=f"/campaigns/{campaign_id}", status_code=303)


@router.post("/campaigns/{campaign_id}/cancel")
async def cancel_campaign(request: Request, campaign_id: int):
    """Cancel a campaign and mark remaining actions as skipped."""
    from app.services.scheduler import scheduler

    db = await get_lp_db()
    now = datetime.utcnow().isoformat()

    await db.execute(
        "UPDATE campaigns SET status = 'cancelled', completed_at = ? WHERE id = ? AND status IN ('draft', 'active', 'paused')",
        (now, campaign_id),
    )
    # Mark remaining pending/scheduled actions as skipped
    await db.execute(
        "UPDATE action_queue SET status = 'skipped' WHERE campaign_id = ? AND status IN ('pending', 'scheduled')",
        (campaign_id,),
    )
    await db.commit()

    await scheduler.cancel_campaign(campaign_id)

    return RedirectResponse(url=f"/campaigns/{campaign_id}", status_code=303)


@router.get("/campaigns/{campaign_id}", response_class=HTMLResponse)
async def campaign_detail(request: Request, campaign_id: int):
    """Show campaign detail with action queue."""
    db = await get_lp_db()

    # Campaign info
    cursor = await db.execute(
        """
        SELECT c.*, cl.name AS list_name, s.name AS sender_name
        FROM campaigns c
        LEFT JOIN custom_lists cl ON c.list_id = cl.id
        LEFT JOIN senders s ON c.sender_id = s.id
        WHERE c.id = ?
        """,
        (campaign_id,),
    )
    campaign = await cursor.fetchone()
    if not campaign:
        return RedirectResponse(url="/campaigns", status_code=303)
    campaign = dict(campaign)

    # Action queue — last 10 actions (most recent first)
    cursor = await db.execute(
        """
        SELECT aq.*, cll.full_name AS lead_name, cll.profile_url AS lead_url
        FROM action_queue aq
        LEFT JOIN custom_list_leads cll ON aq.lead_id = cll.id
        WHERE aq.campaign_id = ?
        ORDER BY aq.id DESC
        LIMIT 10
        """,
        (campaign_id,),
    )
    actions = [dict(row) for row in await cursor.fetchall()]

    # Status summary counts
    cursor = await db.execute(
        """
        SELECT status, COUNT(*) as cnt
        FROM action_queue WHERE campaign_id = ?
        GROUP BY status
        """,
        (campaign_id,),
    )
    status_counts = {row["status"]: row["cnt"] for row in await cursor.fetchall()}

    return templates.TemplateResponse("campaign_detail.html", {
        "request": request,
        "campaign": campaign,
        "actions": actions,
        "status_counts": status_counts,
        "active_page": "campaigns",
    })


# -----------------------------------------------------------------------
# Connection Notes management
# -----------------------------------------------------------------------

@router.post("/connection-notes/add")
async def add_connection_note(request: Request, text: str = Form(...)):
    """Add a new connection note template."""
    db = await get_lp_db()
    await db.execute("INSERT INTO connection_notes (text) VALUES (?)", (text,))
    await db.commit()
    return RedirectResponse(url="/campaigns", status_code=303)


@router.post("/connection-notes/{note_id}/delete")
async def delete_connection_note(request: Request, note_id: int):
    """Delete a connection note."""
    db = await get_lp_db()
    await db.execute("DELETE FROM connection_notes WHERE id = ?", (note_id,))
    await db.commit()
    return RedirectResponse(url="/campaigns", status_code=303)


@router.post("/connection-notes/{note_id}/toggle")
async def toggle_connection_note(request: Request, note_id: int):
    """Toggle a connection note active/inactive."""
    db = await get_lp_db()
    await db.execute(
        "UPDATE connection_notes SET is_active = CASE WHEN is_active = 1 THEN 0 ELSE 1 END WHERE id = ?",
        (note_id,),
    )
    await db.commit()
    return RedirectResponse(url="/campaigns", status_code=303)
