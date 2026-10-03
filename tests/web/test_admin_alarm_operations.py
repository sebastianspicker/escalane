"""Console alarm detail view and bulk form-parsing contracts."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from fastapi import HTTPException

from escalane.persistence.models import Alarm, AlarmStatus, Person, Room
from escalane.web.routes import admin_alarms


def _alarm(*, status: AlarmStatus = AlarmStatus.TRIGGERED) -> Alarm:
    return Alarm(
        id=uuid.uuid4(),
        status=status,
        source="test",
        event="alarm.trigger",
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        person_id="p1",
        room_id="r1",
        severity="P0",
        meta={},
    )


def test_detail_view_keeps_history_renderable_after_master_data_changes() -> None:
    alarm = _alarm()

    view = admin_alarms._alarm_detail_view(alarm, None, None)

    assert view["person"] == "p1" and view["room"] == "r1"
    assert view["can_ack"] and view["can_close"]
    assert admin_alarms._created_event(alarm, "en")["description"] == "Alarm created"
    assert admin_alarms._created_event(alarm, "de")["description"] == "Alarm erstellt"
    resolved = admin_alarms._alarm_detail_view(
        _alarm(status=AlarmStatus.RESOLVED),
        Person(id="p1", display_name="P"),
        Room(id="r1", site_id="s", label="R"),
    )
    assert resolved["can_close"] is False


def test_bulk_validation_parsing_and_target_preserve_partial_success_semantics() -> None:
    valid = uuid.uuid4()
    parsed, invalid = admin_alarms._parse_alarm_ids(
        [str(valid), "bad", str(uuid.uuid4())] + ["x"] * 600
    )
    assert parsed[0] == valid and len(parsed) == 2 and invalid == 498
    with pytest.raises(HTTPException, match="selection_required"):
        admin_alarms._validate_bulk_request("ack", None, [])
    with pytest.raises(HTTPException, match="reason_required"):
        admin_alarms._validate_bulk_request("cancel", " ", [str(valid)])
    assert admin_alarms._bulk_target_status("ack") is AlarmStatus.ACKNOWLEDGED
    assert admin_alarms._bulk_target_status("resolve") is AlarmStatus.RESOLVED
    assert admin_alarms._bulk_target_status("cancel") is AlarmStatus.CANCELLED
