"""Servers Router — статус VPN серверов."""
from __future__ import annotations
from fastapi import APIRouter, Request
from config import settings
from fastapi.responses import HTMLResponse, RedirectResponse
from database.session import async_session_factory
from database import crud
from services.server_manager import ServerManager
from web.auth import get_session_token, verify_session

router = APIRouter(prefix="/servers", tags=["servers"])
sm = ServerManager()

@router.get("", response_class=HTMLResponse)
async def servers_page(request: Request):
    token = get_session_token(request)
    if not verify_session(token):
        return RedirectResponse(f"{settings.ADMIN_PATH}/login", status_code=302)
    templates = request.app.state.templates
    async with async_session_factory() as session:
        servers = await crud.get_active_servers(session)
    server_data = []
    for s in servers:
        status = await sm.get_server_status(s)
        server_data.append({"server": s, "status": status})
    return templates.TemplateResponse(request=request, name="servers.html", context={
        "request": request, "servers": server_data, "page": "servers",
    })
