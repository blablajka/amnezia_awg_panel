"""Users Router — список пользователей."""
from __future__ import annotations
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from database.session import async_session_factory
from database import crud
from web.auth import get_session_token, verify_session

router = APIRouter(prefix="/users", tags=["users"])

@router.get("", response_class=HTMLResponse)
async def users_page(request: Request):
    token = get_session_token(request)
    if not verify_session(token):
        return RedirectResponse("/login", status_code=302)
    templates = request.app.state.templates
    async with async_session_factory() as session:
        users = await crud.get_all_users(session, limit=200)
        total = await crud.count_users(session)
    return templates.TemplateResponse("users.html", {
        "request": request, "users": users, "total": total, "page": "users",
    })
