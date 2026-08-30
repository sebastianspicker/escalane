"""List, filter, paginate, export, and retrieve alarms for administrative clients."""

from __future__ import annotations

import csv
import io
import json
import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import StreamingResponse
from pydantic import Field
from sqlalchemy import Select, and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from escalane.persistence.models import Alarm
from escalane.web.deps import get_session, require_admin
from escalane.web.schemas import AlarmFilterIn, AlarmOut, ExportFormat

_CSV_FORMULA_CHARS = frozenset("=+-@\t\r")
_EXPORT_TIMESTAMP_FORMAT = "%Y%m%d_%H%M%S"


def _sanitize_csv_value(value: Any) -> Any:
    """Prevent CSV formula injection by prefixing dangerous characters."""
    if isinstance(value, str) and value and value[0] in _CSV_FORMULA_CHARS:
        return f"'{value}"
    return value


router = APIRouter(prefix="/v1/alarms", dependencies=[Depends(require_admin)])


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


AlarmFilters = AlarmFilterIn


# Validate list filters, keyset cursor, sort order, and page size without
# changing the generated request-schema description.
class AlarmListQuery(AlarmFilterIn):
    limit: int = Field(default=50, ge=1, le=200)
    cursor: uuid.UUID | None = None
    sort_by: SortField = SortField.CREATED_AT
    sort_order: SortOrder = SortOrder.DESC


# Validate bounded export filters and serialization format without adding
# generated API-schema metadata.
class AlarmExportQuery(AlarmFilterIn):
    format: ExportFormat = ExportFormat.JSON
    limit: int = Field(default=1000, ge=1, le=2000)


def _apply_alarm_filters(stmt: Select, filters: AlarmFilters) -> Select:
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


async def _apply_cursor_filter(
    stmt: Select,
    *,
    session: AsyncSession,
    cursor: uuid.UUID | None,
    sort_by: SortField,
    sort_order: SortOrder,
) -> Select:
    """Apply a keyset cursor only when its row still matches the active filters."""
    if cursor is None:
        return stmt
    cursor_alarm = await session.scalar(stmt.where(Alarm.id == cursor))
    if not cursor_alarm:
        return stmt
    sort_column = _SORT_COLUMNS[sort_by]
    cursor_sort_value = getattr(cursor_alarm, sort_by.value)
    if sort_order == SortOrder.DESC:
        return stmt.where(
            or_(
                sort_column < cursor_sort_value,
                and_(
                    sort_column == cursor_sort_value,
                    Alarm.id < cursor_alarm.id,
                ),
            )
        )
    return stmt.where(
        or_(
            sort_column > cursor_sort_value,
            and_(
                sort_column == cursor_sort_value,
                Alarm.id > cursor_alarm.id,
            ),
        )
    )


def _apply_alarm_sort(stmt: Select, sort_by: SortField, sort_order: SortOrder) -> Select:
    sort_column = _SORT_COLUMNS[sort_by]
    if sort_order == SortOrder.DESC:
        return stmt.order_by(sort_column.desc(), Alarm.id.desc())
    return stmt.order_by(sort_column.asc(), Alarm.id.asc())


def _alarm_filters_from_query(query: AlarmListQuery | AlarmExportQuery) -> AlarmFilters:
    return AlarmFilters(
        status=query.status,
        severity=query.severity,
        person_id=query.person_id,
        room_id=query.room_id,
        site_id=query.site_id,
        device_id=query.device_id,
        source=query.source,
        created_after=query.created_after,
        created_before=query.created_before,
    )


def _export_filename(extension: str) -> str:
    timestamp = datetime.now(UTC).strftime(_EXPORT_TIMESTAMP_FORMAT)
    return f"alarms_export_{timestamp}.{extension}"


def _json_export_content(alarms: list[Alarm]) -> str:
    data = [
        AlarmOut.model_validate(alarm, from_attributes=True).model_dump(mode="json")
        for alarm in alarms
    ]
    return json.dumps(data, indent=2, default=str)


def _csv_export_content(alarms: list[Alarm]) -> str:
    output = io.StringIO()
    if not alarms:
        return output.getvalue()

    field_names = [
        "id",
        "status",
        "source",
        "event",
        "created_at",
        "person_id",
        "room_id",
        "site_id",
        "device_id",
        "severity",
        "silent",
        "zammad_ticket_id",
        "acked_at",
        "acked_by",
        "resolved_at",
        "resolved_by",
        "cancelled_at",
        "cancelled_by",
    ]
    writer = csv.DictWriter(output, fieldnames=field_names, extrasaction="ignore")
    writer.writeheader()
    for alarm in alarms:
        writer.writerow(_csv_export_row(alarm, field_names))
    return output.getvalue()


def _csv_export_row(alarm: Alarm, field_names: list[str]) -> dict[str, Any]:
    row = {name: getattr(alarm, name, None) for name in field_names}
    for dt_field in ["created_at", "acked_at", "resolved_at", "cancelled_at"]:
        dt_val = row[dt_field]
        if dt_val is not None and hasattr(dt_val, "isoformat"):
            row[dt_field] = dt_val.isoformat()
    return {key: _sanitize_csv_value(value) for key, value in row.items()}


@router.get("", response_model=list[AlarmOut])
async def list_alarms(
    response: Response,
    query: Annotated[AlarmListQuery, Query()],
    session: AsyncSession = Depends(get_session),
) -> list[AlarmOut]:
    """List alarms with filtering, pagination, and sorting."""
    stmt = select(Alarm).where(Alarm.deleted_at.is_(None))

    filters = _alarm_filters_from_query(query)
    stmt = _apply_alarm_filters(stmt, filters)
    stmt = await _apply_cursor_filter(
        stmt,
        session=session,
        cursor=query.cursor,
        sort_by=query.sort_by,
        sort_order=query.sort_order,
    )
    stmt = _apply_alarm_sort(stmt, query.sort_by, query.sort_order).limit(query.limit + 1)
    alarms = list((await session.scalars(stmt)).all())

    has_more = len(alarms) > query.limit
    page = alarms[: query.limit]
    if has_more and page:
        response.headers["X-Next-Cursor"] = str(page[-1].id)

    return [AlarmOut.model_validate(alarm, from_attributes=True) for alarm in page]


@router.get("/export")
async def export_alarms(
    query: Annotated[AlarmExportQuery, Query()],
    session: AsyncSession = Depends(get_session),
) -> StreamingResponse:
    """Export alarms in JSON or CSV format."""
    stmt = select(Alarm).where(Alarm.deleted_at.is_(None))

    filters = _alarm_filters_from_query(query)
    stmt = _apply_alarm_filters(stmt, filters)

    stmt = stmt.order_by(Alarm.created_at.desc()).limit(query.limit)
    alarms = list((await session.scalars(stmt)).all())

    if query.format == ExportFormat.JSON:
        content = _json_export_content(alarms)
        media_type = "application/json"
        filename = _export_filename("json")
    else:
        content = _csv_export_content(alarms)
        media_type = "text/csv"
        filename = _export_filename("csv")

    return StreamingResponse(
        iter([content]),
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/stats")
async def alarm_stats(
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Get alarm statistics."""
    status_counts = (
        await session.execute(
            select(Alarm.status, func.count(Alarm.id))
            .where(Alarm.deleted_at.is_(None))
            .group_by(Alarm.status)
        )
    ).all()

    severity_counts = (
        await session.execute(
            select(Alarm.severity, func.count(Alarm.id))
            .where(Alarm.deleted_at.is_(None))
            .group_by(Alarm.severity)
        )
    ).all()

    total = await session.scalar(select(func.count(Alarm.id)).where(Alarm.deleted_at.is_(None)))

    return {
        "total": total or 0,
        "by_status": {str(s): c for s, c in status_counts},
        "by_severity": {s: c for s, c in severity_counts},
    }
