"""Tests for bulk lifecycle commands and operator notes in escalane.alarms."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from escalane.alarms import lifecycle
from escalane.alarms.lifecycle import apply_bulk_state_change
from escalane.alarms.notes import add_alarm_note, list_alarm_notes
from escalane.persistence.models import Alarm, AlarmStatus
from tests.support.factories import make_alarm

pytestmark = [pytest.mark.unit]

_LOGGER = logging.getLogger("escalane")


class _FailingRedis:
    """Reject every enqueue so durable lifecycle events stay pending."""

    async def enqueue_job(self, *args: object, **kwargs: object) -> None:
        raise ConnectionError("redis unavailable")


async def _persist(sessionmaker: async_sessionmaker, *alarms: Alarm) -> None:
    async with sessionmaker() as session:
        session.add_all(alarms)
        await session.commit()


async def test_bulk_counts_changed_unchanged_conflicts_and_missing_in_request_order(
    sessionmaker, seeded_db, fake_redis
):
    triggered = make_alarm()
    acknowledged = make_alarm(status=AlarmStatus.ACKNOWLEDGED)
    resolved = make_alarm(status=AlarmStatus.RESOLVED)
    deleted = make_alarm(deleted_at=datetime.now(UTC))
    unknown = uuid.uuid4()
    await _persist(sessionmaker, triggered, acknowledged, resolved, deleted)

    async with sessionmaker() as session:
        outcome = await apply_bulk_state_change(
            session,
            fake_redis,
            [unknown, triggered.id, acknowledged.id, deleted.id, resolved.id],
            target_status=AlarmStatus.ACKNOWLEDGED,
            actor="operator",
            logger=_LOGGER,
        )

    assert (outcome.changed, outcome.unchanged) == (1, 2)
    assert outcome.missing == [unknown, deleted.id]


async def test_bulk_logs_pending_delivery_and_still_counts_change(sessionmaker, seeded_db, caplog):
    alarm = make_alarm()
    await _persist(sessionmaker, alarm)
    caplog.set_level(logging.WARNING, logger="escalane")

    async with sessionmaker() as session:
        outcome = await apply_bulk_state_change(
            session,
            _FailingRedis(),
            [alarm.id],
            target_status=AlarmStatus.RESOLVED,
            actor="operator",
            note="done",
            logger=_LOGGER,
        )

    assert (outcome.changed, outcome.unchanged, outcome.missing) == (1, 0, [])
    pending = [
        record for record in caplog.records if record.message == "bulk_event_delivery_pending"
    ]
    assert [record.alarm_id for record in pending] == [str(alarm.id)]


async def test_bulk_propagates_errors_other_than_conflicts(
    sessionmaker, seeded_db, fake_redis, monkeypatch: pytest.MonkeyPatch
):
    alarm = make_alarm()
    await _persist(sessionmaker, alarm)
    monkeypatch.setattr(
        lifecycle, "apply_alarm_state_change", AsyncMock(side_effect=RuntimeError("broken"))
    )

    async with sessionmaker() as session:
        with pytest.raises(RuntimeError, match="broken"):
            await apply_bulk_state_change(
                session,
                fake_redis,
                [alarm.id],
                target_status=AlarmStatus.RESOLVED,
                logger=_LOGGER,
            )


async def test_notes_are_attributed_and_listed_oldest_first(sessionmaker, seeded_db):
    alarm = make_alarm()
    await _persist(sessionmaker, alarm)

    async with sessionmaker() as session:
        first = await add_alarm_note(session, alarm, note="First", created_by="operator")
        second = await add_alarm_note(
            session, alarm, note="Second", created_by=None, note_type="system"
        )
        notes = await list_alarm_notes(session, alarm.id)

    assert first.id is not None and first.created_at is not None
    assert (first.note_type, second.note_type) == ("manual", "system")
    assert [(note.note, note.created_by) for note in notes] == [
        ("First", "operator"),
        ("Second", None),
    ]
