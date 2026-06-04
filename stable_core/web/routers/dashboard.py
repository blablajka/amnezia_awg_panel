"""
Dashboard Router — main admin page with live metrics and server status.
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Request
from config import settings
from fastapi.responses import HTMLResponse, RedirectResponse

from database.session import async_session_factory
from services.stats_service import StatsService
from services.server_manager import ServerManager
from web.auth import get_session_token, verify_session

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("", response_class=HTMLResponse)
async def dashboard_page(request: Request):
    """Dashboard with live server health checks."""
    token = get_session_token(request)
    if not await verify_session(token):
        return RedirectResponse(f"{settings.ADMIN_PATH}/login", status_code=302)

    templates = request.app.state.templates

    async with async_session_factory() as session:
        metrics = await StatsService.get_dashboard_metrics(session)
        daily_stats = await StatsService.get_daily_stats(session, days=7)
        servers = await StatsService.get_active_servers_metrics(session)

    # Async server health checks (non-blocking, best-effort)
    sm = ServerManager()
    server_statuses = []
    for s in servers:
        try:
            status = await asyncio.wait_for(sm.get_server_status(s), timeout=5.0)
        except Exception as _e:
            logger.debug("Health check %s: %s", s.name, _e)
            status = {"status": "offline", "peers": 0}
        server_statuses.append({"server": s, "status": status})

    return templates.TemplateResponse(request=request, name="dashboard.html", context={
        "request": request,
        "metrics": metrics,
        "daily_stats": daily_stats,
        "server_statuses": server_statuses,
        "page": "dashboard",
        "admin_path": settings.ADMIN_PATH,
    })
