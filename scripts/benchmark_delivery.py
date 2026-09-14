"""Measure unchanged serial fan-out with synthetic providers and durable audits.

Run the same script and virtual environment with PYTHONPATH pointing to each
source snapshot. Databases are private temporary SQLite files; no network calls.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import tempfile
import time
import uuid
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from typing import Any

from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import create_async_engine

from escalane.notifications import dispatch
from escalane.persistence.base import Base
from escalane.persistence.models import (
    Alarm,
    AlarmNotification,
    EscalationPolicy,
    EscalationStep,
    EscalationTarget,
)
from escalane.persistence.session import create_sessionmaker


class DelayedSms:
    def __init__(self, delay: float):
        self.delay = delay
        self.deliveries = 0
        self.active = 0
        self.max_active = 0

    def enabled(self) -> bool:
        return True

    async def send_sms(self, target: str, message: str) -> None:
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        await asyncio.sleep(self.delay)
        self.deliveries += 1
        self.active -= 1


async def measure(target_count: int, delay: float, directory: str) -> dict[str, Any]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{directory}/{target_count}-{delay}.db")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = create_sessionmaker(engine)
    async with sessions() as session:
        session.add(EscalationPolicy(id="default", name="Synthetic"))
        for index in range(target_count):
            session.add(
                EscalationTarget(
                    id=f"synthetic-{index}", label="Synthetic", channel="sms", address="synthetic"
                )
            )
            session.add(
                EscalationStep(
                    policy_id="default", step_no=0, after_seconds=0, target_id=f"synthetic-{index}"
                )
            )
        await session.commit()
    provider = DelayedSms(delay)
    service = dispatch.NotificationService(zammad=None, sendxms=provider, signal=None)  # type: ignore[arg-type]
    query_count = 0

    def count_query(*args):
        nonlocal query_count
        query_count += 1

    event.listen(engine.sync_engine, "before_cursor_execute", count_query)
    samples, queries = [], []
    for run in range(7):
        async with sessions() as session:
            alarm = Alarm(
                id=uuid.UUID(int=20260907 + run),
                source="synthetic",
                event="fanout",
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
            session.add(alarm)
            await session.commit()
            query_count = 0
            start = time.perf_counter()
            await service.send(
                session,
                alarm,
                {
                    "person_name": "Synthetic",
                    "room_label": "Synthetic",
                    "site_name": "Synthetic",
                    "severity": "P0",
                },
                step_no=0,
                ack_url=None,
            )
            elapsed = (time.perf_counter() - start) * 1000
            count = query_count
            rows = list(
                (
                    await session.scalars(
                        select(AlarmNotification).where(AlarmNotification.alarm_id == alarm.id)
                    )
                ).all()
            )
            if len(rows) != target_count or any(row.result != "ok" for row in rows):
                raise RuntimeError("Delivery audit mismatch")
            delivered = provider.deliveries
            await service.send(
                session,
                alarm,
                {
                    "person_name": "Synthetic",
                    "room_label": "Synthetic",
                    "site_name": "Synthetic",
                    "severity": "P0",
                },
                step_no=0,
                ack_url=None,
            )
            if provider.deliveries != delivered:
                raise RuntimeError("Retry duplicated a completed delivery")
            if run >= 2:
                samples.append(elapsed)
                queries.append(count)
    await engine.dispose()
    if provider.max_active != 1 or provider.deliveries != target_count * 7:
        raise RuntimeError("Serial fanout invariant failed")
    return {
        "targets": target_count,
        "provider_delay_ms": delay * 1000,
        "median_ms": statistics.median(samples),
        "p95_ms": max(samples),
        "runs_ms": samples,
        "query_counts": queries,
        "delivery_count_including_warmups": provider.deliveries,
        "max_concurrent_deliveries": provider.max_active,
        "audit_and_retry_correct": True,
    }


async def run(directory: str) -> dict:
    results = []
    for delay in (0.0, 0.005):
        for targets in (1, 5, 20):
            results.append(await measure(targets, delay, directory))
    return {
        "source": dispatch.__file__,
        "engine": "SQLite",
        "seed": 20260907,
        "warmups": 2,
        "repetitions": 5,
        "sqlalchemy": version("sqlalchemy"),
        "python": __import__("platform").python_version(),
        "workloads": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="escalane-fanout-") as directory:
        report = asyncio.run(run(directory))
    args.output.write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
