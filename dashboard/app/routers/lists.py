"""LinkedPilot v2 — Named lists router."""

from fastapi import APIRouter, Request, Form, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse
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

    return templates.TemplateResponse("list_detail.html", {
        "request": request,
        "list": lst,
        "leads": leads,
        "senders": senders,
        "active_page": "lists",
    })


@router.post("/lists/{list_id}/upload-csv")
async def upload_csv_to_list(
    request: Request,
    list_id: int,
    file: UploadFile = File(...),
):
    """Upload a CSV file to add leads to a list."""
    db = await get_lp_db()

    # Verify list exists
    cursor = await db.execute(
        "SELECT id FROM custom_lists WHERE id = ?", (list_id,)
    )
    if not await cursor.fetchone():
        return RedirectResponse(url="/lists", status_code=303)

    content = await file.read()
    result = await import_csv_to_list(content, list_id)

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

    return RedirectResponse(url=f"/lists/{list_id}", status_code=303)


@router.delete("/lists/{list_id}")
async def delete_list(request: Request, list_id: int):
    """Delete a custom list and its leads."""
    db = await get_lp_db()
    await db.execute("DELETE FROM custom_list_leads WHERE list_id = ?", (list_id,))
    await db.execute("DELETE FROM custom_lists WHERE id = ?", (list_id,))
    await db.commit()
    return RedirectResponse(url="/lists", status_code=303)
