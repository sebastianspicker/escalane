"""Explicitly-invoked PostgreSQL proof for concurrent ordered outbox publication."""

from __future__ import annotations

import logging
import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from escalane.alarms.outbox import dispatch_pending_alarm_events
from escalane.config import constants
from escalane.contracts.alarms import AlarmStatus
from escalane.persistence.models import Alarm, AlarmEventOutbox

pytestmark = pytest.mark.integration

_POSTGRES_URL = os.environ.get("TEST_POSTGRES_URL")
_POSTGRES_ONLY = pytest.mark.skipif(
    not _POSTGRES_URL,
    reason="requires TEST_POSTGRES_URL; run make test-postgres-smoke",
)
logger = logging.getLogger("escalane.tests")


class _RecordingRedis:
    """Capture accepted enqueue requests without adding a Redis dependency to this gate."""

    def __init__(self) -> None:
        self.jobs: list[dict[str, str | None]] = []

    async def enqueue_job(
        self, _name: str, payload: dict[str, str | None], **_kwargs: Any
    ) -> object:
        self.jobs.append(payload)
        return object()


@pytest_asyncio.fixture
async def postgres_sessionmaker() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Open independent live PostgreSQL sessions against the migrated database."""
    assert _POSTGRES_URL is not None
    engine = create_async_engine(_POSTGRES_URL, pool_pre_ping=True)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            await session.execute(delete(Alarm).where(Alarm.event == "postgres.outbox.concurrency"))
            await session.commit()
        await engine.dispose()


def _alarm(alarm_id: uuid.UUID, source: str) -> Alarm:
    return Alarm(
        id=alarm_id,
        status=AlarmStatus.TRIGGERED,
        source=source,
        event="postgres.outbox.concurrency",
        severity="P0",
        silent=True,
        meta={},
    )


@_POSTGRES_ONLY
async def test_locked_oldest_stream_event_does_not_block_another_alarm(
    postgres_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """Skip a locked stream head, publish another stream, then resume in order."""
    first_alarm_id = uuid.uuid4()
    second_alarm_id = uuid.uuid4()
    base_time = datetime.now(UTC) - timedelta(minutes=1)
    first_oldest = AlarmEventOutbox(
        alarm_id=first_alarm_id,
        event_type=constants.EVENT_ALARM_CREATED,
        payload={},
        sequence=0,
        created_at=base_time,
    )
    first_next = AlarmEventOutbox(
        alarm_id=first_alarm_id,
        event_type=constants.EVENT_ALARM_STATE_CHANGED,
        payload={"old_state": "triggered", "new_state": "acknowledged"},
        sequence=1,
        created_at=base_time + timedelta(seconds=1),
    )
    second_oldest = AlarmEventOutbox(
        alarm_id=second_alarm_id,
        event_type=constants.EVENT_ALARM_CREATED,
        payload={},
        sequence=0,
        created_at=base_time + timedelta(seconds=2),
    )
    redis = _RecordingRedis()

    async with postgres_sessionmaker() as session:
        session.add_all(
            [
                _alarm(first_alarm_id, "postgres-outbox-first"),
                _alarm(second_alarm_id, "postgres-outbox-second"),
                first_oldest,
                first_next,
                second_oldest,
            ]
        )
        await session.commit()

    async with postgres_sessionmaker() as lock_session:
        async with lock_session.begin():
            locked_event = await lock_session.scalar(
                select(AlarmEventOutbox)
                .where(AlarmEventOutbox.id == first_oldest.id)
                .with_for_update()
            )
            assert locked_event is not None

            async with postgres_sessionmaker() as publisher_session:
                assert (
                    await dispatch_pending_alarm_events(publisher_session, redis, logger=logger)
                    == 1
                )

            async with postgres_sessionmaker() as inspection_session:
                persisted_first = list(
                    (
                        await inspection_session.scalars(
                            select(AlarmEventOutbox)
                            .where(AlarmEventOutbox.alarm_id == first_alarm_id)
                            .order_by(AlarmEventOutbox.sequence)
                        )
                    ).all()
                )
                persisted_second = await inspection_session.get(AlarmEventOutbox, second_oldest.id)
                assert [event.attempts for event in persisted_first] == [0, 0]
                assert all(event.published_at is None for event in persisted_first)
                assert persisted_second is not None
                assert persisted_second.attempts == 1
                assert persisted_second.published_at is not None

    async with postgres_sessionmaker() as resume_session:
        assert await dispatch_pending_alarm_events(resume_session, redis, logger=logger) == 2

    assert [(job["alarm_id"], job["event_type"]) for job in redis.jobs] == [
        (str(second_alarm_id), constants.EVENT_ALARM_CREATED),
        (str(first_alarm_id), constants.EVENT_ALARM_CREATED),
        (str(first_alarm_id), constants.EVENT_ALARM_STATE_CHANGED),
    ]

    async with postgres_sessionmaker() as session:
        resumed_first = list(
            (
                await session.scalars(
                    select(AlarmEventOutbox)
                    .where(AlarmEventOutbox.alarm_id == first_alarm_id)
                    .order_by(AlarmEventOutbox.sequence)
                )
            ).all()
        )
        assert [event.sequence for event in resumed_first] == [0, 1]
        assert [event.attempts for event in resumed_first] == [1, 1]
        assert all(event.published_at is not None for event in resumed_first)
