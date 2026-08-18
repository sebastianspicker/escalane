"""Pure payload builders used by the notification service facade."""

from __future__ import annotations

from typing import Any

from escalane import constants
from escalane.types import EnrichedAlarmContext, NotificationPayload


def priority_for_severity(severity: str) -> int:
    """Map an alarm severity to an external-system priority ID."""
    priority_map = {
        constants.PRIORITY_CRITICAL: 3,
        constants.PRIORITY_HIGH: 2,
        constants.PRIORITY_MEDIUM: 2,
        constants.PRIORITY_LOW: 1,
    }
    return priority_map.get(severity, 3)


def build_title(enriched: EnrichedAlarmContext, step_no: int) -> str:
    """Build the notification title for an escalation step."""
    person = enriched.get("person_name", "Unknown")
    room = enriched.get("room_label", "Unknown")

    if step_no == 0:
        return f"NOTFALLALARM - {person} - {room}"
    return f"ESKALATION Stufe {step_no} - {person} - {room}"


def build_tags(step_no: int, severity: str) -> list[str]:
    """Build notification tags from the escalation step and severity."""
    tags = []
    if step_no == 0:
        tags.append(constants.TAG_EMERGENCY)
    if severity == constants.PRIORITY_CRITICAL:
        tags.append(constants.TAG_SILENT)
    return tags


def build_zammad_ticket_payload(payload: NotificationPayload, zammad_config: Any) -> dict[str, Any]:
    """Build the Zammad ticket request from a notification payload."""
    return {
        "title": payload["title"],
        "group": zammad_config.group,
        "priority_id": payload["priority"],
        "state_id": zammad_config.state_id_new,
        "customer_id": zammad_config.customer,
        "tags": payload["tags"],
        "article": {
            "subject": "Alarm ausgelöst (silent)",
            "body": payload["body"],
            "type": "note",
            "internal": True,
        },
    }
