"""Model factories shared by service, route, and worker tests."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from escalane.persistence.models import Alarm, AlarmStatus


def make_alarm(alarm_id: uuid.UUID | None = None, **overrides: Any) -> Alarm:
    """Create a complete triggered alarm, with test-specific overrides."""
    values = {
        "id": alarm_id or uuid.uuid4(),
        "status": AlarmStatus.TRIGGERED,
        "source": "test",
        "event": "alarm.trigger",
        "person_id": "ma-012",
        "room_id": "bg-1.23",
        "site_id": "bg",
        "device_id": "ylk-t5-10023",
        "severity": "P0",
        "silent": True,
        "ack_token": f"tok-{uuid.uuid4().hex[:8]}",
        "created_at": datetime.now(UTC),
        "meta": {},
        "resolved_at": None,
        "resolved_by": None,
        "cancelled_at": None,
        "cancelled_by": None,
        "acked_at": None,
        "acked_by": None,
    }
    values.update(overrides)
    return Alarm(**values)
