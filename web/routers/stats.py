"""Stats Router — страница статистики."""
from __future__ import annotations
from fastapi import APIRouter, Request
from config import settings
from fastapi.responses import HTMLResponse, RedirectResponse
from database.session import async_session_factory
from database import crud
from services.stats_service import StatsService
from web.auth import get_session_token, verify_session

router = APIRouter(prefix="/stats", tags=["stats"])

@router.get("", response_class=HTMLResponse)
async def stats_page(request: Request):
    token = get_session_token(request)
    if not verify_session(token):
        return RedirectResponse(f"{settings.ADMIN_PATH}/login", status_code=302)
    templates = request.app.state.templates
    async with async_session_factory() as session:
        metrics = await StatsService.get_dashboard_metrics(session)
        daily = await StatsService.get_daily_stats(session, days=30)
        payments = await crud.get_all_payments(session, limit=50)
    return templates.TemplateResponse(request=request, name="stats.html", context={
        "request": request, "metrics": metrics,
        "daily_stats": daily, "payments": payments, "page": "stats",
    })
