"""Stable alarm-domain contracts shared across application boundaries."""

from __future__ import annotations

from enum import StrEnum


class AlarmStatus(StrEnum):
    """The durable lifecycle states an alarm may occupy."""

    TRIGGERED = "triggered"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"
    CANCELLED = "cancelled"
