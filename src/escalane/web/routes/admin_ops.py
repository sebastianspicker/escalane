"""Activity, system status, and simulation console pages."""

from __future__ import annotations

from fastapi import APIRouter, Cookie, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from escalane import __version__
from escalane.config.settings import Settings
from escalane.persistence.models import AdminAuditEvent, Alarm
from escalane.providers.mock import get_mock_store
from escalane.web.deps import get_app_settings, get_session
from escalane.web.routes.admin_console import (
    UiPageContext,
    _action_session,
    _html,
    _requested_locale,
    _session_from_request,
)

router = APIRouter()


@router.get("/admin/activity", response_class=HTMLResponse)
async def admin_activity(
    request: Request,
    page: UiPageContext,
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    locale, browser_session = page
    events = list(
        (
            await session.scalars(
                select(AdminAuditEvent).order_by(AdminAuditEvent.created_at.desc()).limit(100)
            )
        ).all()
    )
    return _html(
        request,
        "admin_activity.html",
        locale,
        events=events,
        operator_name=browser_session.operator_name,
        logout_action="/admin/logout",
        csrf_token=browser_session.csrf_token,
    )


@router.get("/admin/system", response_class=HTMLResponse)
async def admin_system(
    request: Request,
    *,
    page: UiPageContext,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_app_settings),
) -> HTMLResponse:
    locale, browser_session = page
    database_ok = (await session.scalar(select(func.count(Alarm.id)))) is not None
    states = [
        {"name": "Application", "status": "ok", "detail": __version__},
        {"name": "Database", "status": "ok" if database_ok else "error", "detail": "query"},
        {"name": "Redis", "status": "ok", "detail": "operator session"},
        {
            "name": "Simulation",
            "status": "enabled" if settings.simulation_enabled else "disabled",
            "detail": "SIMULATION_ENABLED",
        },
    ]
    return _html(
        request,
        "admin_system.html",
        locale,
        states=states,
        operator_name=browser_session.operator_name,
        logout_action="/admin/logout",
        csrf_token=browser_session.csrf_token,
    )


@router.get("/admin/simulation", response_class=HTMLResponse)
async def admin_simulation(
    request: Request,
    lang: str | None = Query(default=None),
    admin_session: str | None = Cookie(default=None),
    settings: Settings = Depends(get_app_settings),
) -> HTMLResponse:
    if not settings.simulation_enabled:
        raise HTTPException(status_code=404, detail="simulation_disabled")
    locale = _requested_locale(request, lang)
    browser_session = await _session_from_request(request, settings, admin_session, extend=True)
    notifications = get_mock_store().get_all()
    return _html(
        request,
        "admin_simulation.html",
        locale,
        notifications=notifications,
        clear_action="/admin/simulation/clear",
        operator_name=browser_session.operator_name,
        logout_action="/admin/logout",
        csrf_token=browser_session.csrf_token,
    )


@router.post("/admin/simulation/clear")
async def admin_simulation_clear(
    request: Request,
    csrf_token: str | None = Form(default=None),
    admin_session: str | None = Cookie(default=None),
    settings: Settings = Depends(get_app_settings),
) -> RedirectResponse:
    if not settings.simulation_enabled:
        raise HTTPException(status_code=404, detail="simulation_disabled")
    await _action_session(request, settings, admin_session, csrf_token)
    get_mock_store().clear()
    return RedirectResponse("/admin/simulation", status_code=303)
