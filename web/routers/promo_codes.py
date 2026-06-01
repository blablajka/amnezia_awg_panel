"""Promo Codes Router — управление промокодами."""
from __future__ import annotations
from decimal import Decimal
from fastapi import APIRouter, Request, Form
from config import settings
from fastapi.responses import HTMLResponse, RedirectResponse
from database.session import async_session_factory
from database import crud
from services.promo_service import PromoService
from web.auth import get_session_token, verify_session

router = APIRouter(prefix="/promo-codes", tags=["promo_codes"])

@router.get("", response_class=HTMLResponse)
async def promo_codes_page(request: Request):
    token = get_session_token(request)
    if not verify_session(token):
        return RedirectResponse(f"{settings.ADMIN_PATH}/login", status_code=302)
    templates = request.app.state.templates
    async with async_session_factory() as session:
        promos = await crud.get_all_promo_codes(session)
    return templates.TemplateResponse("promo_codes.html", {
        "request": request, "promos": promos, "page": "promo_codes",
    })

@router.post("/create")
async def create_promo(
    request: Request,
    code: str = Form(...),
    discount_percent: int = Form(0),
    max_uses: int = Form(None),
):
    token = get_session_token(request)
    if not verify_session(token):
        return RedirectResponse(f"{settings.ADMIN_PATH}/login", status_code=302)
    async with async_session_factory() as session:
        await PromoService.create_promo(
            session=session, code=code,
            discount_percent=discount_percent, max_uses=max_uses,
        )
        await session.commit()
    return RedirectResponse(f"{settings.ADMIN_PATH}/promo-codes", status_code=302)
