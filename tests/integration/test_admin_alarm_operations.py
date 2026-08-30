"""Bulk console operation and alarm-timeline contracts."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from escalane.alarms.lifecycle import AlarmStateOutcome
from escalane.contracts.alarms import AlarmStatus
from escalane.persistence.models import (
    Alarm,
    AlarmNote,
    AlarmNotification,
    Person,
    Room,
)
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


def test_detail_view_and_timeline_keep_history_renderable_after_master_data_changes() -> None:
    alarm = _alarm()
    note = AlarmNote(
        alarm_id=alarm.id,
        note="Operator note",
        created_by=None,
        created_at=datetime(2026, 1, 2, tzinfo=UTC),
    )
    delivery = AlarmNotification(
        alarm_id=alarm.id,
        channel="sms",
        target_id="oncall",
        payload={},
        result="ok",
        created_at=datetime(2026, 1, 3, tzinfo=UTC),
    )

    view = admin_alarms._alarm_detail_view(alarm, None, None)
    timeline = admin_alarms._alarm_timeline(alarm, "en", [note], [delivery])

    assert view["person"] == "p1" and view["room"] == "r1"
    assert view["can_ack"] and view["can_close"]
    assert [event["description"] for event in timeline] == [
        "Alarm created",
        "System: Operator note",
        "sms: ok",
    ]
    resolved = admin_alarms._alarm_detail_view(
        _alarm(status=AlarmStatus.RESOLVED),
        Person(id="p1", display_name="P"),
        Room(id="r1", site_id="s", label="R"),
    )
    assert resolved["can_close"] is False


def test_bulk_validation_parsing_and_transition_preserve_partial_success_semantics() -> None:
    valid = uuid.uuid4()
    parsed, invalid = admin_alarms._parse_alarm_ids(
        [str(valid), "bad", str(uuid.uuid4())] + ["x"] * 600
    )
    assert parsed[0] == valid and len(parsed) == 2 and invalid == 498
    with pytest.raises(HTTPException, match="selection_required"):
        admin_alarms._validate_bulk_request("ack", None, [])
    with pytest.raises(HTTPException, match="reason_required"):
        admin_alarms._validate_bulk_request("cancel", " ", [str(valid)])
    transition = admin_alarms._bulk_transition("ack", actor="operator", reason=None, redis=object())
    assert transition.target_status is AlarmStatus.ACKNOWLEDGED


@pytest.mark.asyncio
async def test_bulk_actions_count_missing_unchanged_and_pending_delivery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    existing = _alarm()
    deleted = _alarm()
    deleted.deleted_at = datetime.now(UTC)
    session = MagicMock()
    session.get = AsyncMock(side_effect=[existing, deleted, None])
    outcome = AlarmStateOutcome(changed=True, published=False, pending=True)
    monkeypatch.setattr(admin_alarms, "_apply_bulk_transition", AsyncMock(return_value=outcome))
    transition = admin_alarms._bulk_transition(
        "resolve", actor="operator", reason="done", redis=object()
    )

    changed, unchanged, missing = await admin_alarms._apply_bulk_actions(
        session, [existing.id, deleted.id, uuid.uuid4()], transition
    )

    assert (changed, unchanged, missing) == (1, 0, 2)


@pytest.mark.asyncio
async def test_bulk_transition_treats_only_conflicts_as_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    alarm = _alarm()
    transition = admin_alarms._bulk_transition(
        "resolve", actor="operator", reason=None, redis=object()
    )
    monkeypatch.setattr(
        admin_alarms,
        "apply_alarm_state_change",
        AsyncMock(side_effect=HTTPException(status_code=409, detail="race")),
    )
    assert await admin_alarms._apply_bulk_transition(MagicMock(), alarm, transition) is None
    monkeypatch.setattr(
        admin_alarms,
        "apply_alarm_state_change",
        AsyncMock(side_effect=HTTPException(status_code=500, detail="broken")),
    )
    with pytest.raises(HTTPException) as raised:
        await admin_alarms._apply_bulk_transition(MagicMock(), alarm, transition)
    assert raised.value.status_code == 500
