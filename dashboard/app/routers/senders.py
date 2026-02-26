"""LinkedPilot v2 — Sender management router."""

from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from app.config import TEMPLATES_DIR
from app.database import get_lp_db
from app.automation.browser import browser_manager

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

    # Add browser status and company pages to each sender
    for sender in senders:
        sender["browser_open"] = browser_manager.is_open(sender["id"])
        cursor = await db.execute(
            "SELECT * FROM company_pages WHERE sender_id = ? ORDER BY page_name",
            (sender["id"],),
        )
        sender["company_pages"] = [dict(row) for row in await cursor.fetchall()]

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


@router.post("/senders/login-all")
async def login_all_senders(request: Request):
    """Open Chrome browsers for all active senders."""
    db = await get_lp_db()
    cursor = await db.execute(
        "SELECT id, browser_profile FROM senders WHERE status IN ('active', 'paused') ORDER BY id"
    )
    senders = await cursor.fetchall()
    for sender in senders:
        sid = sender["id"]
        if not browser_manager.is_open(sid):
            try:
                await browser_manager.open_for_login(sid, sender["browser_profile"])
            except Exception:
                pass
    return RedirectResponse(url="/senders", status_code=303)


@router.post("/senders/close-all")
async def close_all_browsers(request: Request):
    """Close all open sender browsers (keeps Playwright running)."""
    await browser_manager.close_all_contexts()
    return RedirectResponse(url="/senders", status_code=303)


@router.post("/senders/{sender_id}/login")
async def login_sender(request: Request, sender_id: int):
    """Open a Chrome browser for this sender to manually log into LinkedIn.

    The browser opens with the sender's dedicated profile directory.
    The user logs in manually. Cookies are saved automatically.
    """
    db = await get_lp_db()
    cursor = await db.execute(
        "SELECT browser_profile FROM senders WHERE id = ?", (sender_id,)
    )
    sender = await cursor.fetchone()
    if not sender:
        return RedirectResponse(url="/senders", status_code=303)

    profile_dir = sender["browser_profile"]
    try:
        await browser_manager.open_for_login(sender_id, profile_dir)
    except Exception as e:
        # Log the error but don't crash
        import logging
        logging.getLogger(__name__).error("Failed to open browser for sender %s: %s", sender_id, e)

    return RedirectResponse(url="/senders", status_code=303)


@router.post("/senders/{sender_id}/check-login")
async def check_login(request: Request, sender_id: int):
    """Check if the sender is logged into LinkedIn. Returns JSON."""
    result = await browser_manager.check_login_status(sender_id)
    return JSONResponse(content=result)


@router.post("/senders/{sender_id}/close-browser")
async def close_browser(request: Request, sender_id: int):
    """Close the browser for this sender."""
    await browser_manager.close_context(sender_id)
    return RedirectResponse(url="/senders", status_code=303)


# -----------------------------------------------------------------------
# Company Pages — manage LinkedIn company pages linked to a sender
# -----------------------------------------------------------------------

@router.post("/senders/{sender_id}/pages/add")
async def add_company_page(
    request: Request,
    sender_id: int,
    page_name: str = Form(...),
    page_url: str = Form(...),
):
    """Add a company page to a sender account."""
    db = await get_lp_db()
    page_url = page_url.rstrip("/")
    try:
        await db.execute(
            "INSERT INTO company_pages (sender_id, page_name, page_url) VALUES (?, ?, ?)",
            (sender_id, page_name, page_url),
        )
        await db.commit()
    except Exception:
        pass  # duplicate or FK error
    return RedirectResponse(url="/senders", status_code=303)


@router.post("/senders/{sender_id}/pages/{page_id}/remove")
async def remove_company_page(request: Request, sender_id: int, page_id: int):
    """Remove a company page from a sender."""
    db = await get_lp_db()
    await db.execute(
        "DELETE FROM company_pages WHERE id = ? AND sender_id = ?",
        (page_id, sender_id),
    )
    await db.commit()
    return RedirectResponse(url="/senders", status_code=303)


@router.post("/senders/{sender_id}/pages/{page_id}/toggle")
async def toggle_company_page(request: Request, sender_id: int, page_id: int):
    """Toggle a company page active/inactive."""
    db = await get_lp_db()
    await db.execute(
        """
        UPDATE company_pages SET is_active = CASE WHEN is_active = 1 THEN 0 ELSE 1 END
        WHERE id = ? AND sender_id = ?
        """,
        (page_id, sender_id),
    )
    await db.commit()
    return RedirectResponse(url="/senders", status_code=303)
