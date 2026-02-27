"""LinkedPilot v2 — Settings page router."""

from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.config import TEMPLATES_DIR
from app.database import get_lp_db
from app.services.ai_comment import check_vps_health

router = APIRouter()
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


@router.get("/api/vps-health-fragment", response_class=HTMLResponse)
async def vps_health_fragment(request: Request):
    """Return VPS status as an HTML fragment for HTMX lazy-load."""
    vps_status = await check_vps_health()
    is_healthy = vps_status.get("status") == "healthy"
    latency = vps_status.get("latency_ms", -1)

    color = "var(--lp-green)" if is_healthy else "var(--lp-red)"
    label = "Connected" if is_healthy else "Disconnected"

    html = f"""
    <div style="display: flex; align-items: center; gap: 0.75rem; margin-bottom: 1rem;">
        <span style="display: inline-block; width: 12px; height: 12px; border-radius: 50%; background: {color};"></span>
        <strong>{label}</strong>
    </div>
    <div style="font-size: 0.85rem; margin-bottom: 1rem;">
        <p style="margin-bottom: 0.25rem;"><strong>Status:</strong> {vps_status.get('status', 'unknown')}</p>
        {"<p style='margin-bottom: 0.25rem;'><strong>Latency:</strong> " + str(latency) + "ms</p>" if latency >= 0 else ""}
    </div>
    """
    return HTMLResponse(html)


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    """Show all settings and VPS connection status."""
    db = await get_lp_db()

    # Load all settings as key/value pairs
    cursor = await db.execute("SELECT key, value FROM settings ORDER BY key")
    rows = await cursor.fetchall()
    settings = {row["key"]: row["value"] for row in rows}

    # Return page immediately — VPS status will be fetched via HTMX
    vps_status = {"status": "checking", "latency_ms": -1}

    return templates.TemplateResponse("settings.html", {
        "request": request,
        "settings": settings,
        "vps_status": vps_status,
        "active_page": "settings",
    })


@router.get("/api/vps-health")
async def vps_health_api(request: Request):
    """Return VPS health as JSON — called async by frontend after page load."""
    vps_status = await check_vps_health()
    return vps_status


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
