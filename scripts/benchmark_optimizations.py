"""Synthetic read benchmark; PostgreSQL plans require an explicit disposable URL.

Creates only a private temporary SQLite database by default. PostgreSQL mode
requires an empty disposable database, refuses populated databases, and does not
remove the database. Results are evidence for that engine and workload only.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import tempfile
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory
from sqlalchemy import delete, func, insert, select, text
from sqlalchemy.ext.asyncio import create_async_engine

from escalane.persistence.base import Base
from escalane.persistence.models import Alarm, AlarmNotification, DashboardRevision

SEED = 20260907
REPETITIONS = 5
WARMUPS = 2


def distribution(samples: list[float]) -> dict[str, Any]:
    ordered = sorted(samples)
    return {"median_ms": statistics.median(samples), "p95_ms": ordered[-1], "runs_ms": samples}


async def timed(connection, statement, operators: int = 1) -> dict[str, Any]:
    samples = []
    for index in range(WARMUPS + REPETITIONS):
        start = time.perf_counter()
        for _ in range(operators):
            (await connection.execute(statement)).all()
        elapsed = (time.perf_counter() - start) * 1000
        if index >= WARMUPS:
            samples.append(elapsed)
    return distribution(samples)


def migrate_disposable(connection) -> None:
    """Apply the actual migration sequence inside this disposable connection."""
    scripts = ScriptDirectory.from_config(
        Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    )
    with Operations.context(MigrationContext.configure(connection)):
        for revision in reversed(list(scripts.walk_revisions())):
            revision.module.upgrade()


async def benchmark(url: str, sizes: list[int]) -> dict:
    engine = create_async_engine(url)
    report: dict[str, Any] = {
        "seed": SEED,
        "repetitions": REPETITIONS,
        "warmups": WARMUPS,
        "engine": engine.dialect.name,
        "five_operator_model": "five sequential polling queries",
        "workloads": [],
    }
    async with engine.begin() as connection:
        if engine.dialect.name == "postgresql":
            existing = await connection.scalar(
                text("SELECT count(*) FROM pg_tables WHERE schemaname = current_schema()")
            )
            if existing:
                raise ValueError("Benchmark requires an empty disposable database schema")
            await connection.run_sync(migrate_disposable)
        else:
            await connection.run_sync(Base.metadata.create_all)
        for size in sizes:
            await connection.execute(delete(AlarmNotification))
            await connection.execute(delete(Alarm))
            base = datetime(2026, 1, 1, tzinfo=UTC)
            alarms = [
                {
                    "id": uuid.UUID(int=SEED + index),
                    "source": "synthetic",
                    "event": "benchmark",
                    "created_at": base + timedelta(seconds=index),
                    "status": "triggered",
                    "severity": f"P{index % 3}",
                    "meta": {},
                }
                for index in range(size)
            ]
            for offset in range(0, size, 1000):
                await connection.execute(insert(Alarm), alarms[offset : offset + 1000])
            notifications = [
                {
                    "id": uuid.UUID(int=index + 1),
                    "alarm_id": alarms[index % size]["id"],
                    "channel": "zammad",
                    "payload": {"action": "ack_update", "ticket_id": index},
                    "logical_delivery_key": f"ack_update:{index}",
                    "result": "ok",
                }
                for index in range(size * 10)
            ]
            for offset in range(0, len(notifications), 1000):
                await connection.execute(
                    insert(AlarmNotification), notifications[offset : offset + 1000]
                )
            legacy = select(
                func.count(Alarm.id),
                func.max(Alarm.created_at),
                func.max(Alarm.acked_at),
                func.max(Alarm.resolved_at),
                func.max(Alarm.cancelled_at),
            ).where(Alarm.deleted_at.is_(None))
            durable = select(DashboardRevision.epoch, DashboardRevision.version).where(
                DashboardRevision.id == 1
            )
            chronological = (
                select(Alarm.id)
                .where(Alarm.deleted_at.is_(None))
                .order_by(Alarm.created_at.desc(), Alarm.id.desc())
                .limit(50)
            )
            filtered = chronological.where(Alarm.status == "triggered", Alarm.severity == "P1")
            substring = chronological.where(Alarm.event.ilike("%bench%"))
            queries: dict[str, Any] = {
                "legacy_revision": legacy,
                "durable_revision": durable,
                "chronological": chronological,
                "filtered": filtered,
                "substring": substring,
            }
            result: dict[str, Any] = {"alarms": size, "notifications": size * 10, "queries": {}}
            for name, query in queries.items():
                measurements = await timed(connection, query, 5 if "revision" in name else 1)
                compiled = str(
                    query.compile(dialect=engine.dialect, compile_kwargs={"literal_binds": True})
                )
                explain = (
                    "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) "
                    if engine.dialect.name == "postgresql"
                    else "EXPLAIN QUERY PLAN "
                )
                plan = (await connection.execute(text(explain + compiled))).all()
                measurements["plan"] = [list(row) for row in plan]
                result["queries"][name] = measurements
            report["workloads"].append(result)
    await engine.dispose()
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--disposable-database-url")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="escalane-benchmark-") as directory:
        url = args.disposable_database_url or f"sqlite+aiosqlite:///{directory}/synthetic.db"
        result = asyncio.run(benchmark(url, [1000, 10000]))
    args.output.write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()
