"""Compare a 1,000-event serial outbox drain using isolated synthetic SQLite state."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import statistics
import tempfile
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import delete, event, select
from sqlalchemy.ext.asyncio import create_async_engine

from escalane.alarms import outbox
from escalane.config import constants
from escalane.persistence.base import Base
from escalane.persistence.models import Alarm, AlarmEventOutbox
from escalane.persistence.session import create_sessionmaker


class SyntheticQueue:
    def __init__(self):
        self.ids: set[str] = set()

    async def enqueue_job(self, name, payload, **kwargs):
        self.ids.add(kwargs["_job_id"])
        return object()


async def measure(directory: str) -> dict:
    engine = create_async_engine(f"sqlite+aiosqlite:///{directory}/recovery.db")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = create_sessionmaker(engine)
    query_count = 0

    def count_query(*args):
        nonlocal query_count
        query_count += 1

    event.listen(engine.sync_engine, "before_cursor_execute", count_query)
    samples, queries, commits = [], [], []
    for run in range(7):
        async with sessions() as session:
            await session.execute(delete(AlarmEventOutbox))
            await session.execute(delete(Alarm))
            base = datetime(2026, 1, 1, tzinfo=UTC)
            for index in range(1000):
                alarm_id = uuid.UUID(int=20260907 + index)
                session.add(Alarm(id=alarm_id, source="synthetic", event="recovery"))
                session.add(
                    AlarmEventOutbox(
                        id=uuid.UUID(int=index + 1),
                        alarm_id=alarm_id,
                        event_type=constants.EVENT_ALARM_CREATED,
                        created_at=base + timedelta(seconds=index),
                        payload={},
                    )
                )
            await session.commit()
            commit_count = 0

            def committed(_session):
                nonlocal commit_count
                commit_count += 1

            event.listen(session.sync_session, "after_commit", committed)
            queue = SyntheticQueue()
            query_count = 0
            start = time.perf_counter()
            published = await outbox.dispatch_pending_alarm_events(
                session, queue, logger=logging.getLogger("benchmark"), limit=5000
            )
            elapsed = (time.perf_counter() - start) * 1000
            count = query_count
            remaining = await session.scalar(
                select(AlarmEventOutbox.id).where(AlarmEventOutbox.published_at.is_(None)).limit(1)
            )
            if published != 1000 or len(queue.ids) != 1000 or remaining is not None:
                raise RuntimeError("Recovery lost or duplicated events")
            if run >= 2:
                samples.append(elapsed)
                queries.append(count)
                commits.append(commit_count)
    await engine.dispose()
    return {
        "source": outbox.__file__,
        "engine": "SQLite",
        "seed": 20260907,
        "events": 1000,
        "warmups": 2,
        "repetitions": 5,
        "median_ms": statistics.median(samples),
        "p95_ms": max(samples),
        "runs_ms": samples,
        "query_counts": queries,
        "commits": commits,
        "accepted_jobs": 1000,
        "published_audits": 1000,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="escalane-recovery-") as directory:
        result = asyncio.run(measure(directory))
    args.output.write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()
