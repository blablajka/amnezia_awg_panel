"""Users Router — список пользователей."""
from __future__ import annotations
import logging
from fastapi import APIRouter, Request
from config import settings
from fastapi.responses import HTMLResponse, RedirectResponse
from database.session import async_session_factory
from database import crud
from web.auth import get_session_token, verify_session

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/users", tags=["users"])

@router.get("", response_class=HTMLResponse)
async def users_page(request: Request):
    token = get_session_token(request)
    if not await verify_session(token):
        return RedirectResponse(f"{settings.ADMIN_PATH}/login", status_code=302)
    templates = request.app.state.templates
    try:
        async with async_session_factory() as session:
            users = await crud.get_all_users(session, limit=200)
            total = await crud.count_users(session)
    except Exception as e:
        logger.error("Users page DB: %s", e)
        users, total = [], 0
    return templates.TemplateResponse(request=request, name="users.html", context={
        "request": request, "users": users, "total": total, "page": "users",
        "admin_path": settings.ADMIN_PATH,
    })
