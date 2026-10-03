"""Explicitly-invoked PostgreSQL proof for concurrent ordered outbox publication."""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from escalane.alarms.outbox import (
    EVENT_ALARM_CREATED,
    EVENT_ALARM_STATE_CHANGED,
    dispatch_pending_alarm_events,
)
from escalane.persistence.models import Alarm, AlarmEventOutbox, AlarmStatus

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


class _DelayedRecordingRedis(_RecordingRedis):
    async def enqueue_job(self, name: str, payload: dict[str, str | None], **kwargs: Any) -> object:
        await asyncio.sleep(0.01)
        return await super().enqueue_job(name, payload, **kwargs)


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
        event_type=EVENT_ALARM_CREATED,
        payload={},
        sequence=0,
        created_at=base_time,
    )
    first_next = AlarmEventOutbox(
        alarm_id=first_alarm_id,
        event_type=EVENT_ALARM_STATE_CHANGED,
        payload={"old_state": "triggered", "new_state": "acknowledged"},
        sequence=1,
        created_at=base_time + timedelta(seconds=1),
    )
    second_oldest = AlarmEventOutbox(
        alarm_id=second_alarm_id,
        event_type=EVENT_ALARM_CREATED,
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
        (str(second_alarm_id), EVENT_ALARM_CREATED),
        (str(first_alarm_id), EVENT_ALARM_CREATED),
        (str(first_alarm_id), EVENT_ALARM_STATE_CHANGED),
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


@_POSTGRES_ONLY
async def test_delayed_publication_commits_one_complete_short_batch_before_budget_exit(
    postgres_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    alarm_ids = [uuid.uuid4() for _ in range(3)]
    redis = _DelayedRecordingRedis()
    async with postgres_sessionmaker() as session:
        session.add_all(
            [
                item
                for alarm_id in alarm_ids
                for item in (
                    _alarm(alarm_id, "postgres-outbox-batch"),
                    AlarmEventOutbox(
                        alarm_id=alarm_id,
                        event_type=EVENT_ALARM_CREATED,
                        payload={},
                    ),
                )
            ]
        )
        await session.commit()
        original_commit = session.commit
        session.commit = AsyncMock(wraps=original_commit)  # type: ignore[method-assign]

        published = await dispatch_pending_alarm_events(
            session,
            redis,
            logger=logger,
            batch_size=2,
            budget_seconds=0.001,
        )

        assert published == 2
        session.commit.assert_awaited_once()
    assert len(redis.jobs) == 2


@_POSTGRES_ONLY
async def test_commit_failure_stops_before_a_second_postgres_batch(
    postgres_sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    alarm_ids = [uuid.uuid4() for _ in range(3)]
    redis = _RecordingRedis()
    async with postgres_sessionmaker() as session:
        session.add_all(
            [
                item
                for alarm_id in alarm_ids
                for item in (
                    _alarm(alarm_id, "postgres-outbox-commit-failure"),
                    AlarmEventOutbox(
                        alarm_id=alarm_id,
                        event_type=EVENT_ALARM_CREATED,
                        payload={},
                    ),
                )
            ]
        )
        await session.commit()
        session.commit = AsyncMock(side_effect=RuntimeError("commit unavailable"))  # type: ignore[method-assign]
        with pytest.raises(RuntimeError, match="commit unavailable"):
            await dispatch_pending_alarm_events(session, redis, logger=logger, batch_size=2)

    assert len(redis.jobs) == 2
