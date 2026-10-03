"""Render alarm exports as downloadable JSON or formula-safe CSV responses."""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from fastapi.responses import StreamingResponse

from escalane.persistence.models import Alarm
from escalane.web.schemas import AlarmOut, ExportFormat

_CSV_FORMULA_CHARS = frozenset("=+-@\t\r")
_EXPORT_TIMESTAMP_FORMAT = "%Y%m%d_%H%M%S"
_CSV_FIELD_NAMES = [
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


def _sanitize_csv_value(value: Any) -> Any:
    """Prevent CSV formula injection by prefixing dangerous characters."""
    if isinstance(value, str) and value and value[0] in _CSV_FORMULA_CHARS:
        return f"'{value}"
    return value


def _export_filename(extension: str) -> str:
    timestamp = datetime.now(UTC).strftime(_EXPORT_TIMESTAMP_FORMAT)
    return f"alarms_export_{timestamp}.{extension}"


def _json_export_content(alarms: Sequence[Alarm]) -> str:
    data = [
        AlarmOut.model_validate(alarm, from_attributes=True).model_dump(mode="json")
        for alarm in alarms
    ]
    return json.dumps(data, indent=2, default=str)


def _csv_export_content(alarms: Sequence[Alarm]) -> str:
    output = io.StringIO()
    if not alarms:
        return output.getvalue()

    writer = csv.DictWriter(output, fieldnames=_CSV_FIELD_NAMES, extrasaction="ignore")
    writer.writeheader()
    for alarm in alarms:
        writer.writerow(_csv_export_row(alarm))
    return output.getvalue()


def _csv_export_row(alarm: Alarm) -> dict[str, Any]:
    row = {name: getattr(alarm, name, None) for name in _CSV_FIELD_NAMES}
    for dt_field in ["created_at", "acked_at", "resolved_at", "cancelled_at"]:
        dt_val = row[dt_field]
        if dt_val is not None and hasattr(dt_val, "isoformat"):
            row[dt_field] = dt_val.isoformat()
    return {key: _sanitize_csv_value(value) for key, value in row.items()}


def alarm_export_response(
    alarms: Sequence[Alarm], export_format: ExportFormat
) -> StreamingResponse:
    """Stream alarms as a JSON or CSV attachment with a timestamped filename."""
    if export_format == ExportFormat.JSON:
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
