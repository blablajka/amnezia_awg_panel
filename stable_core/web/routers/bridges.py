"""Bridges Router — cascade tunnels between servers."""
from __future__ import annotations
import json
import logging
from fastapi import APIRouter, Request, Form
from config import settings
from fastapi.responses import HTMLResponse, RedirectResponse
from database.session import async_session_factory
from database import crud
from services.server_manager import ServerManager
from web.auth import get_session_token, verify_session

logger = logging.getLogger(__name__)

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

    return templates.TemplateResponse(request=request, name="bridges.html", context={
        "request": request, "bridges": bridges,
        "servers": servers, "page": "bridges",
        "admin_path": settings.ADMIN_PATH,
    })


@router.post("/create")
async def create_bridge(
    request: Request,
    server_from_id: int = Form(...),
    server_to_id: int = Form(...),
    protocol: str = Form("awg"),
    routing_mode: str = Form("full"),
    preset: str = Form("default"),
):
    token = get_session_token(request)
    if not verify_session(token):
        return RedirectResponse(f"{settings.ADMIN_PATH}/login", status_code=302)

    async with async_session_factory() as session:
        from_server = await crud.get_server_by_id(session, server_from_id)
        to_server = await crud.get_server_by_id(session, server_to_id)

        if not from_server or not to_server:
            return RedirectResponse(
                f"{settings.ADMIN_PATH}/bridges?error=server_not_found", status_code=302,
            )

        try:
            result = await sm.deploy_full_cascade(
                russian_server=from_server,
                foreign_server=to_server,
                tunnel_interface="awg1",
                routing_mode=routing_mode,
                preset=preset,
            )

            bridge = await crud.create_bridge(
                session,
                server_from_id=server_from_id,
                server_to_id=server_to_id,
                protocol=protocol,
                config_data=json.dumps(result),
            )
            bridge.auto_installed = True
            bridge.awg_params = json.dumps(result.get("awg_params", {}))
            bridge.tunnel_interface = result.get("tunnel_interface", "awg1")
            bridge.listen_port = result.get("listen_port", 0)
            bridge.routing_mode = routing_mode

            to_server.awg_params = json.dumps(result.get("awg_params", {}))
            to_server.awg_preset = preset

            await session.commit()
            logger.info("Cascade deployed: %s -> %s", from_server.name, to_server.name)
            return RedirectResponse(
                f"{settings.ADMIN_PATH}/bridges?deployed=1&interface={result.get('tunnel_interface','awg1')}",
                status_code=302,
            )
        except Exception as e:
            logger.error("Cascade deploy failed: %s", e)
            return RedirectResponse(
                f"{settings.ADMIN_PATH}/bridges?error={str(e)[:200]}", status_code=302,
            )


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
