"""Server-rendered, session-authenticated operator console."""

from __future__ import annotations

import html
import secrets
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Cookie, Depends, Form, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from sqlalchemy import String, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from alarm_broker import __version__
from alarm_broker.api.admin_session import (
    SESSION_COOKIE,
    SESSION_TTL_SECONDS,
    AdminSession,
    create_admin_session,
    destroy_admin_session,
    pop_flash,
    require_admin_session,
    validate_admin_csrf,
)
from alarm_broker.api.deps import (
    get_app_settings,
    get_client_ip,
    get_redis,
    get_session,
    is_secure_request,
)
from alarm_broker.api.i18n import SUPPORTED_LOCALES, normalise_locale, translation_context
from alarm_broker.api.templating import render_template
from alarm_broker.connectors.mock import get_mock_store
from alarm_broker.core.rate_limit import minute_bucket, rate_limit_key
from alarm_broker.db.models import (
    AdminAuditEvent,
    Alarm,
    AlarmStatus,
    Person,
    Room,
)
from alarm_broker.settings import Settings

router = APIRouter()
_FAILED_LOGIN_LIMIT = 5
_FAILED_LOGIN_WINDOW_SECONDS = 60


def escape(value: str) -> str:
    """Compatibility helper retained for callers that import it."""
    return html.escape(value, quote=True)


def _failed_login_key(request: Request, settings: Settings) -> str:
    return rate_limit_key(
        f"admin-login:{get_client_ip(request, settings)}",
        minute_bucket(),
    )


def _requested_locale(request: Request, explicit: str | None) -> str:
    if explicit in SUPPORTED_LOCALES:
        return explicit
    persisted = request.cookies.get("ui_locale")
    if persisted in SUPPORTED_LOCALES:
        return persisted
    return normalise_locale(request.headers.get("accept-language"))


def _base_context(request: Request, locale: str, **values: Any) -> dict[str, Any]:
    return {
        **translation_context(locale),
        "current_path": request.url.path,
        "worklist_url": f"/admin?lang={locale}",
        "asset_url": "/admin/assets/ui.css",
        "script_url": "/admin/assets/ui.js",
        **values,
    }


def _html(
    request: Request,
    template: str,
    locale: str,
    *,
    status_code: int = 200,
    persist_locale: bool = False,
    **context: Any,
) -> HTMLResponse:
    response = HTMLResponse(
        render_template(template, **_base_context(request, locale, **context)),
        status_code=status_code,
    )
    if persist_locale:
        response.set_cookie("ui_locale", locale, max_age=31_536_000, samesite="lax")
    return response


def _login_error(locale: str, kind: str) -> str:
    messages = {
        "en": {
            "invalid": "The admin key is not valid.",
            "rate": "Too many failed attempts. Wait one minute and try again.",
            "config": "Administrator login is not configured.",
        },
        "de": {
            "invalid": "Der Admin-Schlüssel ist nicht gültig.",
            "rate": "Zu viele fehlgeschlagene Versuche. Bitte warten Sie eine Minute.",
            "config": "Die Administrator-Anmeldung ist nicht konfiguriert.",
        },
    }
    return messages[locale][kind]


async def _session_from_request(
    request: Request,
    settings: Settings,
    token: str | None,
    *,
    extend: bool,
) -> AdminSession:
    return await require_admin_session(get_redis(request), settings, token, extend=extend)


@router.get("/admin/login", response_class=HTMLResponse)
async def admin_login_page(
    request: Request, lang: str | None = Query(default=None)
) -> HTMLResponse:
    locale = _requested_locale(request, lang)
    return _html(
        request,
        "admin_login.html",
        locale,
        persist_locale=lang in SUPPORTED_LOCALES,
        login_action=f"/admin/login?lang={locale}",
        error=None,
    )


@router.post("/admin/login", response_class=HTMLResponse)
async def admin_login_submit(
    request: Request,
    admin_key: str = Form(...),
    operator_name: str = Form(default="Admin", max_length=120),
    lang: str | None = Query(default=None),
    settings: Settings = Depends(get_app_settings),
) -> Response:
    locale = _requested_locale(request, lang)
    if not settings.admin_api_key:
        return _html(
            request,
            "admin_login.html",
            locale,
            status_code=500,
            login_action=f"/admin/login?lang={locale}",
            error=_login_error(locale, "config"),
        )

    redis = get_redis(request)
    failure_key = _failed_login_key(request, settings)
    attempts = await redis.get(failure_key)
    if attempts is not None and int(attempts) >= _FAILED_LOGIN_LIMIT:
        return _html(
            request,
            "admin_login.html",
            locale,
            status_code=429,
            login_action=f"/admin/login?lang={locale}",
            error=_login_error(locale, "rate"),
        )
    if not secrets.compare_digest(admin_key, settings.admin_api_key):
        count = await redis.incr(failure_key)
        if count == 1:
            await redis.expire(failure_key, _FAILED_LOGIN_WINDOW_SECONDS)
        return _html(
            request,
            "admin_login.html",
            locale,
            status_code=401,
            login_action=f"/admin/login?lang={locale}",
            error=_login_error(locale, "invalid"),
        )

    await redis.delete(failure_key)
    named = operator_name.strip() or "Admin"
    browser_session = await create_admin_session(redis, settings, named)
    response = RedirectResponse(f"/admin?lang={locale}", status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(
        SESSION_COOKIE,
        browser_session.token,
        httponly=True,
        secure=is_secure_request(request, settings),
        samesite="strict",
        max_age=SESSION_TTL_SECONDS,
    )
    response.set_cookie("ui_locale", locale, max_age=31_536_000, samesite="lax")
    return response


@router.post("/admin/logout")
async def admin_logout(
    request: Request,
    csrf_token: str | None = Form(default=None),
    admin_session: str | None = Cookie(default=None),
    settings: Settings = Depends(get_app_settings),
) -> Response:
    browser_session = await _session_from_request(request, settings, admin_session, extend=False)
    validate_admin_csrf(browser_session, csrf_token)
    await destroy_admin_session(get_redis(request), admin_session)
    response = RedirectResponse("/admin/login", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(SESSION_COOKIE)
    return response


@router.post("/admin/session/extend")
async def admin_extend_session(
    request: Request,
    csrf_token: str | None = Form(default=None),
    admin_session: str | None = Cookie(default=None),
    settings: Settings = Depends(get_app_settings),
) -> RedirectResponse:
    browser_session = await _session_from_request(request, settings, admin_session, extend=True)
    validate_admin_csrf(browser_session, csrf_token)
    return RedirectResponse("/admin", status_code=status.HTTP_303_SEE_OTHER)


def _alarm_query(
    status_filter: str | None,
    search: str | None,
    sort_by: str,
    order: str,
    cursor: uuid.UUID | None,
    limit: int,
):
    stmt = (
        select(Alarm, Person.display_name, Room.label)
        .outerjoin(Person, Person.id == Alarm.person_id)
        .outerjoin(Room, Room.id == Alarm.room_id)
        .where(Alarm.deleted_at.is_(None))
    )
    if status_filter in {item.value for item in AlarmStatus}:
        stmt = stmt.where(Alarm.status == AlarmStatus(status_filter))
    if search:
        pattern = f"%{search.strip()}%"
        stmt = stmt.where(
            or_(
                cast(Alarm.id, String).ilike(pattern),
                Alarm.source.ilike(pattern),
                Alarm.event.ilike(pattern),
                Person.display_name.ilike(pattern),
                Room.label.ilike(pattern),
            )
        )
    if cursor is not None:
        stmt = stmt.where(Alarm.id < cursor if order == "desc" else Alarm.id > cursor)
    sort_columns = {
        "status": Alarm.status,
        "severity": Alarm.severity,
        "created_at": Alarm.created_at,
    }
    sort_column = sort_columns.get(sort_by, Alarm.created_at)
    ordering = sort_column.desc() if order == "desc" else sort_column.asc()
    id_ordering = Alarm.id.desc() if order == "desc" else Alarm.id.asc()
    return stmt.order_by(ordering, id_ordering).limit(limit + 1)


async def _counts(session: AsyncSession) -> dict[str, int]:
    rows = (
        await session.execute(
            select(Alarm.status, func.count(Alarm.id))
            .where(Alarm.deleted_at.is_(None))
            .group_by(Alarm.status)
        )
    ).all()
    counts = {item.value: 0 for item in AlarmStatus}
    counts.update({state.value: int(count) for state, count in rows})
    return counts


def _display_time(value: datetime) -> str:
    aware = value if value.tzinfo else value.replace(tzinfo=UTC)
    minutes = max(0, int((datetime.now(UTC) - aware).total_seconds() // 60))
    return f"{minutes} min" if minutes < 60 else f"{minutes // 60} h {minutes % 60} min"


def _worklist_row(
    alarm: Alarm, person: str | None, room: str | None, locale: str
) -> dict[str, Any]:
    return {
        "id": str(alarm.id),
        "short_id": str(alarm.id)[:8],
        "status": alarm.status.value,
        "created_at": _display_time(alarm.created_at),
        "created_at_iso": alarm.created_at.isoformat(),
        "person": person or alarm.person_id or "—",
        "room": room or alarm.room_id or "—",
        "source": alarm.source,
        "severity": alarm.severity,
        "detail_url": f"/admin/alarms/{alarm.id}?lang={locale}",
    }


async def _revision(session: AsyncSession) -> str:
    rows = (
        await session.execute(
            select(
                func.count(Alarm.id),
                func.max(Alarm.created_at),
                func.max(Alarm.acked_at),
                func.max(Alarm.resolved_at),
                func.max(Alarm.cancelled_at),
            ).where(Alarm.deleted_at.is_(None))
        )
    ).one()
    return uuid.uuid5(uuid.NAMESPACE_OID, "|".join(str(value or "") for value in rows)).hex


@router.get("/admin", response_class=HTMLResponse)
async def admin_dashboard(
    request: Request,
    status_filter: str | None = Query(default=None, alias="status"),
    search: str | None = Query(default=None, max_length=120),
    sort_by: str = Query(default="created_at", pattern="^(created_at|status|severity)$"),
    order: str = Query(default="desc", pattern="^(asc|desc)$"),
    cursor: uuid.UUID | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    lang: str | None = Query(default=None),
    admin_session: str | None = Cookie(default=None),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_app_settings),
) -> HTMLResponse:
    locale = _requested_locale(request, lang)
    browser_session = await _session_from_request(request, settings, admin_session, extend=True)
    statement = _alarm_query(status_filter, search, sort_by, order, cursor, limit)
    result = list((await session.execute(statement)).all())
    page_rows = result[:limit]
    next_cursor = str(page_rows[-1][0].id) if len(result) > limit and page_rows else None
    flash = await pop_flash(get_redis(request), browser_session)
    return _html(
        request,
        "admin_worklist.html",
        locale,
        persist_locale=lang in SUPPORTED_LOCALES,
        alarms=[_worklist_row(alarm, person, room, locale) for alarm, person, room in page_rows],
        counts=await _counts(session),
        statuses=[item.value for item in AlarmStatus],
        filters={"status": status_filter or "", "search": search or ""},
        poll_url=f"/admin/revision?lang={locale}",
        poll_interval=15,
        revision=await _revision(session),
        next_cursor=next_cursor,
        operator_name=browser_session.operator_name,
        logout_action="/admin/logout",
        csrf_token=browser_session.csrf_token,
        flash=flash,
        simulation_enabled=settings.simulation_enabled,
    )


@router.get("/admin/revision")
async def admin_revision(
    request: Request,
    admin_session: str | None = Cookie(default=None),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_app_settings),
) -> JSONResponse:
    await _session_from_request(request, settings, admin_session, extend=False)
    return JSONResponse({"revision": await _revision(session)})


async def _action_session(
    request: Request,
    settings: Settings,
    token: str | None,
    csrf_token: str | None,
) -> AdminSession:
    browser_session = await _session_from_request(request, settings, token, extend=True)
    validate_admin_csrf(browser_session, csrf_token)
    return browser_session


@router.get("/admin/activity", response_class=HTMLResponse)
async def admin_activity(
    request: Request,
    lang: str | None = Query(default=None),
    admin_session: str | None = Cookie(default=None),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_app_settings),
) -> HTMLResponse:
    locale = _requested_locale(request, lang)
    browser_session = await _session_from_request(request, settings, admin_session, extend=True)
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
    lang: str | None = Query(default=None),
    admin_session: str | None = Cookie(default=None),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_app_settings),
) -> HTMLResponse:
    locale = _requested_locale(request, lang)
    browser_session = await _session_from_request(request, settings, admin_session, extend=True)
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


def _render_alarm_row(alarm: Alarm) -> str:
    """Legacy unit-test seam; the live worklist is rendered by Jinja."""
    alarm_id = str(alarm.id)
    can_ack = alarm.status == AlarmStatus.TRIGGERED
    can_resolve = alarm.status in {AlarmStatus.TRIGGERED, AlarmStatus.ACKNOWLEDGED}
    disabled_ack = " disabled" if not can_ack else ""
    disabled_resolve = " disabled" if not can_resolve else ""
    return (
        f"<tr data-alarm-id='{escape(alarm_id)}' "
        f"data-person='{escape(str(alarm.person_id or '-'))}' "
        f"data-room='{escape(str(alarm.room_id or '-'))}' data-source='{escape(alarm.source)}' "
        f"data-severity='{escape(alarm.severity)}' "
        f"data-acked-by='{escape(str(alarm.acked_by or '-'))}' "
        f"data-can-ack='{'true' if can_ack else 'false'}' "
        f"data-can-resolve='{'true' if can_resolve else 'false'}'>"
        f"<td>{escape(alarm.status.value)}</td><td>{escape(alarm_id[:8])}</td>"
        f"<td><button class='quick-ack-btn'{disabled_ack}>Quick Ack</button>"
        f"<button class='quick-resolve-btn'{disabled_resolve}>Quick Resolve</button></td></tr>"
    )
