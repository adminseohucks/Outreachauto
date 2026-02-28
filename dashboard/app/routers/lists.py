"""LinkedPilot v2 — Named lists router."""

import json

from fastapi import APIRouter, Request, Form, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from app.config import TEMPLATES_DIR
from app.database import get_lp_db
from app.services.csv_handler import import_csv_to_list

router = APIRouter()
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


@router.get("/lists", response_class=HTMLResponse)
async def lists_page(request: Request):
    """Show all custom lists with lead counts."""
    db = await get_lp_db()
    cursor = await db.execute(
        "SELECT * FROM custom_lists ORDER BY created_at DESC"
    )
    lists = [dict(row) for row in await cursor.fetchall()]

    return templates.TemplateResponse("lists.html", {
        "request": request,
        "lists": lists,
        "active_page": "lists",
    })


@router.post("/lists/create")
async def create_list(
    request: Request,
    name: str = Form(...),
    description: str = Form(""),
):
    """Create a new custom list."""
    db = await get_lp_db()
    await db.execute(
        "INSERT INTO custom_lists (name, description, source) VALUES (?, ?, 'manual')",
        (name, description),
    )
    await db.commit()
    return RedirectResponse(url="/lists", status_code=303)


@router.get("/lists/{list_id}", response_class=HTMLResponse)
async def list_detail(request: Request, list_id: int):
    """Show leads in a specific list with campaign action buttons."""
    db = await get_lp_db()

    # Get the list info
    cursor = await db.execute(
        "SELECT * FROM custom_lists WHERE id = ?", (list_id,)
    )
    lst = await cursor.fetchone()
    if not lst:
        return RedirectResponse(url="/lists", status_code=303)
    lst = dict(lst)

    # Get leads in this list
    cursor = await db.execute(
        "SELECT * FROM custom_list_leads WHERE list_id = ? ORDER BY created_at DESC",
        (list_id,),
    )
    leads = [dict(row) for row in await cursor.fetchall()]

    # Get active senders for campaign creation
    cursor = await db.execute(
        "SELECT id, name FROM senders WHERE status = 'active' ORDER BY name"
    )
    senders = [dict(row) for row in await cursor.fetchall()]

    # Get company pages for campaign creation "Act As" dropdown
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

    return templates.TemplateResponse("list_detail.html", {
        "request": request,
        "list": lst,
        "leads": leads,
        "senders": senders,
        "company_pages": company_pages,
        "active_page": "lists",
    })


@router.post("/lists/{list_id}/upload-csv")
async def upload_csv_to_list(
    request: Request,
    list_id: int,
    file: UploadFile = File(...),
    column_mapping: str = Form(""),
):
    """Upload a CSV file to add leads to a list with optional column mapping."""
    db = await get_lp_db()

    # Verify list exists
    cursor = await db.execute(
        "SELECT id FROM custom_lists WHERE id = ?", (list_id,)
    )
    if not await cursor.fetchone():
        return JSONResponse({"success": False, "errors": ["List not found."]})

    content = await file.read()

    # Parse column mapping JSON from the header-matching modal
    mapping = None
    if column_mapping:
        try:
            mapping = json.loads(column_mapping)
        except json.JSONDecodeError:
            mapping = None

    result = await import_csv_to_list(content, list_id, column_mapping=mapping)

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

    return JSONResponse({
        "success": True,
        "imported": result["imported"],
        "skipped": result["skipped"],
        "errors": result["errors"],
    })


@router.delete("/lists/{list_id}")
async def delete_list(request: Request, list_id: int):
    """Delete a custom list and its leads."""
    db = await get_lp_db()
    await db.execute("DELETE FROM custom_list_leads WHERE list_id = ?", (list_id,))
    await db.execute("DELETE FROM custom_lists WHERE id = ?", (list_id,))
    await db.commit()
    return RedirectResponse(url="/lists", status_code=303)


@router.post("/lists/{list_id}/leads/{lead_id}/delete")
async def delete_lead_from_list(request: Request, list_id: int, lead_id: int):
    """Delete a single lead from a list."""
    db = await get_lp_db()
    await db.execute(
        "DELETE FROM custom_list_leads WHERE id = ? AND list_id = ?",
        (lead_id, list_id),
    )
    # Update lead count
    cursor = await db.execute(
        "SELECT COUNT(*) AS cnt FROM custom_list_leads WHERE list_id = ?", (list_id,)
    )
    row = await cursor.fetchone()
    await db.execute(
        "UPDATE custom_lists SET lead_count = ? WHERE id = ?",
        (row["cnt"] if row else 0, list_id),
    )
    await db.commit()
    return RedirectResponse(url=f"/lists/{list_id}", status_code=303)
