"""Worklist dashboard and revision-polling routes with their URL and row-rendering helpers."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Cookie, Depends, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from escalane.alarms.dashboard import dashboard_counts, dashboard_revision
from escalane.alarms.queries import (
    CONSOLE_SEVERITY_FILTERS,
    SortField,
    SortOrder,
    list_worklist_page,
)
from escalane.config.settings import Settings
from escalane.persistence.models import Alarm, AlarmStatus
from escalane.web.admin_session import pop_flash
from escalane.web.console import (
    render_page,
    requested_locale,
    session_from_request,
)
from escalane.web.deps import get_app_settings, get_redis, get_session
from escalane.web.i18n import SUPPORTED_LOCALES

router = APIRouter()
_CONSOLE_SEVERITY_PATTERN = f"^({'|'.join(CONSOLE_SEVERITY_FILTERS)})$"


def _next_page_url(request: Request, cursor: uuid.UUID | None) -> str | None:
    if cursor is None:
        return None
    query = [(key, value) for key, value in request.query_params.multi_items() if key != "cursor"]
    query.append(("cursor", str(cursor)))
    return f"{request.url.path}?{urlencode(query)}"


def _filter_url(request: Request, locale: str, **updates: str | None) -> str:
    """Build a fresh worklist URL without carrying a stale cursor into a new view."""
    query = [
        (key, value)
        for key, value in request.query_params.multi_items()
        if key not in {"cursor", *updates}
    ]
    for key, value in updates.items():
        if value:
            query.append((key, value))
    if not any(key == "lang" for key, _ in query):
        query.append(("lang", locale))
    return f"/admin?{urlencode(query)}"


def _export_url(status_filter: str | None, severity_filter: str | None, export_format: str) -> str:
    query = [("format", export_format)]
    if status_filter:
        query.append(("status", status_filter))
    if severity_filter:
        query.append(("severity", severity_filter))
    return f"/admin/export?{urlencode(query)}"


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
        "person": person or alarm.person_id or "-",
        "room": room or alarm.room_id or "-",
        "source": alarm.source,
        "severity": alarm.severity,
        "owner": alarm.acked_by,
        "detail_url": f"/admin/alarms/{alarm.id}?lang={locale}",
    }


async def _revision(session: AsyncSession) -> str:
    return await dashboard_revision(session)


@router.get("/admin", response_class=HTMLResponse)
async def admin_dashboard(
    request: Request,
    status_filter: str | None = Query(default=None, alias="status"),
    severity_filter: str | None = Query(
        default=None, alias="severity", pattern=_CONSOLE_SEVERITY_PATTERN
    ),
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
    locale = requested_locale(request, lang)
    browser_session = await session_from_request(request, settings, admin_session, extend=True)
    page_revision = await _revision(session)
    page = await list_worklist_page(
        session,
        status_filter=status_filter,
        severity_filter=severity_filter,
        search=search,
        sort_by=SortField(sort_by),
        sort_order=SortOrder(order),
        cursor=cursor,
        limit=limit,
    )
    flash = await pop_flash(get_redis(request), browser_session)
    return render_page(
        request,
        "admin_worklist.html",
        locale,
        persist_locale=lang in SUPPORTED_LOCALES,
        alarms=[_worklist_row(row.alarm, row.person, row.room, locale) for row in page.rows],
        counts=await dashboard_counts(session, get_redis(request)),
        statuses=[item.value for item in AlarmStatus],
        filters={
            "status": status_filter or "",
            "severity": severity_filter or "",
            "search": search or "",
            "sort_by": sort_by,
            "order": order,
        },
        status_urls={
            status: _filter_url(request, locale, status=status)
            for status in (item.value for item in AlarmStatus)
        },
        severity_urls={
            severity: _filter_url(request, locale, severity=severity)
            for severity in CONSOLE_SEVERITY_FILTERS
        },
        all_severities_url=_filter_url(request, locale, severity=None),
        export_csv_url=_export_url(status_filter, severity_filter, "csv"),
        export_json_url=_export_url(status_filter, severity_filter, "json"),
        poll_url=f"/admin/revision?lang={locale}",
        poll_interval=15,
        revision=page_revision,
        next_page_url=_next_page_url(request, page.next_cursor),
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
    await session_from_request(request, settings, admin_session, extend=False)
    return JSONResponse({"revision": await _revision(session)})
