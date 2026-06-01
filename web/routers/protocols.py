"""Protocols Router — управление VPN протоколами на серверах."""
from __future__ import annotations
from fastapi import APIRouter, Request, Form
from config import settings
from fastapi.responses import HTMLResponse, RedirectResponse
from database.session import async_session_factory
from database import crud
from services.server_manager import ServerManager
from services.protocols import get_protocol_handler
from web.auth import get_session_token, verify_session

router = APIRouter(prefix="/protocols", tags=["protocols"])
sm = ServerManager()

AVAILABLE_PROTOCOLS = [
    {"id": "awg", "name": "AmneziaWG", "icon": "fa-shield-halved", "color": "brand"},
]


@router.get("", response_class=HTMLResponse)
async def protocols_page(request: Request):
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

    return templates.TemplateResponse(request=request, name="protocols.html", context={
        "request": request, "servers": server_data,
        "protocols": AVAILABLE_PROTOCOLS, "page": "protocols",
    })


@router.post("/deploy")
async def deploy_protocol(
    request: Request,
    server_id: int = Form(...),
    protocol: str = Form(...),
    acme_domains: str = Form(""),
    acme_email: str = Form(""),
):
    token = get_session_token(request)
    if not verify_session(token):
        return RedirectResponse(f"{settings.ADMIN_PATH}/login", status_code=302)

    async with async_session_factory() as session:
        server = await crud.get_server_by_id(session, server_id)
        if not server:
            return RedirectResponse(f"{settings.ADMIN_PATH}/protocols?error=server_not_found", status_code=302)

    try:
        handler = get_protocol_handler(protocol)
        kwargs = {}
        if acme_domains:
            kwargs["acme_domains"] = [d.strip() for d in acme_domains.split(",")]
        if acme_email:
            kwargs["acme_email"] = acme_email.strip()

        await handler.deploy_server(server, **kwargs)
    except Exception as e:
        return RedirectResponse(f"/protocols?error={str(e)[:200]}", status_code=302)

    return RedirectResponse(f"{settings.ADMIN_PATH}/protocols?deployed=1", status_code=302)
