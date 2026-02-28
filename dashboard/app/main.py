"""LinkedPilot v2 — FastAPI application entry point."""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import TEMPLATES_DIR, STATIC_DIR, EXPORTS_DIR
from app.database import get_lp_db, close_databases
from app.services.openoutreach_reader import ensure_mock_crm_db
from app.automation.browser import browser_manager, extension_chrome

# Configure logging so app logs (logger.info, logger.error, etc.) show in CMD
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)

_logger = logging.getLogger(__name__)


async def _auto_open_sender_browsers():
    """Auto-open Playwright browsers + extension Chrome for all active senders."""
    db = await get_lp_db()
    cursor = await db.execute(
        "SELECT id, name, browser_profile FROM senders "
        "WHERE status IN ('active', 'paused') ORDER BY id"
    )
    senders = await cursor.fetchall()
    if not senders:
        return

    print(f"\n  [Startup] Auto-opening browsers for {len(senders)} sender(s)...")

    for sender in senders:
        sid = sender["id"]
        sname = sender["name"]

        # --- Automation browser (Playwright) ---
        try:
            await browser_manager.open_for_login(sid, sender["browser_profile"])
            status = await browser_manager.check_login_status(sid)
            if status.get("logged_in"):
                print(f"  [Startup] Sender {sid} ({sname}): browser open, logged in")
            else:
                print(f"  [Startup] Sender {sid} ({sname}): browser open, NOT logged in — please log in manually")
        except Exception as e:
            print(f"  [Startup] Sender {sid} ({sname}): browser FAILED — {e}")

        # --- Extension Chrome (regular Chrome subprocess) ---
        try:
            ok = extension_chrome.open(sid)
            if ok:
                print(f"  [Startup] Sender {sid} ({sname}): extension Chrome opened")
            else:
                print(f"  [Startup] Sender {sid} ({sname}): extension Chrome FAILED (Chrome not found?)")
        except Exception as e:
            print(f"  [Startup] Sender {sid} ({sname}): extension Chrome FAILED — {e}")

    print(f"  [Startup] All browsers launched.\n")


async def _browser_health_loop():
    """Background task: check browser health every 45s, auto-reconnect dead ones."""
    logger = logging.getLogger("app.browser_health")
    while True:
        await asyncio.sleep(45)
        try:
            db = await get_lp_db()
            cursor = await db.execute(
                "SELECT id, name, browser_profile FROM senders "
                "WHERE status IN ('active', 'paused')"
            )
            senders = await cursor.fetchall()
            for s in senders:
                sid = s["id"]
                if not browser_manager.is_open(sid):
                    continue
                alive = await browser_manager.health_check(sid)
                if not alive:
                    logger.warning(
                        "Sender %s (%s) browser died — reconnecting...",
                        sid, s["name"],
                    )
                    try:
                        await browser_manager.open_for_login(
                            sid, s["browser_profile"]
                        )
                        logger.info("Sender %s reconnected", sid)
                    except Exception as e:
                        logger.error("Sender %s reconnect failed: %s", sid, e)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error("Health check error: %s", e)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    await get_lp_db()
    await ensure_mock_crm_db()
    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)

    # Resume any campaigns that were active before server restart
    from app.services.scheduler import scheduler
    await scheduler.resume_active_campaigns()

    # Auto-open all sender browsers (Playwright + Extension Chrome)
    await _auto_open_sender_browsers()

    # Start background health monitor (keeps connections alive)
    health_task = asyncio.create_task(_browser_health_loop())

    yield

    # Shutdown
    health_task.cancel()
    try:
        await health_task
    except asyncio.CancelledError:
        pass
    await browser_manager.close_all()
    extension_chrome.close_all()
    await close_databases()


app = FastAPI(title="LinkedPilot v2", lifespan=lifespan)

# CORS — allow Chrome extension to call /api/ext/* endpoints
app.add_middleware(
    CORSMiddleware,
    allow_origins=["chrome-extension://*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# Static files & templates
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# --- Import and register routers ---
from app.routers import dashboard, leads, lists, campaigns, comments, settings_page, logs, senders, api, ext_api, search

app.include_router(dashboard.router)
app.include_router(leads.router)
app.include_router(lists.router)
app.include_router(campaigns.router)
app.include_router(comments.router)
app.include_router(settings_page.router)
app.include_router(logs.router)
app.include_router(senders.router)
app.include_router(search.router)
app.include_router(api.router)
app.include_router(ext_api.router)
