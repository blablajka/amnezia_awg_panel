"""Subscriptions Router — список подписок."""
from __future__ import annotations
from fastapi import APIRouter, Request
from config import settings
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from database.session import async_session_factory
from database.models import Subscription
from web.auth import get_session_token, verify_session

router = APIRouter(prefix="/subscriptions", tags=["subscriptions"])

@router.get("", response_class=HTMLResponse)
async def subscriptions_page(request: Request):
    token = get_session_token(request)
    if not verify_session(token):
        return RedirectResponse(f"{settings.ADMIN_PATH}/login", status_code=302)
    templates = request.app.state.templates
    async with async_session_factory() as session:
        stmt = select(Subscription).order_by(Subscription.created_at.desc()).limit(200)
        result = await session.execute(stmt)
        subs = list(result.scalars().all())
    return templates.TemplateResponse(request=request, name="subscriptions.html", context={
        "request": request, "subscriptions": subs, "page": "subscriptions",
        "admin_path": settings.ADMIN_PATH,
    })
