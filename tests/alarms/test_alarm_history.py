"""Tests for escalane.alarms.history event projection."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

from escalane.alarms.history import (
    HistoryKey,
    InvalidHistoryCursor,
    history_page,
    parse_history_cursor,
)
from escalane.persistence.models import AlarmNote, AlarmNotification
from tests.support.factories import make_alarm

pytestmark = [pytest.mark.unit]


async def test_history_describes_notes_and_deliveries_oldest_first(sessionmaker):
    alarm = make_alarm(person_id=None, room_id=None, site_id=None, device_id=None)
    async with sessionmaker() as session:
        session.add(alarm)
        await session.flush()
        session.add_all(
            [
                AlarmNote(
                    alarm_id=alarm.id,
                    note="Operator note",
                    created_by=None,
                    created_at=datetime(2026, 1, 2, tzinfo=UTC),
                ),
                AlarmNotification(
                    alarm_id=alarm.id,
                    channel="sms",
                    target_id="oncall",
                    payload={},
                    result="ok",
                    created_at=datetime(2026, 1, 3, tzinfo=UTC),
                ),
                AlarmNotification(
                    alarm_id=alarm.id,
                    channel="signal",
                    target_id="oncall",
                    payload={},
                    result=None,
                    created_at=datetime(2026, 1, 4, tzinfo=UTC),
                ),
            ]
        )
        await session.commit()

        events, cursor, include_creation = await history_page(session, alarm.id, None)

    assert [event["description"] for event in events] == [
        "System: Operator note",
        "sms: ok",
        "signal: pending",
    ]
    assert cursor is None and include_creation


@pytest.mark.parametrize("value", ["", "bad", "x" * 301, "W10", "bnVsbA"])
def test_invalid_history_cursor(value):
    with pytest.raises(InvalidHistoryCursor):
        parse_history_cursor(value)


def test_history_cursor_normalizes_timezone():
    key = HistoryKey(datetime(2026, 1, 1, tzinfo=UTC), "note", uuid.UUID(int=1))
    assert parse_history_cursor(key.encode()) == key
