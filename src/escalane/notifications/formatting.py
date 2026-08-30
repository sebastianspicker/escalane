"""Alarm notification message formatting.

NOTE: Message strings are in German: this is intentional (customer requirement).
"""

from __future__ import annotations

from datetime import datetime


def format_alarm_message(
    *,
    alarm_id: str,
    person: str,
    room: str,
    site: str | None,
    created_at: datetime,
    ack_url: str | None,
    step_no: int,
) -> str:
    """Format the intentionally German notification body used by responder channels."""
    parts = [
        "NOTFALLALARM (silent)",
        f"Alarm-ID: {alarm_id}",
        f"Person: {person}",
        f"Ort: {room}" + (f" / {site}" if site else ""),
        f"Zeit: {created_at.isoformat()}",
        f"Stufe: {step_no}",
    ]
    if ack_url:
        parts.append(f"Quittieren: {ack_url}")
    return "\n".join(parts)
