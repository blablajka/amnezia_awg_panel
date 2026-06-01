"""
Dashboard Router — главная страница админ-панели с метриками.
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from database.session import async_session_factory
from services.stats_service import StatsService
from web.auth import get_session_token, verify_session

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("", response_class=HTMLResponse)
async def dashboard_page(request: Request):
    """Главная страница дашборда."""
    token = get_session_token(request)
    if not verify_session(token):
        return RedirectResponse("/login", status_code=302)

    templates = request.app.state.templates

    async with async_session_factory() as session:
        metrics = await StatsService.get_dashboard_metrics(session)
        daily_stats = await StatsService.get_daily_stats(session, days=7)

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "metrics": metrics,
        "daily_stats": daily_stats,
        "page": "dashboard",
    })
