"""Jinja context construction for the responder acknowledgement page."""

from __future__ import annotations

from typing import Any

from escalane.contracts.alarms import AlarmStatus
from escalane.contracts.notifications import EnrichedAlarmContext
from escalane.persistence.models import Alarm
from escalane.web.i18n import translation_context
from escalane.web.templating import render_template


def render_ack_page(
    alarm: Alarm,
    enriched: EnrichedAlarmContext,
    *,
    ack_action: str,
    locale: str,
    csrf_token: str = "",
    error: str | None = None,
    values: dict[str, str] | None = None,
) -> str:
    """Render a capability-link ACK page without exposing hidden alarm fields to the browser."""
    context: dict[str, Any] = {
        **translation_context(locale),
        "asset_url": "/admin/assets/ui.css",
        "script_url": "/admin/assets/ui.js",
        "current_path": ack_action,
        "alarm": {
            "id": str(alarm.id),
            "status": alarm.status.value,
            "person": enriched.get("person_name") or alarm.person_id or "-",
            "room": enriched.get("room_label") or alarm.room_id or "-",
            "created_at": alarm.created_at.isoformat(timespec="minutes"),
            "created_at_iso": alarm.created_at.isoformat(),
            "can_ack": alarm.status == AlarmStatus.TRIGGERED,
        },
        "ack_action": ack_action,
        "csrf_token": csrf_token,
        "error": error,
        "values": values or {},
    }
    return render_template("ack.html", **context)
