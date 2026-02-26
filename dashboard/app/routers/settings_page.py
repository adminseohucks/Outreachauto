"""LinkedPilot v2 — Settings page router."""

from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.config import TEMPLATES_DIR
from app.database import get_lp_db
from app.services.ai_comment import check_vps_health

router = APIRouter()
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    """Show all settings and VPS connection status."""
    db = await get_lp_db()

    # Load all settings as key/value pairs
    cursor = await db.execute("SELECT key, value FROM settings ORDER BY key")
    rows = await cursor.fetchall()
    settings = {row["key"]: row["value"] for row in rows}

    # Check VPS health
    vps_status = await check_vps_health()

    return templates.TemplateResponse("settings.html", {
        "request": request,
        "settings": settings,
        "vps_status": vps_status,
        "active_page": "settings",
    })


@router.post("/settings/save")
async def save_settings(request: Request):
    """Save settings from form key/value pairs."""
    db = await get_lp_db()
    form_data = await request.form()

    for key, value in form_data.items():
        if key.startswith("_"):
            # Skip internal form fields (e.g., CSRF tokens)
            continue
        await db.execute(
            """
            INSERT INTO settings (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (key, str(value)),
        )
    await db.commit()

    return RedirectResponse(url="/settings", status_code=303)


@router.post("/settings/test-vps")
async def test_vps(request: Request):
    """Test VPS connection and redirect back to settings with result."""
    db = await get_lp_db()
    vps_result = await check_vps_health()

    # Store last test result in settings
    await db.execute(
        """
        INSERT INTO settings (key, value) VALUES ('vps_last_test_status', ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (vps_result["status"],),
    )
    await db.execute(
        """
        INSERT INTO settings (key, value) VALUES ('vps_last_test_latency', ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (str(vps_result["latency_ms"]),),
    )
    await db.commit()

    return RedirectResponse(url="/settings", status_code=303)
