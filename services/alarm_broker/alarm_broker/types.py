"""Typed dictionaries for structured internal data."""

from __future__ import annotations

from typing import TypedDict


class EnrichedAlarmContext(TypedDict):
    """Context returned by enrich_alarm_context()."""

    person_name: str | None
    room_label: str | None
    site_name: str | None
    severity: str


class NotificationPayload(TypedDict):
    """Payload built by NotificationService._build_notification_payload()."""

    title: str
    body: str
    tags: list[str]
    priority: int
    step_no: int
    alarm_id: str
