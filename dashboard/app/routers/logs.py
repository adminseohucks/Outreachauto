"""LinkedPilot v2 — Activity logs router."""

import csv
import io
from datetime import datetime

import aiofiles

from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse
from fastapi.templating import Jinja2Templates

from app.config import TEMPLATES_DIR, EXPORTS_DIR
from app.database import get_lp_db

router = APIRouter()
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


@router.get("/logs", response_class=HTMLResponse)
async def logs_page(
    request: Request,
    action_type: str = "",
    status: str = "",
    date_from: str = "",
    date_to: str = "",
    page: int = 1,
):
    """Show activity logs with date/type filters and pagination."""
    db = await get_lp_db()
    per_page = 50

    conditions: list[str] = []
    params: list = []

    if action_type:
        conditions.append("action_type = ?")
        params.append(action_type)

    if status:
        conditions.append("status = ?")
        params.append(status)

    if date_from:
        conditions.append("created_at >= ?")
        params.append(date_from)

    if date_to:
        conditions.append("created_at <= ?")
        params.append(date_to + " 23:59:59")

    where_clause = ""
    if conditions:
        where_clause = "WHERE " + " AND ".join(conditions)

    # Total count
    cursor = await db.execute(
        f"SELECT COUNT(*) AS cnt FROM activity_log {where_clause}",
        params,
    )
    row = await cursor.fetchone()
    total = row["cnt"] if row else 0

    pages = max(1, -(-total // per_page))  # ceil division
    offset = (page - 1) * per_page

    # Paginated results
    cursor = await db.execute(
        f"SELECT * FROM activity_log {where_clause} ORDER BY created_at DESC LIMIT ? OFFSET ?",
        params + [per_page, offset],
    )
    logs = [dict(row) for row in await cursor.fetchall()]

    # Get distinct action types for the filter dropdown
    cursor = await db.execute(
        "SELECT DISTINCT action_type FROM activity_log ORDER BY action_type"
    )
    action_types = [row["action_type"] for row in await cursor.fetchall()]

    # Get distinct statuses for the filter dropdown
    cursor = await db.execute(
        "SELECT DISTINCT status FROM activity_log ORDER BY status"
    )
    statuses = [row["status"] for row in await cursor.fetchall()]

    return templates.TemplateResponse("logs.html", {
        "request": request,
        "logs": logs,
        "total": total,
        "page": page,
        "pages": pages,
        "per_page": per_page,
        "action_types": action_types,
        "statuses": statuses,
        "filters": {
            "action_type": action_type,
            "status": status,
            "date_from": date_from,
            "date_to": date_to,
        },
        "active_page": "logs",
    })


@router.post("/logs/export")
async def export_logs(
    request: Request,
    action_type: str = Form(""),
    status: str = Form(""),
    date_from: str = Form(""),
    date_to: str = Form(""),
):
    """Export filtered activity logs to CSV."""
    db = await get_lp_db()

    conditions: list[str] = []
    params: list = []

    if action_type:
        conditions.append("action_type = ?")
        params.append(action_type)

    if status:
        conditions.append("status = ?")
        params.append(status)

    if date_from:
        conditions.append("created_at >= ?")
        params.append(date_from)

    if date_to:
        conditions.append("created_at <= ?")
        params.append(date_to + " 23:59:59")

    where_clause = ""
    if conditions:
        where_clause = "WHERE " + " AND ".join(conditions)

    cursor = await db.execute(
        f"SELECT * FROM activity_log {where_clause} ORDER BY created_at DESC",
        params,
    )
    rows = [dict(row) for row in await cursor.fetchall()]

    # Build CSV in memory
    headers = [
        "id", "action_type", "sender_id", "sender_name",
        "lead_name", "lead_url", "campaign_id", "status",
        "details", "created_at",
    ]
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=headers, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)

    # Write to file
    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    filename = f"activity_logs_{timestamp}.csv"
    file_path = EXPORTS_DIR / filename

    async with aiofiles.open(str(file_path), mode="w", encoding="utf-8", newline="") as f:
        await f.write(output.getvalue())

    return FileResponse(
        path=str(file_path),
        filename=filename,
        media_type="text/csv",
    )
