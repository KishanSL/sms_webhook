"""Server-rendered HTML pages."""

from __future__ import annotations

from fastapi import APIRouter, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse

from app.config import settings
from app.database import get_bank_map
from app.deps import templates

router = APIRouter()


@router.get("/", response_class=HTMLResponse, summary="Dashboard UI")
@router.get("/dashboard", response_class=HTMLResponse, summary="Dashboard UI")
async def dashboard(request: Request) -> Response:
    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "app_name": settings.APP_NAME,
            "version": settings.APP_VERSION,
        },
    )


@router.get("/transactions", response_class=HTMLResponse, summary="Bank transactions UI")
async def transactions_page(request: Request) -> Response:
    return templates.TemplateResponse(
        request=request,
        name="transactions.html",
        context={
            "app_name": settings.APP_NAME,
            "version": settings.APP_VERSION,
            # One entry per bank name: a bank may be linked from several headers
            "bank_names": sorted(set((await get_bank_map(settings.DB_PATH)).values())),
            "gnucash_enabled": settings.gnucash_enabled,
            "uncategorised_label": settings.GNUCASH_UNCATEGORISED_LABEL,
        },
    )


@router.get("/cards", response_class=HTMLResponse, summary="Credit card pending payments UI")
async def cards_page(request: Request) -> Response:
    return templates.TemplateResponse(
        request=request,
        name="cards.html",
        context={
            "app_name": settings.APP_NAME,
            "version": settings.APP_VERSION,
        },
    )


@router.get("/settings", response_class=HTMLResponse, summary="Settings: banks, accounts, data")
async def settings_page(request: Request) -> Response:
    return templates.TemplateResponse(
        request=request,
        name="settings.html",
        context={
            "app_name": settings.APP_NAME,
            "version": settings.APP_VERSION,
            "gnucash_enabled": settings.gnucash_enabled,
        },
    )


@router.get("/banks", include_in_schema=False)
async def banks_redirect() -> RedirectResponse:
    """Old URL, kept working."""
    return RedirectResponse(url="/settings", status_code=308)
