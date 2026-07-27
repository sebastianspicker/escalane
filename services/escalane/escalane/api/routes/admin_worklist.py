"""Worklist dashboard, revision polling, and related query helpers."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from html import escape
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Cookie, Depends, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import String, and_, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from escalane.api.admin_session import pop_flash
from escalane.api.deps import get_app_settings, get_redis, get_session
from escalane.api.i18n import SUPPORTED_LOCALES
from escalane.api.routes.admin_console import (
    _html,
    _requested_locale,
    _session_from_request,
)
from escalane.db.models import Alarm, AlarmStatus, Person, Room
from escalane.settings import Settings

router = APIRouter()


def _alarm_statement(status_filter: str | None, search: str | None):
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
    return stmt


def _sort_details(sort_by: str):
    sort_columns = {
        "status": Alarm.status,
        "severity": Alarm.severity,
        "created_at": Alarm.created_at,
    }
    sort_name = sort_by if sort_by in sort_columns else "created_at"
    return sort_name, sort_columns[sort_name]


def _cursor_comparison(sort_column, cursor_alarm: Alarm, sort_name: str, order: str):
    cursor_sort_value = getattr(cursor_alarm, sort_name)
    if order == "desc":
        return or_(
            sort_column < cursor_sort_value,
            and_(sort_column == cursor_sort_value, Alarm.id < cursor_alarm.id),
        )
    return or_(
        sort_column > cursor_sort_value,
        and_(sort_column == cursor_sort_value, Alarm.id > cursor_alarm.id),
    )


async def _apply_alarm_cursor(
    session: AsyncSession, stmt, sort_column, sort_name: str, order: str, cursor: uuid.UUID | None
):
    if cursor is None:
        return stmt
    cursor_row = (await session.execute(stmt.where(Alarm.id == cursor))).first()
    if cursor_row is None:
        return stmt
    return stmt.where(_cursor_comparison(sort_column, cursor_row[0], sort_name, order))


async def _alarm_query(
    session: AsyncSession,
    status_filter: str | None,
    search: str | None,
    sort_by: str,
    order: str,
    cursor: uuid.UUID | None,
    limit: int,
):
    stmt = _alarm_statement(status_filter, search)
    sort_name, sort_column = _sort_details(sort_by)
    stmt = await _apply_alarm_cursor(session, stmt, sort_column, sort_name, order, cursor)
    ordering = sort_column.desc() if order == "desc" else sort_column.asc()
    id_ordering = Alarm.id.desc() if order == "desc" else Alarm.id.asc()
    return stmt.order_by(ordering, id_ordering).limit(limit + 1)


def _next_page_url(request: Request, cursor: uuid.UUID | None) -> str | None:
    if cursor is None:
        return None
    query = [(key, value) for key, value in request.query_params.multi_items() if key != "cursor"]
    query.append(("cursor", str(cursor)))
    return f"{request.url.path}?{urlencode(query)}"


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
        "person": person or alarm.person_id or "-",
        "room": room or alarm.room_id or "-",
        "source": alarm.source,
        "severity": alarm.severity,
        "owner": alarm.acked_by,
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


# Render a filtered keyset-paginated worklist and its opaque change revision.
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
    statement = await _alarm_query(session, status_filter, search, sort_by, order, cursor, limit)
    result = list((await session.execute(statement)).all())
    page_rows = result[:limit]
    next_cursor = page_rows[-1][0].id if len(result) > limit and page_rows else None
    flash = await pop_flash(get_redis(request), browser_session)
    return _html(
        request,
        "admin_worklist.html",
        locale,
        persist_locale=lang in SUPPORTED_LOCALES,
        alarms=[_worklist_row(alarm, person, room, locale) for alarm, person, room in page_rows],
        counts=await _counts(session),
        statuses=[item.value for item in AlarmStatus],
        filters={
            "status": status_filter or "",
            "search": search or "",
            "sort_by": sort_by,
            "order": order,
        },
        poll_url=f"/admin/revision?lang={locale}",
        poll_interval=15,
        revision=await _revision(session),
        next_page_url=_next_page_url(request, next_cursor),
        operator_name=browser_session.operator_name,
        logout_action="/admin/logout",
        csrf_token=browser_session.csrf_token,
        flash=flash,
        simulation_enabled=settings.simulation_enabled,
    )


# Expose only an opaque revision so polling never leaks alarm data.
@router.get("/admin/revision")
async def admin_revision(
    request: Request,
    admin_session: str | None = Cookie(default=None),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_app_settings),
) -> JSONResponse:
    await _session_from_request(request, settings, admin_session, extend=False)
    return JSONResponse({"revision": await _revision(session)})


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
