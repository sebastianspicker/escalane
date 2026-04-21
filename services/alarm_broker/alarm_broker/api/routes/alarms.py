from __future__ import annotations

import csv
import io
import json
import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import StreamingResponse
from sqlalchemy import Select, and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from alarm_broker.api.deps import get_session, require_admin
from alarm_broker.api.schemas import AlarmOut, ExportFormat
from alarm_broker.db.models import Alarm, AlarmStatus

_CSV_FORMULA_CHARS = frozenset("=+-@\t\r")


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


def _apply_alarm_filters(
    stmt: Select,
    status: AlarmStatus | None = None,
    severity: str | None = None,
    person_id: str | None = None,
    room_id: str | None = None,
    site_id: str | None = None,
    device_id: str | None = None,
    source: str | None = None,
    created_after: datetime | None = None,
    created_before: datetime | None = None,
) -> Select:
    """Apply common filter parameters to an alarm query."""
    if status is not None:
        stmt = stmt.where(Alarm.status == status)

    if severity is not None:
        stmt = stmt.where(Alarm.severity == severity)

    if person_id is not None:
        stmt = stmt.where(Alarm.person_id == person_id)

    if room_id is not None:
        stmt = stmt.where(Alarm.room_id == room_id)

    if site_id is not None:
        stmt = stmt.where(Alarm.site_id == site_id)

    if device_id is not None:
        stmt = stmt.where(Alarm.device_id == device_id)

    if source is not None:
        stmt = stmt.where(Alarm.source == source)

    if created_after is not None:
        stmt = stmt.where(Alarm.created_at >= created_after)

    if created_before is not None:
        stmt = stmt.where(Alarm.created_at <= created_before)

    return stmt


@router.get("", response_model=list[AlarmOut])
async def list_alarms(
    response: Response,
    # Filtering
    status: AlarmStatus | None = None,
    severity: str | None = None,
    person_id: str | None = None,
    room_id: str | None = None,
    site_id: str | None = None,
    device_id: str | None = None,
    source: str | None = None,
    # Date range filtering
    created_after: datetime | None = None,
    created_before: datetime | None = None,
    # Pagination
    limit: int = Query(default=50, ge=1, le=200),
    cursor: uuid.UUID | None = None,
    # Sorting
    sort_by: SortField = SortField.CREATED_AT,
    sort_order: SortOrder = SortOrder.DESC,
    session: AsyncSession = Depends(get_session),
) -> list[AlarmOut]:
    """List alarms with filtering, pagination, and sorting."""
    stmt = select(Alarm).where(Alarm.deleted_at.is_(None))

    stmt = _apply_alarm_filters(
        stmt,
        status=status,
        severity=severity,
        person_id=person_id,
        room_id=room_id,
        site_id=site_id,
        device_id=device_id,
        source=source,
        created_after=created_after,
        created_before=created_before,
    )

    # Apply cursor pagination
    if cursor is not None:
        cursor_alarm = await session.get(Alarm, cursor)
        if cursor_alarm:
            if sort_order == SortOrder.DESC:
                stmt = stmt.where(
                    or_(
                        Alarm.created_at < cursor_alarm.created_at,
                        and_(
                            Alarm.created_at == cursor_alarm.created_at,
                            Alarm.id < cursor_alarm.id,
                        ),
                    )
                )
            else:
                stmt = stmt.where(
                    or_(
                        Alarm.created_at > cursor_alarm.created_at,
                        and_(
                            Alarm.created_at == cursor_alarm.created_at,
                            Alarm.id > cursor_alarm.id,
                        ),
                    )
                )

    # Apply sorting
    sort_column = getattr(Alarm, sort_by.value)
    if sort_order == SortOrder.DESC:
        stmt = stmt.order_by(sort_column.desc(), Alarm.id.desc())
    else:
        stmt = stmt.order_by(sort_column.asc(), Alarm.id.asc())

    # Apply limit
    stmt = stmt.limit(limit + 1)
    alarms = list((await session.scalars(stmt)).all())

    has_more = len(alarms) > limit
    page = alarms[:limit]
    if has_more and page:
        response.headers["X-Next-Cursor"] = str(page[-1].id)

    return [AlarmOut.model_validate(alarm, from_attributes=True) for alarm in page]


@router.get("/export")
async def export_alarms(
    # Filtering
    status: AlarmStatus | None = Query(default=None),
    severity: str | None = Query(default=None),
    person_id: str | None = Query(default=None),
    room_id: str | None = Query(default=None),
    site_id: str | None = Query(default=None),
    device_id: str | None = Query(default=None),
    source: str | None = Query(default=None),
    # Date range filtering
    created_after: datetime | None = Query(default=None),
    created_before: datetime | None = Query(default=None),
    # Export options
    format: ExportFormat = Query(default=ExportFormat.JSON),
    # TODO: implement true streaming export to avoid loading all records into memory
    limit: int = Query(default=1000, ge=1, le=2000),
    session: AsyncSession = Depends(get_session),
) -> StreamingResponse:
    """Export alarms in JSON or CSV format."""
    stmt = select(Alarm).where(Alarm.deleted_at.is_(None))

    stmt = _apply_alarm_filters(
        stmt,
        status=status,
        severity=severity,
        person_id=person_id,
        room_id=room_id,
        site_id=site_id,
        device_id=device_id,
        source=source,
        created_after=created_after,
        created_before=created_before,
    )

    stmt = stmt.order_by(Alarm.created_at.desc()).limit(limit)
    alarms = list((await session.scalars(stmt)).all())

    if format == ExportFormat.JSON:
        data = [
            AlarmOut.model_validate(alarm, from_attributes=True).model_dump(mode="json")
            for alarm in alarms
        ]
        content = json.dumps(data, indent=2, default=str)
        media_type = "application/json"
        filename = f"alarms_export_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}.json"
    else:
        output = io.StringIO()
        if alarms:
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
                row = {k: getattr(alarm, k, None) for k in field_names}
                for dt_field in ["created_at", "acked_at", "resolved_at", "cancelled_at"]:
                    dt_val = row[dt_field]
                    if dt_val is not None and hasattr(dt_val, "isoformat"):
                        row[dt_field] = dt_val.isoformat()
                row = {k: _sanitize_csv_value(v) for k, v in row.items()}
                writer.writerow(row)
        content = output.getvalue()
        media_type = "text/csv"
        filename = f"alarms_export_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}.csv"

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
