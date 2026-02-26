"""LinkedPilot v2 — Predefined comments router."""

from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.config import TEMPLATES_DIR
from app.database import get_lp_db

router = APIRouter()
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


@router.get("/comments", response_class=HTMLResponse)
async def comments_page(request: Request):
    """Show all predefined comments with usage counts."""
    db = await get_lp_db()
    cursor = await db.execute(
        "SELECT * FROM predefined_comments ORDER BY created_at DESC"
    )
    comments = [dict(row) for row in await cursor.fetchall()]

    return templates.TemplateResponse("comments.html", {
        "request": request,
        "comments": comments,
        "active_page": "comments",
    })


@router.post("/comments/add")
async def add_comment(
    request: Request,
    text: str = Form(...),
    category: str = Form("general"),
):
    """Add a new predefined comment."""
    db = await get_lp_db()
    await db.execute(
        "INSERT INTO predefined_comments (text, category) VALUES (?, ?)",
        (text, category),
    )
    await db.commit()
    return RedirectResponse(url="/comments", status_code=303)


@router.post("/comments/{comment_id}/edit")
async def edit_comment(
    request: Request,
    comment_id: int,
    text: str = Form(...),
):
    """Edit an existing predefined comment's text."""
    db = await get_lp_db()
    await db.execute(
        "UPDATE predefined_comments SET text = ? WHERE id = ?",
        (text, comment_id),
    )
    await db.commit()
    return RedirectResponse(url="/comments", status_code=303)


@router.post("/comments/{comment_id}/toggle")
async def toggle_comment(request: Request, comment_id: int):
    """Toggle a predefined comment's is_active flag."""
    db = await get_lp_db()
    await db.execute(
        "UPDATE predefined_comments SET is_active = CASE WHEN is_active = 1 THEN 0 ELSE 1 END WHERE id = ?",
        (comment_id,),
    )
    await db.commit()
    return RedirectResponse(url="/comments", status_code=303)


@router.delete("/comments/{comment_id}")
async def delete_comment(request: Request, comment_id: int):
    """Delete a predefined comment."""
    db = await get_lp_db()
    await db.execute(
        "DELETE FROM predefined_comments WHERE id = ?",
        (comment_id,),
    )
    await db.commit()
    return RedirectResponse(url="/comments", status_code=303)
