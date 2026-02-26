"""LinkedPilot v2 — Sender management router."""

from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.config import TEMPLATES_DIR
from app.database import get_lp_db

router = APIRouter()
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


@router.get("/senders", response_class=HTMLResponse)
async def senders_page(request: Request):
    """Show all senders with their status."""
    db = await get_lp_db()
    cursor = await db.execute(
        "SELECT * FROM senders ORDER BY created_at DESC"
    )
    senders = [dict(row) for row in await cursor.fetchall()]

    return templates.TemplateResponse("senders.html", {
        "request": request,
        "senders": senders,
        "active_page": "senders",
    })


@router.post("/senders/add")
async def add_sender(
    request: Request,
    name: str = Form(...),
    linkedin_email: str = Form(...),
    browser_profile: str = Form(...),
    profile_url: str = Form(""),
    daily_like_limit: int = Form(100),
    daily_comment_limit: int = Form(50),
    weekly_like_limit: int = Form(300),
    weekly_comment_limit: int = Form(200),
):
    """Add a new sender (LinkedIn account)."""
    db = await get_lp_db()
    await db.execute(
        """
        INSERT INTO senders
            (name, linkedin_email, browser_profile, profile_url,
             daily_like_limit, daily_comment_limit,
             weekly_like_limit, weekly_comment_limit)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            name, linkedin_email, browser_profile, profile_url,
            daily_like_limit, daily_comment_limit,
            weekly_like_limit, weekly_comment_limit,
        ),
    )
    await db.commit()
    return RedirectResponse(url="/senders", status_code=303)


@router.post("/senders/{sender_id}/edit")
async def edit_sender(
    request: Request,
    sender_id: int,
    name: str = Form(...),
    linkedin_email: str = Form(...),
    browser_profile: str = Form(...),
    profile_url: str = Form(""),
    daily_like_limit: int = Form(100),
    daily_comment_limit: int = Form(50),
    weekly_like_limit: int = Form(300),
    weekly_comment_limit: int = Form(200),
):
    """Edit an existing sender's details."""
    db = await get_lp_db()
    await db.execute(
        """
        UPDATE senders SET
            name = ?,
            linkedin_email = ?,
            browser_profile = ?,
            profile_url = ?,
            daily_like_limit = ?,
            daily_comment_limit = ?,
            weekly_like_limit = ?,
            weekly_comment_limit = ?
        WHERE id = ?
        """,
        (
            name, linkedin_email, browser_profile, profile_url,
            daily_like_limit, daily_comment_limit,
            weekly_like_limit, weekly_comment_limit,
            sender_id,
        ),
    )
    await db.commit()
    return RedirectResponse(url="/senders", status_code=303)


@router.post("/senders/{sender_id}/toggle")
async def toggle_sender(request: Request, sender_id: int):
    """Toggle a sender between active and paused status."""
    db = await get_lp_db()
    await db.execute(
        """
        UPDATE senders SET status = CASE
            WHEN status = 'active' THEN 'paused'
            WHEN status = 'paused' THEN 'active'
            ELSE status
        END
        WHERE id = ?
        """,
        (sender_id,),
    )
    await db.commit()
    return RedirectResponse(url="/senders", status_code=303)


@router.post("/senders/{sender_id}/disable")
async def disable_sender(request: Request, sender_id: int):
    """Disable a sender entirely."""
    db = await get_lp_db()
    await db.execute(
        "UPDATE senders SET status = 'disabled' WHERE id = ?",
        (sender_id,),
    )
    await db.commit()
    return RedirectResponse(url="/senders", status_code=303)
