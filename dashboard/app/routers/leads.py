"""LinkedPilot v2 — Lead browser router (reads from OpenOutreach crm.db)."""

from datetime import datetime

from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.templating import Jinja2Templates

from app.config import TEMPLATES_DIR
from app.database import get_lp_db
from app.services.openoutreach_reader import get_leads
from app.services.csv_handler import export_leads_csv

router = APIRouter()
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


@router.get("/leads", response_class=HTMLResponse)
async def leads_page(
    request: Request,
    title: str = "",
    company: str = "",
    location: str = "",
    status: str = "",
    date_from: str = "",
    date_to: str = "",
    search: str = "",
    page: int = 1,
):
    """Lead browser with filtering and pagination."""
    filters = {}
    if title:
        filters["title"] = title
    if company:
        filters["company"] = company
    if location:
        filters["location"] = location
    if status:
        filters["status"] = status
    if date_from:
        filters["date_from"] = date_from
    if date_to:
        filters["date_to"] = date_to
    if search:
        filters["search"] = search

    result = await get_leads(filters=filters, page=page, per_page=50)

    # Get available lists for "Add to List" dropdown
    db = await get_lp_db()
    cursor = await db.execute("SELECT id, name FROM custom_lists ORDER BY name")
    lists = [dict(r) for r in await cursor.fetchall()]

    return templates.TemplateResponse("leads.html", {
        "request": request,
        "leads": result["leads"],
        "total": result["total"],
        "page": result["page"],
        "pages": result["pages"],
        "per_page": result["per_page"],
        # Sticky filter values
        "filters": {
            "title": title,
            "company": company,
            "location": location,
            "status": status,
            "date_from": date_from,
            "date_to": date_to,
            "search": search,
        },
        "lists": lists,
        "active_page": "leads",
    })


@router.post("/leads/export-csv")
async def export_csv(
    request: Request,
    title: str = Form(""),
    company: str = Form(""),
    location: str = Form(""),
    status: str = Form(""),
    date_from: str = Form(""),
    date_to: str = Form(""),
    search: str = Form(""),
):
    """Export filtered leads to CSV and return the file."""
    filters = {}
    if title:
        filters["title"] = title
    if company:
        filters["company"] = company
    if location:
        filters["location"] = location
    if status:
        filters["status"] = status
    if date_from:
        filters["date_from"] = date_from
    if date_to:
        filters["date_to"] = date_to
    if search:
        filters["search"] = search

    # Fetch all matching leads (no pagination limit for export)
    result = await get_leads(filters=filters, page=1, per_page=10000)
    leads = result["leads"]

    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    filename = f"leads_export_{timestamp}.csv"
    file_path = await export_leads_csv(leads, filename)

    return FileResponse(
        path=file_path,
        filename=filename,
        media_type="text/csv",
    )


@router.post("/leads/add-to-list")
async def add_leads_to_list(
    request: Request,
    list_name: str = Form(...),
    lead_ids: str = Form(""),
):
    """Add selected lead IDs from CRM to a named custom list."""
    from app.services.openoutreach_reader import get_lead_by_id

    db = await get_lp_db()

    # Ensure list exists or create it
    cursor = await db.execute(
        "SELECT id FROM custom_lists WHERE name = ?", (list_name,)
    )
    row = await cursor.fetchone()
    if row:
        list_id = row["id"]
    else:
        cursor = await db.execute(
            "INSERT INTO custom_lists (name, source) VALUES (?, 'openoutreach')",
            (list_name,),
        )
        await db.commit()
        list_id = cursor.lastrowid

    # Parse selected lead IDs
    ids = [int(lid.strip()) for lid in lead_ids.split(",") if lid.strip().isdigit()]
    added = 0

    for lead_id in ids:
        lead = await get_lead_by_id(lead_id)
        if not lead:
            continue

        # Skip duplicates
        cursor = await db.execute(
            "SELECT id FROM custom_list_leads WHERE list_id = ? AND profile_url = ?",
            (list_id, lead.get("profile_url", "")),
        )
        if await cursor.fetchone():
            continue

        await db.execute(
            """
            INSERT INTO custom_list_leads
                (list_id, full_name, first_name, headline, company,
                 location, profile_url, source, openoutreach_lead_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'openoutreach', ?)
            """,
            (
                list_id,
                lead.get("full_name", ""),
                lead.get("first_name", ""),
                lead.get("headline", ""),
                lead.get("company", ""),
                lead.get("location", ""),
                lead.get("profile_url", ""),
                lead_id,
            ),
        )
        added += 1

    # Update lead count
    cursor = await db.execute(
        "SELECT COUNT(*) AS cnt FROM custom_list_leads WHERE list_id = ?",
        (list_id,),
    )
    row = await cursor.fetchone()
    count = row["cnt"] if row else 0
    await db.execute(
        "UPDATE custom_lists SET lead_count = ? WHERE id = ?",
        (count, list_id),
    )
    await db.commit()

    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/leads", status_code=303)
