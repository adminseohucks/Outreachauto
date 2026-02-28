"""LinkedPilot v2 — Lead management router.

Shows all leads from custom_list_leads (the real working data).
Supports: search, filter, manual add, CSV upload, delete, export, add-to-list.
"""

from datetime import datetime

from fastapi import APIRouter, Request, Form, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from app.config import TEMPLATES_DIR
from app.database import get_lp_db
from app.services.csv_handler import export_leads_csv, import_csv_to_list

router = APIRouter()
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


# -----------------------------------------------------------------------
# Lead browser — shows ALL leads from custom_list_leads
# -----------------------------------------------------------------------

@router.get("/leads", response_class=HTMLResponse)
async def leads_page(
    request: Request,
    search: str = "",
    company: str = "",
    location: str = "",
    title: str = "",
    list_id: int = 0,
    source: str = "",
    status: str = "",
    page: int = 1,
):
    """Lead browser with filtering and pagination — reads from custom_list_leads."""
    db = await get_lp_db()
    per_page = 50

    # Build WHERE clause
    conditions = []
    params = []

    if search:
        conditions.append(
            "(cll.full_name LIKE ? OR cll.headline LIKE ? OR cll.company LIKE ? OR cll.profile_url LIKE ?)"
        )
        s = f"%{search}%"
        params.extend([s, s, s, s])
    if company:
        conditions.append("cll.company LIKE ?")
        params.append(f"%{company}%")
    if location:
        conditions.append("cll.location LIKE ?")
        params.append(f"%{location}%")
    if title:
        conditions.append("cll.headline LIKE ?")
        params.append(f"%{title}%")
    if list_id:
        conditions.append("cll.list_id = ?")
        params.append(list_id)
    if source:
        conditions.append("cll.source = ?")
        params.append(source)
    if status:
        conditions.append("cll.status = ?")
        params.append(status)

    where = ""
    if conditions:
        where = "WHERE " + " AND ".join(conditions)

    # Total count
    cursor = await db.execute(
        f"SELECT COUNT(*) AS cnt FROM custom_list_leads cll {where}", params
    )
    row = await cursor.fetchone()
    total = row["cnt"] if row else 0
    total_pages = max(1, -(-total // per_page))

    # Paginated results with list name
    offset = (page - 1) * per_page
    cursor = await db.execute(
        f"""
        SELECT cll.*, cl.name AS list_name
        FROM custom_list_leads cll
        LEFT JOIN custom_lists cl ON cll.list_id = cl.id
        {where}
        ORDER BY cll.created_at DESC
        LIMIT ? OFFSET ?
        """,
        params + [per_page, offset],
    )
    leads = [dict(row) for row in await cursor.fetchall()]

    # Get all lists for filter dropdown and "Add to List"
    cursor = await db.execute("SELECT id, name FROM custom_lists ORDER BY name")
    lists = [dict(r) for r in await cursor.fetchall()]

    # Get distinct sources and statuses for filter dropdowns
    cursor = await db.execute(
        "SELECT DISTINCT source FROM custom_list_leads WHERE source IS NOT NULL ORDER BY source"
    )
    sources = [row["source"] for row in await cursor.fetchall()]

    cursor = await db.execute(
        "SELECT DISTINCT status FROM custom_list_leads WHERE status IS NOT NULL ORDER BY status"
    )
    statuses = [row["status"] for row in await cursor.fetchall()]

    return templates.TemplateResponse("leads.html", {
        "request": request,
        "leads": leads,
        "total": total,
        "page": page,
        "pages": total_pages,
        "per_page": per_page,
        "filters": {
            "search": search,
            "company": company,
            "location": location,
            "title": title,
            "list_id": list_id,
            "source": source,
            "status": status,
        },
        "lists": lists,
        "sources": sources,
        "statuses": statuses,
        "active_page": "leads",
    })


# -----------------------------------------------------------------------
# Manual lead add
# -----------------------------------------------------------------------

@router.post("/leads/add-manual")
async def add_manual_lead(
    request: Request,
    full_name: str = Form(...),
    profile_url: str = Form(...),
    list_name: str = Form(""),
    new_list_name: str = Form(""),
    first_name: str = Form(""),
    headline: str = Form(""),
    company: str = Form(""),
    location: str = Form(""),
):
    """Add a single lead manually."""
    db = await get_lp_db()

    # Determine target list
    target_list = new_list_name.strip() if new_list_name.strip() else list_name.strip()
    if not target_list:
        target_list = "Manual Leads"

    # Get or create list
    cursor = await db.execute(
        "SELECT id FROM custom_lists WHERE name = ?", (target_list,)
    )
    row = await cursor.fetchone()
    if row:
        list_id = row["id"]
    else:
        cursor = await db.execute(
            "INSERT INTO custom_lists (name, source) VALUES (?, 'manual')",
            (target_list,),
        )
        await db.commit()
        list_id = cursor.lastrowid

    # Check duplicate
    cursor = await db.execute(
        "SELECT id FROM custom_list_leads WHERE list_id = ? AND profile_url = ?",
        (list_id, profile_url),
    )
    if await cursor.fetchone():
        return RedirectResponse(url="/leads", status_code=303)

    # Derive first_name if not given
    if not first_name and full_name:
        first_name = full_name.split()[0]

    await db.execute(
        """
        INSERT INTO custom_list_leads
            (list_id, full_name, first_name, headline, company, location,
             profile_url, source, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'manual', ?)
        """,
        (list_id, full_name, first_name, headline, company, location,
         profile_url, datetime.utcnow().isoformat()),
    )

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

    return RedirectResponse(url="/leads", status_code=303)


# -----------------------------------------------------------------------
# CSV upload with new list creation
# -----------------------------------------------------------------------

@router.post("/leads/upload-csv")
async def upload_csv_leads(
    request: Request,
    file: UploadFile = File(...),
    list_name: str = Form(""),
    new_list_name: str = Form(""),
    column_mapping: str = Form(""),
):
    """Upload CSV and import leads — with option to create a new list."""
    import json
    db = await get_lp_db()

    # Determine target list
    target_list = new_list_name.strip() if new_list_name.strip() else list_name.strip()
    if not target_list:
        target_list = f"CSV Import {datetime.utcnow().strftime('%Y-%m-%d %H:%M')}"

    # Get or create list
    cursor = await db.execute(
        "SELECT id FROM custom_lists WHERE name = ?", (target_list,)
    )
    row = await cursor.fetchone()
    if row:
        list_id = row["id"]
    else:
        cursor = await db.execute(
            "INSERT INTO custom_lists (name, source) VALUES (?, 'csv_upload')",
            (target_list,),
        )
        await db.commit()
        list_id = cursor.lastrowid

    content = await file.read()

    # Parse column mapping
    mapping = None
    if column_mapping:
        try:
            mapping = json.loads(column_mapping)
        except json.JSONDecodeError:
            mapping = None

    result = await import_csv_to_list(content, list_id, column_mapping=mapping)

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

    return JSONResponse({
        "success": True,
        "imported": result["imported"],
        "skipped": result["skipped"],
        "errors": result["errors"],
        "list_name": target_list,
    })


# -----------------------------------------------------------------------
# Add selected leads to a list (move/copy between lists)
# -----------------------------------------------------------------------

@router.post("/leads/add-to-list")
async def add_leads_to_list(
    request: Request,
    list_name: str = Form(""),
    new_list_name: str = Form(""),
    lead_ids: str = Form(""),
):
    """Copy selected leads to another list (new or existing)."""
    db = await get_lp_db()

    # Determine target list
    target_list = new_list_name.strip() if new_list_name.strip() else list_name.strip()
    if not target_list:
        return RedirectResponse(url="/leads", status_code=303)

    # Get or create list
    cursor = await db.execute(
        "SELECT id FROM custom_lists WHERE name = ?", (target_list,)
    )
    row = await cursor.fetchone()
    if row:
        list_id = row["id"]
    else:
        cursor = await db.execute(
            "INSERT INTO custom_lists (name, source) VALUES (?, 'manual')",
            (target_list,),
        )
        await db.commit()
        list_id = cursor.lastrowid

    # Parse selected lead IDs (these are custom_list_leads IDs)
    ids = [int(lid.strip()) for lid in lead_ids.split(",") if lid.strip().isdigit()]
    added = 0

    for lead_id in ids:
        # Fetch lead details
        cursor = await db.execute(
            "SELECT * FROM custom_list_leads WHERE id = ?", (lead_id,)
        )
        lead = await cursor.fetchone()
        if not lead:
            continue
        lead = dict(lead)

        # Skip duplicates in target list
        cursor = await db.execute(
            "SELECT id FROM custom_list_leads WHERE list_id = ? AND profile_url = ?",
            (list_id, lead["profile_url"]),
        )
        if await cursor.fetchone():
            continue

        await db.execute(
            """
            INSERT INTO custom_list_leads
                (list_id, full_name, first_name, headline, company,
                 location, profile_url, source, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                list_id, lead["full_name"], lead.get("first_name", ""),
                lead.get("headline", ""), lead.get("company", ""),
                lead.get("location", ""), lead["profile_url"],
                lead.get("source", "manual"), datetime.utcnow().isoformat(),
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

    return RedirectResponse(url="/leads", status_code=303)


# -----------------------------------------------------------------------
# Delete leads
# -----------------------------------------------------------------------

@router.post("/leads/delete")
async def delete_leads(request: Request, lead_ids: str = Form("")):
    """Delete selected leads from custom_list_leads."""
    db = await get_lp_db()

    ids = [int(lid.strip()) for lid in lead_ids.split(",") if lid.strip().isdigit()]
    if not ids:
        return RedirectResponse(url="/leads", status_code=303)

    # Get affected list IDs for count update
    placeholders = ",".join("?" * len(ids))
    cursor = await db.execute(
        f"SELECT DISTINCT list_id FROM custom_list_leads WHERE id IN ({placeholders})",
        ids,
    )
    affected_lists = [row["list_id"] for row in await cursor.fetchall()]

    # Delete the leads
    await db.execute(
        f"DELETE FROM custom_list_leads WHERE id IN ({placeholders})", ids
    )

    # Update counts for affected lists
    for lid in affected_lists:
        cursor = await db.execute(
            "SELECT COUNT(*) AS cnt FROM custom_list_leads WHERE list_id = ?", (lid,)
        )
        row = await cursor.fetchone()
        await db.execute(
            "UPDATE custom_lists SET lead_count = ? WHERE id = ?",
            (row["cnt"] if row else 0, lid),
        )

    await db.commit()
    return RedirectResponse(url="/leads", status_code=303)


# -----------------------------------------------------------------------
# Delete ALL leads matching current filters (bulk filter delete)
# -----------------------------------------------------------------------

@router.post("/leads/delete-filtered")
async def delete_filtered_leads(
    request: Request,
    search: str = Form(""),
    company: str = Form(""),
    location: str = Form(""),
    title: str = Form(""),
    list_id: int = Form(0),
    source: str = Form(""),
    status: str = Form(""),
):
    """Delete ALL leads that match the current filter criteria."""
    db = await get_lp_db()

    conditions = []
    params = []

    if search:
        conditions.append(
            "(full_name LIKE ? OR headline LIKE ? OR company LIKE ? OR profile_url LIKE ?)"
        )
        s = f"%{search}%"
        params.extend([s, s, s, s])
    if company:
        conditions.append("company LIKE ?")
        params.append(f"%{company}%")
    if location:
        conditions.append("location LIKE ?")
        params.append(f"%{location}%")
    if title:
        conditions.append("headline LIKE ?")
        params.append(f"%{title}%")
    if list_id:
        conditions.append("list_id = ?")
        params.append(list_id)
    if source:
        conditions.append("source = ?")
        params.append(source)
    if status:
        conditions.append("status = ?")
        params.append(status)

    where = ""
    if conditions:
        where = "WHERE " + " AND ".join(conditions)

    # Get affected list IDs before deletion
    cursor = await db.execute(
        f"SELECT DISTINCT list_id FROM custom_list_leads {where}", params
    )
    affected_lists = [row["list_id"] for row in await cursor.fetchall()]

    # Delete matching leads
    await db.execute(f"DELETE FROM custom_list_leads {where}", params)

    # Update counts for affected lists
    for lid in affected_lists:
        cursor = await db.execute(
            "SELECT COUNT(*) AS cnt FROM custom_list_leads WHERE list_id = ?", (lid,)
        )
        row = await cursor.fetchone()
        await db.execute(
            "UPDATE custom_lists SET lead_count = ? WHERE id = ?",
            (row["cnt"] if row else 0, lid),
        )

    await db.commit()
    return RedirectResponse(url="/leads", status_code=303)


# -----------------------------------------------------------------------
# Export leads as CSV
# -----------------------------------------------------------------------

@router.post("/leads/export-csv")
async def export_csv(
    request: Request,
    search: str = Form(""),
    company: str = Form(""),
    location: str = Form(""),
    title: str = Form(""),
    list_id: int = Form(0),
):
    """Export filtered leads to CSV."""
    db = await get_lp_db()

    conditions = []
    params = []

    if search:
        conditions.append(
            "(cll.full_name LIKE ? OR cll.headline LIKE ? OR cll.company LIKE ? OR cll.profile_url LIKE ?)"
        )
        s = f"%{search}%"
        params.extend([s, s, s, s])
    if company:
        conditions.append("cll.company LIKE ?")
        params.append(f"%{company}%")
    if location:
        conditions.append("cll.location LIKE ?")
        params.append(f"%{location}%")
    if title:
        conditions.append("cll.headline LIKE ?")
        params.append(f"%{title}%")
    if list_id:
        conditions.append("cll.list_id = ?")
        params.append(list_id)

    where = ""
    if conditions:
        where = "WHERE " + " AND ".join(conditions)

    cursor = await db.execute(
        f"""
        SELECT cll.full_name, cll.first_name, cll.headline, cll.company,
               cll.location, cll.profile_url, cl.name AS list_name,
               cll.source, cll.created_at
        FROM custom_list_leads cll
        LEFT JOIN custom_lists cl ON cll.list_id = cl.id
        {where}
        ORDER BY cll.created_at DESC
        """,
        params,
    )
    leads = [dict(row) for row in await cursor.fetchall()]

    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    filename = f"leads_export_{timestamp}.csv"
    file_path = await export_leads_csv(leads, filename)

    return FileResponse(
        path=file_path,
        filename=filename,
        media_type="text/csv",
    )
