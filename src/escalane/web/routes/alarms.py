"""List, filter, paginate, export, and retrieve alarms for administrative clients."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import StreamingResponse
from pydantic import Field
from sqlalchemy.ext.asyncio import AsyncSession

from escalane.alarms.queries import (
    AlarmFilters,
    SortField,
    SortOrder,
    alarm_statistics,
    list_alarm_page,
    list_alarms_for_export,
)
from escalane.web.deps import get_session, require_admin
from escalane.web.exports import alarm_export_response
from escalane.web.schemas import AlarmFilterIn, AlarmOut, ExportFormat

router = APIRouter(prefix="/v1/alarms", dependencies=[Depends(require_admin)])


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


def _alarm_filters_from_query(query: AlarmFilterIn) -> AlarmFilters:
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


@router.get("", response_model=list[AlarmOut])
async def list_alarms(
    response: Response,
    query: Annotated[AlarmListQuery, Query()],
    session: AsyncSession = Depends(get_session),
) -> list[AlarmOut]:
    """List alarms with filtering, pagination, and sorting."""
    page = await list_alarm_page(
        session,
        _alarm_filters_from_query(query),
        cursor=query.cursor,
        sort_by=query.sort_by,
        sort_order=query.sort_order,
        limit=query.limit,
    )
    if page.next_cursor is not None:
        response.headers["X-Next-Cursor"] = str(page.next_cursor)

    return [AlarmOut.model_validate(alarm, from_attributes=True) for alarm in page.alarms]


@router.get("/export")
async def export_alarms(
    query: Annotated[AlarmExportQuery, Query()],
    session: AsyncSession = Depends(get_session),
) -> StreamingResponse:
    """Export alarms in JSON or CSV format."""
    alarms = await list_alarms_for_export(
        session, _alarm_filters_from_query(query), limit=query.limit
    )
    return alarm_export_response(alarms, query.format)


@router.get("/stats")
async def alarm_stats(
    session: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    """Get alarm statistics."""
    stats = await alarm_statistics(session)
    return {
        "total": stats.total,
        "by_status": stats.by_status,
        "by_severity": stats.by_severity,
    }
