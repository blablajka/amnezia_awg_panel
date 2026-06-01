"""Bridges Router — управление мостами/туннелями между серверами."""
from __future__ import annotations
from fastapi import APIRouter, Request, Form
from config import settings
from fastapi.responses import HTMLResponse, RedirectResponse
from database.session import async_session_factory
from database import crud
from services.server_manager import ServerManager
from web.auth import get_session_token, verify_session

router = APIRouter(prefix="/bridges", tags=["bridges"])
sm = ServerManager()


@router.get("", response_class=HTMLResponse)
async def bridges_page(request: Request):
    token = get_session_token(request)
    if not verify_session(token):
        return RedirectResponse(f"{settings.ADMIN_PATH}/login", status_code=302)
    templates = request.app.state.templates

    async with async_session_factory() as session:
        bridges = await crud.get_all_bridges(session)
        servers = await crud.get_active_servers(session)

    return templates.TemplateResponse("bridges.html", {
        "request": request, "bridges": bridges,
        "servers": servers, "page": "bridges",
        "admin_path": settings.ADMIN_PATH,
    })


@router.post("/create")
async def create_bridge(
    request: Request,
    server_from_id: int = Form(...),
    server_to_id: int = Form(...),
    protocol: str = Form("awg_bridge"),
):
    token = get_session_token(request)
    if not verify_session(token):
        return RedirectResponse(f"{settings.ADMIN_PATH}/login", status_code=302)

    async with async_session_factory() as session:
        bridge = await crud.create_bridge(
            session, server_from_id, server_to_id, protocol=protocol,
        )

        from_server = await crud.get_server_by_id(session, server_from_id)
        to_server = await crud.get_server_by_id(session, server_to_id)

        # Мосты пока работают в тестовом режиме, ждем реализации AWG Bridge
        bridge.config_data = "AWG Bridge Not Implemented Yet"
        
        await session.commit()

    return RedirectResponse(f"{settings.ADMIN_PATH}/bridges?created=1", status_code=302)


@router.post("/delete")
async def delete_bridge(request: Request, bridge_id: int = Form(...)):
    token = get_session_token(request)
    if not verify_session(token):
        return RedirectResponse(f"{settings.ADMIN_PATH}/login", status_code=302)

    async with async_session_factory() as session:
        await crud.delete_bridge(session, bridge_id)
        await session.commit()

    return RedirectResponse(f"{settings.ADMIN_PATH}/bridges?deleted=1", status_code=302)


@router.post("/toggle")
async def toggle_bridge(request: Request, bridge_id: int = Form(...)):
    token = get_session_token(request)
    if not verify_session(token):
        return RedirectResponse(f"{settings.ADMIN_PATH}/login", status_code=302)

    async with async_session_factory() as session:
        bridge = await crud.get_bridge_by_id(session, bridge_id)
        if bridge:
            await crud.update_bridge_status(session, bridge_id, not bridge.is_active)
            await session.commit()

    return RedirectResponse(f"{settings.ADMIN_PATH}/bridges?toggled=1", status_code=302)
