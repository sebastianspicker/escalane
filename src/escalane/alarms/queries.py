"""Read-only alarm queries: filtered keyset pages, exports, statistics, and display labels."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import Select, String, and_, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from escalane.alarms.dashboard import visible_counts
from escalane.alarms.severity import PRIORITY_CRITICAL, PRIORITY_HIGH, PRIORITY_MEDIUM
from escalane.persistence.models import Alarm, AlarmStatus, Person, Room

# Severities the operator console offers as worklist and export filters.
CONSOLE_SEVERITY_FILTERS = (PRIORITY_CRITICAL, PRIORITY_HIGH, PRIORITY_MEDIUM)


class SortOrder(StrEnum):
    """Sort order for alarm listing."""

    DESC = "desc"
    ASC = "asc"


class SortField(StrEnum):
    """Fields available for sorting alarms."""

    CREATED_AT = "created_at"
    STATUS = "status"
    SEVERITY = "severity"


_SORT_COLUMNS = {
    SortField.CREATED_AT: Alarm.created_at,
    SortField.STATUS: Alarm.status,
    SortField.SEVERITY: Alarm.severity,
}


@dataclass(frozen=True, slots=True)
class AlarmFilters:
    """Optional exact-match and creation-window filters shared by list and export reads."""

    status: AlarmStatus | None = None
    severity: str | None = None
    person_id: str | None = None
    room_id: str | None = None
    site_id: str | None = None
    device_id: str | None = None
    source: str | None = None
    created_after: datetime | None = None
    created_before: datetime | None = None


@dataclass(frozen=True, slots=True)
class AlarmPage:
    """One keyset page of alarms and the cursor for the next page, if any."""

    alarms: list[Alarm]
    next_cursor: uuid.UUID | None


@dataclass(frozen=True, slots=True)
class WorklistRow:
    """One console worklist alarm with its optional person and room labels."""

    alarm: Alarm
    person: str | None
    room: str | None


@dataclass(frozen=True, slots=True)
class WorklistPage:
    """One keyset page of worklist rows and the cursor for the next page, if any."""

    rows: list[WorklistRow]
    next_cursor: uuid.UUID | None


@dataclass(frozen=True, slots=True)
class AlarmStatistics:
    """Counts of active alarms in total, by present status, and by severity."""

    total: int
    by_status: dict[str, int]
    by_severity: dict[str, int]


def _active_alarms() -> Select[Any]:
    return select(Alarm).where(Alarm.deleted_at.is_(None))


def _apply_alarm_filters(stmt: Select[Any], filters: AlarmFilters) -> Select[Any]:
    """Apply common filter parameters to an alarm query."""
    equality_filters = [
        (Alarm.status, filters.status),
        (Alarm.severity, filters.severity),
        (Alarm.person_id, filters.person_id),
        (Alarm.room_id, filters.room_id),
        (Alarm.site_id, filters.site_id),
        (Alarm.device_id, filters.device_id),
        (Alarm.source, filters.source),
    ]
    for column, value in equality_filters:
        if value is not None:
            stmt = stmt.where(column == value)

    if filters.created_after is not None:
        stmt = stmt.where(Alarm.created_at >= filters.created_after)

    if filters.created_before is not None:
        stmt = stmt.where(Alarm.created_at <= filters.created_before)

    return stmt


async def _apply_keyset_cursor(
    session: AsyncSession,
    stmt: Select[Any],
    *,
    cursor: uuid.UUID | None,
    sort_by: SortField,
    sort_order: SortOrder,
) -> Select[Any]:
    """Apply a keyset cursor only when its row still matches the active filters.

    ``stmt`` must select ``Alarm`` as its first column.
    """
    if cursor is None:
        return stmt
    cursor_row = (await session.execute(stmt.where(Alarm.id == cursor))).first()
    if cursor_row is None:
        return stmt
    cursor_alarm = cursor_row[0]
    sort_column = _SORT_COLUMNS[sort_by]
    cursor_sort_value = getattr(cursor_alarm, sort_by.value)
    if sort_order == SortOrder.DESC:
        return stmt.where(
            or_(
                sort_column < cursor_sort_value,
                and_(sort_column == cursor_sort_value, Alarm.id < cursor_alarm.id),
            )
        )
    return stmt.where(
        or_(
            sort_column > cursor_sort_value,
            and_(sort_column == cursor_sort_value, Alarm.id > cursor_alarm.id),
        )
    )


def _apply_alarm_sort(stmt: Select[Any], sort_by: SortField, sort_order: SortOrder) -> Select[Any]:
    sort_column = _SORT_COLUMNS[sort_by]
    if sort_order == SortOrder.DESC:
        return stmt.order_by(sort_column.desc(), Alarm.id.desc())
    return stmt.order_by(sort_column.asc(), Alarm.id.asc())


async def _keyset_rows(
    session: AsyncSession,
    stmt: Select[Any],
    *,
    cursor: uuid.UUID | None,
    sort_by: SortField,
    sort_order: SortOrder,
    limit: int,
) -> tuple[list[Any], uuid.UUID | None]:
    """Read ``limit`` rows after the cursor and the next cursor when more rows exist."""
    stmt = await _apply_keyset_cursor(
        session, stmt, cursor=cursor, sort_by=sort_by, sort_order=sort_order
    )
    stmt = _apply_alarm_sort(stmt, sort_by, sort_order).limit(limit + 1)
    rows = list((await session.execute(stmt)).all())
    page = rows[:limit]
    next_cursor = page[-1][0].id if len(rows) > limit and page else None
    return page, next_cursor


async def list_alarm_page(
    session: AsyncSession,
    filters: AlarmFilters,
    *,
    cursor: uuid.UUID | None,
    sort_by: SortField,
    sort_order: SortOrder,
    limit: int,
) -> AlarmPage:
    """Return one filtered, sorted keyset page of active alarms."""
    stmt = _apply_alarm_filters(_active_alarms(), filters)
    rows, next_cursor = await _keyset_rows(
        session, stmt, cursor=cursor, sort_by=sort_by, sort_order=sort_order, limit=limit
    )
    return AlarmPage(alarms=[row[0] for row in rows], next_cursor=next_cursor)


async def list_alarms_for_export(
    session: AsyncSession, filters: AlarmFilters, *, limit: int
) -> list[Alarm]:
    """Return at most ``limit`` filtered active alarms, newest first."""
    stmt = _apply_alarm_filters(_active_alarms(), filters)
    stmt = stmt.order_by(Alarm.created_at.desc()).limit(limit)
    return list((await session.scalars(stmt)).all())


def _worklist_statement(
    status_filter: str | None, severity_filter: str | None, search: str | None
) -> Select[Any]:
    """Build the labelled worklist query; unknown status or severity values are ignored."""
    stmt = (
        select(Alarm, Person.display_name, Room.label)
        .outerjoin(Person, Person.id == Alarm.person_id)
        .outerjoin(Room, Room.id == Alarm.room_id)
        .where(Alarm.deleted_at.is_(None))
    )
    if status_filter in {item.value for item in AlarmStatus}:
        stmt = stmt.where(Alarm.status == AlarmStatus(status_filter))
    if severity_filter in CONSOLE_SEVERITY_FILTERS:
        stmt = stmt.where(Alarm.severity == severity_filter)
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


async def list_worklist_page(
    session: AsyncSession,
    *,
    status_filter: str | None,
    severity_filter: str | None,
    search: str | None,
    sort_by: SortField,
    sort_order: SortOrder,
    cursor: uuid.UUID | None,
    limit: int,
) -> WorklistPage:
    """Return one console worklist page with person and room labels."""
    stmt = _worklist_statement(status_filter, severity_filter, search)
    rows, next_cursor = await _keyset_rows(
        session, stmt, cursor=cursor, sort_by=sort_by, sort_order=sort_order, limit=limit
    )
    return WorklistPage(
        rows=[WorklistRow(alarm, person, room) for alarm, person, room in rows],
        next_cursor=next_cursor,
    )


async def alarm_statistics(session: AsyncSession) -> AlarmStatistics:
    """Count active alarms in total, per status present, and per severity."""
    status_counts = await visible_counts(session)

    severity_counts = (
        await session.execute(
            select(Alarm.severity, func.count(Alarm.id))
            .where(Alarm.deleted_at.is_(None))
            .group_by(Alarm.severity)
        )
    ).all()

    total = await session.scalar(select(func.count(Alarm.id)).where(Alarm.deleted_at.is_(None)))

    return AlarmStatistics(
        total=total or 0,
        by_status={status: count for status, count in status_counts.items() if count > 0},
        by_severity={s: c for s, c in severity_counts},
    )


async def alarm_display_labels(
    session: AsyncSession, alarm_id: uuid.UUID
) -> tuple[str | None, str | None] | None:
    """Return the current person display name and room label for one alarm, if it exists."""
    labels = (
        await session.execute(
            select(Person.display_name, Room.label)
            .select_from(Alarm)
            .outerjoin(Person, Person.id == Alarm.person_id)
            .outerjoin(Room, Room.id == Alarm.room_id)
            .where(Alarm.id == alarm_id)
        )
    ).one_or_none()
    if labels is None:
        return None
    return labels[0], labels[1]
