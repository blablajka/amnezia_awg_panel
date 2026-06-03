"""Servers Router — server management & auto-install."""
from __future__ import annotations
import logging
from fastapi import APIRouter, Request, Form
from config import settings
from fastapi.responses import HTMLResponse, RedirectResponse
from database.session import async_session_factory
from database import crud
from services.server_manager import ServerManager
from web.auth import get_session_token, verify_session

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/servers", tags=["servers"])
sm = ServerManager()


@router.get("", response_class=HTMLResponse)
async def servers_page(request: Request):
    token = get_session_token(request)
    if not await verify_session(token):
        return RedirectResponse(f"{settings.ADMIN_PATH}/login", status_code=302)
    templates = request.app.state.templates
    async with async_session_factory() as session:
        servers = await crud.get_all_servers(session)
    server_data = []
    for s in servers:
        try:
            status = await sm.get_server_status(s)
        except Exception:
            status = {"status": "offline", "peers": 0}
        server_data.append({"server": s, "status": status})
    return templates.TemplateResponse(request=request, name="servers.html", context={
        "request": request, "servers": server_data, "page": "servers",
        "admin_path": settings.ADMIN_PATH,
    })


@router.post("/add")
async def add_server(
    request: Request,
    name: str = Form(...),
    host: str = Form(...),
    port: int = Form(22),
    ssh_user: str = Form("root"),
    ssh_password: str = Form(""),
    country_code: str = Form("DK"),
    preset: str = Form("default"),
    awg_listen_port: int = Form(39743),
    ipv6_enabled: bool = Form(True),
):
    token = get_session_token(request)
    if not await verify_session(token):
        return RedirectResponse(f"{settings.ADMIN_PATH}/login", status_code=302)

    async with async_session_factory() as session:
        server = await crud.create_server(
            session, name=name, host=host, country_code=country_code,
            port=port, ssh_user=ssh_user,
            ssh_password=ssh_password or None,
            protocol="awg", awg_preset=preset,
            awg_listen_port=awg_listen_port,
            ipv6_enabled=ipv6_enabled,
        )
        try:
            await sm.deploy_awg_server(server, preset=preset)
            logger.info("Server %s auto-installed OK", name)
        except Exception as e:
            logger.error("Auto-install failed for %s: %s", name, e)
            await session.rollback()
            return RedirectResponse(
                f"{settings.ADMIN_PATH}/servers?error={str(e)[:100]}",
                status_code=302,
            )
        await session.commit()
    return RedirectResponse(
        f"{settings.ADMIN_PATH}/servers?added=1",
        status_code=302,
    )


@router.post("/{server_id}/syncconf")
async def sync_server_config(request: Request, server_id: int):
    """Hot-reload AWG config on server (awg syncconf)."""
    token = get_session_token(request)
    if not await verify_session(token):
        return RedirectResponse(f"{settings.ADMIN_PATH}/login", status_code=302)

    async with async_session_factory() as session:
        server = await crud.get_server_by_id(session, server_id)
        if not server:
            return RedirectResponse(f"{settings.ADMIN_PATH}/servers?error=notfound", status_code=302)

        from services.protocols.awg import AwgProtocolHandler
        awg = AwgProtocolHandler(sm)
        try:
            result = await awg.syncconf(server)
            logger.info("syncconf on %s: %s", server.name, result[:100])
        except Exception as e:
            logger.error("syncconf failed on %s: %s", server.name, e)
            return RedirectResponse(
                f"{settings.ADMIN_PATH}/servers?error=syncconf_failed",
                status_code=302,
            )

    return RedirectResponse(f"{settings.ADMIN_PATH}/servers?synced=1", status_code=302)


@router.post("/{server_id}/delete")
async def delete_server(request: Request, server_id: int):
    """Delete server and deactivate all associated UserServer records."""
    token = get_session_token(request)
    if not await verify_session(token):
        return RedirectResponse(f"{settings.ADMIN_PATH}/login", status_code=302)

    async with async_session_factory() as session:
        server = await crud.get_server_by_id(session, server_id)
        if not server:
            return RedirectResponse(f"{settings.ADMIN_PATH}/servers?error=notfound", status_code=302)

        # Deactivate all user configs on this server
        from sqlalchemy import update
        from database.models import UserServer
        await session.execute(
            update(UserServer)
            .where(UserServer.server_id == server_id)
            .values(is_active=False)
        )
        server.is_active = False
        await session.delete(server)
        await session.commit()
        logger.info("Server %s deleted", server.name)

    return RedirectResponse(f"{settings.ADMIN_PATH}/servers?deleted=1", status_code=302)
