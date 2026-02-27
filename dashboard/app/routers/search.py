"""LinkedPilot v2 — LinkedIn Search router.

Search LinkedIn people using sender's authenticated browser session.
Supports search with filters: location, network degree, company size.
"""

import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from app.config import TEMPLATES_DIR
from app.database import get_lp_db
from app.automation.browser import browser_manager

router = APIRouter()
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
logger = logging.getLogger(__name__)

# In-memory cache of last search results (per session, simple approach)
_last_search_results: list[dict] = []


def _build_template_context(request, senders, lists, **extra):
    """Build the base template context for search page."""
    ctx = {
        "request": request,
        "senders": senders,
        "lists": lists,
        "results": [],
        "search_query": "",
        "search_location": "",
        "search_network": "",
        "search_company_size": "",
        "total_results": 0,
        "active_page": "search",
    }
    ctx.update(extra)
    return ctx


async def _get_senders_and_lists():
    """Fetch senders with browser status and lists."""
    db = await get_lp_db()

    cursor = await db.execute(
        "SELECT id, name FROM senders WHERE status IN ('active', 'paused') ORDER BY name"
    )
    senders = [dict(r) for r in await cursor.fetchall()]
    for s in senders:
        s["browser_open"] = browser_manager.is_open(s["id"])

    cursor = await db.execute("SELECT id, name FROM custom_lists ORDER BY name")
    lists = [dict(r) for r in await cursor.fetchall()]

    return senders, lists


@router.get("/search", response_class=HTMLResponse)
async def search_page(request: Request):
    """Show the LinkedIn search page."""
    senders, lists = await _get_senders_and_lists()
    return templates.TemplateResponse(
        "search.html",
        _build_template_context(request, senders, lists),
    )


@router.post("/search/run")
async def run_search(
    request: Request,
    keywords: str = Form(...),
    sender_id: int = Form(...),
    max_results: int = Form(100),
    location: str = Form(""),
    network: str = Form(""),
    company_size: str = Form(""),
):
    """Execute a LinkedIn people search using the sender's browser."""
    global _last_search_results
    from app.automation.linkedin_search import search_people

    senders, lists = await _get_senders_and_lists()
    extra = {
        "search_query": keywords,
        "search_location": location,
        "search_network": network,
        "search_company_size": company_size,
    }

    # Validate sender has browser open
    if not browser_manager.is_open(sender_id):
        return templates.TemplateResponse("search.html", _build_template_context(
            request, senders, lists,
            error="Browser is not open for this sender. Go to Senders page and click 'Open Chrome & Login' first.",
            **extra,
        ))

    # Cap at 999
    max_results = min(max_results, 999)

    # Parse filters
    network_filter = None
    if network:
        network_filter = [network]  # e.g. ["F"], ["S"], ["O"]

    company_size_filter = None
    if company_size:
        company_size_filter = [company_size]

    try:
        page = await browser_manager.get_page(sender_id)
        results, search_error = await search_people(
            page,
            keywords,
            max_results=max_results,
            location=location,
            network=network_filter,
            company_size=company_size_filter,
        )
    except Exception as exc:
        logger.error("Search failed: %s", exc)
        results = []
        search_error = str(exc)

    # Cache results
    _last_search_results = results

    error = None
    if search_error:
        if results:
            error = f"Partial results. Warning: {search_error}"
        else:
            error = f"Search failed: {search_error}"

    return templates.TemplateResponse("search.html", _build_template_context(
        request, senders, lists,
        results=results,
        total_results=len(results),
        error=error,
        **extra,
    ))


@router.post("/search/add-to-list")
async def add_search_results_to_list(
    request: Request,
    list_name: str = Form(""),
    new_list_name: str = Form(""),
    selected_indices: str = Form(""),
):
    """Add selected search results to a list."""
    global _last_search_results
    db = await get_lp_db()

    # Determine target list
    target = new_list_name.strip() if new_list_name.strip() else list_name.strip()
    if not target:
        return RedirectResponse(url="/search", status_code=303)

    # Get or create list
    cursor = await db.execute("SELECT id FROM custom_lists WHERE name = ?", (target,))
    row = await cursor.fetchone()
    if row:
        list_id = row["id"]
    else:
        cursor = await db.execute(
            "INSERT INTO custom_lists (name, source) VALUES (?, 'search')",
            (target,),
        )
        await db.commit()
        list_id = cursor.lastrowid

    # Parse selected indices
    indices = [int(i.strip()) for i in selected_indices.split(",") if i.strip().isdigit()]
    added = 0
    now = datetime.utcnow().isoformat()

    for idx in indices:
        if idx < 0 or idx >= len(_last_search_results):
            continue
        lead = _last_search_results[idx]
        profile_url = lead.get("profile_url", "")
        if not profile_url:
            continue

        # Skip duplicate
        cursor = await db.execute(
            "SELECT id FROM custom_list_leads WHERE list_id = ? AND profile_url = ?",
            (list_id, profile_url),
        )
        if await cursor.fetchone():
            continue

        await db.execute(
            """
            INSERT INTO custom_list_leads
                (list_id, full_name, first_name, headline, company,
                 location, profile_url, source, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'search', ?)
            """,
            (
                list_id,
                lead.get("full_name", ""),
                lead.get("first_name", ""),
                lead.get("headline", ""),
                lead.get("company", ""),
                lead.get("location", ""),
                profile_url,
                now,
            ),
        )
        added += 1

    # Update lead count
    cursor = await db.execute(
        "SELECT COUNT(*) AS cnt FROM custom_list_leads WHERE list_id = ?", (list_id,)
    )
    count_row = await cursor.fetchone()
    await db.execute(
        "UPDATE custom_lists SET lead_count = ? WHERE id = ?",
        (count_row["cnt"] if count_row else 0, list_id),
    )
    await db.commit()

    logger.info("Added %d search results to list '%s' (id=%d)", added, target, list_id)

    return JSONResponse({
        "success": True,
        "added": added,
        "list_name": target,
    })


@router.post("/search/enrich")
async def enrich_lead(
    request: Request,
    lead_id: int = Form(...),
    sender_id: int = Form(...),
):
    """Enrich a single lead's profile using LinkedIn Voyager API."""
    from app.automation.linkedin_search import enrich_profile

    db = await get_lp_db()

    # Get the lead
    cursor = await db.execute(
        "SELECT * FROM custom_list_leads WHERE id = ?", (lead_id,)
    )
    lead = await cursor.fetchone()
    if not lead:
        return JSONResponse({"success": False, "error": "Lead not found"})

    lead = dict(lead)
    profile_url = lead.get("profile_url", "")
    if not profile_url:
        return JSONResponse({"success": False, "error": "No profile URL"})

    # Check sender's browser is open
    if not browser_manager.is_open(sender_id):
        return JSONResponse({"success": False, "error": "Browser not open for sender"})

    try:
        page = await browser_manager.get_page(sender_id)
        enriched = await enrich_profile(page, profile_url)
    except Exception as exc:
        return JSONResponse({"success": False, "error": str(exc)})

    if "error" in enriched:
        return JSONResponse({"success": False, "error": enriched["error"]})

    # Update lead with enriched data
    updates = []
    params = []
    for field in ["full_name", "first_name", "headline", "company", "location"]:
        if enriched.get(field):
            updates.append(f"{field} = ?")
            params.append(enriched[field])

    if updates:
        params.append(lead_id)
        await db.execute(
            f"UPDATE custom_list_leads SET {', '.join(updates)} WHERE id = ?",
            params,
        )
        await db.commit()

    return JSONResponse({
        "success": True,
        "enriched": enriched,
    })
