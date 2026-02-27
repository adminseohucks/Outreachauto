"""LinkedPilot v2 — FastAPI application entry point."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.config import TEMPLATES_DIR, STATIC_DIR, EXPORTS_DIR
from app.database import get_lp_db, close_databases
from app.services.openoutreach_reader import ensure_mock_crm_db
from app.automation.browser import browser_manager


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    await get_lp_db()
    await ensure_mock_crm_db()
    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)

    # Resume any campaigns that were active before server restart
    from app.services.scheduler import scheduler
    await scheduler.resume_active_campaigns()

    yield
    # Shutdown
    await browser_manager.close_all()
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
