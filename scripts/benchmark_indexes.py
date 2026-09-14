"""Benchmark candidate PostgreSQL indexes in an explicitly disposable empty schema.

The benchmark applies Escalane's real migrations and deterministic synthetic data
inside one transaction, drops every candidate index after its comparison, and
rolls the transaction back so the supplied schema returns to its original empty
state. It never installs extensions or changes migration files.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import json
import statistics
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

_MIGRATION_HELPER_MODULE = (
    "scripts.benchmark_optimizations" if __package__ else "benchmark_optimizations"
)
migrate_disposable = importlib.import_module(_MIGRATION_HELPER_MODULE).migrate_disposable

SEED = 20260907
WARMUPS = 2
REPETITIONS = 5
WRITE_ROWS = 100
WRITE_REGRESSION_LIMIT = 0.10
OUTBOX_ROWS = 1000


@dataclass(frozen=True)
class Candidate:
    """One independently evaluated index and the production-shaped query it serves."""

    name: str
    index_name: str
    create_sql: str
    query_sql: str

    @property
    def drop_sql(self) -> str:
        return f'DROP INDEX IF EXISTS "{self.index_name}"'


CANDIDATES = (
    Candidate(
        name="visible_chronological",
        index_name="benchmark_idx_alarms_visible_chronological",
        create_sql=(
            "CREATE INDEX benchmark_idx_alarms_visible_chronological "
            "ON alarms (created_at DESC, id DESC) WHERE deleted_at IS NULL"
        ),
        query_sql=(
            "SELECT id FROM alarms WHERE deleted_at IS NULL "
            "ORDER BY created_at DESC, id DESC LIMIT 51"
        ),
    ),
    Candidate(
        name="status_severity_chronological",
        index_name="benchmark_idx_alarms_status_severity_chronological",
        create_sql=(
            "CREATE INDEX benchmark_idx_alarms_status_severity_chronological "
            "ON alarms (status, severity, created_at DESC, id DESC) "
            "WHERE deleted_at IS NULL"
        ),
        query_sql=(
            "SELECT id FROM alarms WHERE deleted_at IS NULL "
            "AND status = 'triggered'::alarm_status AND severity = 'P1' "
            "ORDER BY created_at DESC, id DESC LIMIT 51"
        ),
    ),
    Candidate(
        name="pending_global_order",
        index_name="benchmark_idx_outbox_pending_global_order",
        create_sql=(
            "CREATE INDEX benchmark_idx_outbox_pending_global_order "
            "ON alarm_event_outbox (created_at, sequence, id) WHERE published_at IS NULL"
        ),
        query_sql=(
            "SELECT candidate.id FROM alarm_event_outbox AS candidate "
            "WHERE candidate.published_at IS NULL AND NOT EXISTS ("
            "SELECT 1 FROM alarm_event_outbox AS earlier "
            "WHERE earlier.alarm_id = candidate.alarm_id "
            "AND earlier.published_at IS NULL AND ("
            "earlier.created_at < candidate.created_at OR "
            "(earlier.created_at = candidate.created_at "
            "AND earlier.sequence < candidate.sequence) OR "
            "(earlier.created_at = candidate.created_at "
            "AND earlier.sequence = candidate.sequence AND earlier.id < candidate.id))) "
            "ORDER BY candidate.created_at, candidate.sequence, candidate.id "
            "LIMIT 25 FOR UPDATE OF candidate SKIP LOCKED"
        ),
    ),
    Candidate(
        name="terminal_delivery_composite",
        index_name="benchmark_idx_notifications_terminal_delivery",
        create_sql=(
            "CREATE INDEX benchmark_idx_notifications_terminal_delivery "
            "ON alarm_notifications "
            "(alarm_id, channel, logical_delivery_key, created_at DESC, id DESC) "
            "WHERE target_id IS NULL AND result IN ('ok', 'permanent_error')"
        ),
        query_sql=(
            "SELECT id FROM alarm_notifications "
            "WHERE alarm_id = md5('alarm:1')::uuid AND channel = 'zammad' "
            "AND target_id IS NULL AND result IN ('ok', 'permanent_error') "
            "AND logical_delivery_key = 'ack_update:0' "
            "ORDER BY created_at DESC, id DESC LIMIT 1"
        ),
    ),
)

SUBSTRING_QUERY = (
    "SELECT id FROM alarms WHERE deleted_at IS NULL "
    "AND (source ILIKE '%needle%' OR event ILIKE '%needle%') "
    "ORDER BY created_at DESC, id DESC LIMIT 51"
)


def _distribution(samples: list[float]) -> dict[str, Any]:
    ordered = sorted(samples)
    return {
        "median_ms": statistics.median(samples),
        "p95_ms": ordered[-1],
        "runs_ms": samples,
    }


def _walk_plan(node: dict[str, Any]):
    yield node
    for child in node.get("Plans", []):
        yield from _walk_plan(child)


def plan_evidence(document: list[dict[str, Any]]) -> dict[str, Any]:
    """Extract buffer and sorting evidence while retaining the raw plan separately."""
    root = document[0]["Plan"]
    nodes = list(_walk_plan(root))
    buffer_fields = (
        "Shared Hit Blocks",
        "Shared Read Blocks",
        "Local Hit Blocks",
        "Local Read Blocks",
        "Temp Read Blocks",
        "Temp Written Blocks",
    )
    buffers = {field: int(root.get(field, 0)) for field in buffer_fields}
    buffer_nodes = [
        {
            "node_type": node["Node Type"],
            "buffers": {
                field: int(node.get(field, 0)) for field in buffer_fields if int(node.get(field, 0))
            },
        }
        for node in nodes
        if any(int(node.get(field, 0)) for field in buffer_fields)
    ]
    sort_nodes = [
        {
            "node_type": node["Node Type"],
            "sort_key": node.get("Sort Key", []),
            "sort_method": node.get("Sort Method"),
            "sort_space_kb": node.get("Sort Space Used"),
            "sort_space_type": node.get("Sort Space Type"),
        }
        for node in nodes
        if node.get("Node Type") in {"Sort", "Incremental Sort"}
    ]
    return {
        "buffer_blocks": sum(buffers.values()),
        "buffers": buffers,
        "buffer_nodes": buffer_nodes,
        "sort_present": bool(sort_nodes),
        "sort_nodes": sort_nodes,
    }


async def _explain_samples(connection: AsyncConnection, query_sql: str) -> dict[str, Any]:
    samples: list[dict[str, Any]] = []
    for sample_index in range(WARMUPS + REPETITIONS):
        started = time.perf_counter()
        document = await connection.scalar(
            text(f"EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) {query_sql}")
        )
        client_ms = (time.perf_counter() - started) * 1000
        if sample_index < WARMUPS:
            continue
        if not isinstance(document, list):
            raise TypeError(f"Expected PostgreSQL JSON plan list, found {type(document).__name__}")
        evidence = plan_evidence(document)
        samples.append(
            {
                "client_ms": client_ms,
                "planning_ms": float(document[0].get("Planning Time", 0.0)),
                "execution_ms": float(document[0].get("Execution Time", 0.0)),
                **evidence,
                "raw_plan": document,
            }
        )
    return {
        "client": _distribution([sample["client_ms"] for sample in samples]),
        "server_execution": _distribution([sample["execution_ms"] for sample in samples]),
        "samples": samples,
    }


async def _write_samples(connection: AsyncConnection) -> dict[str, Any]:
    samples: list[float] = []
    for sample_index in range(WARMUPS + REPETITIONS):
        savepoint = await connection.begin_nested()
        started = time.perf_counter()
        try:
            await connection.execute(
                text(
                    "UPDATE alarms SET "
                    "status = CASE WHEN status = 'triggered'::alarm_status "
                    "THEN 'acknowledged'::alarm_status ELSE 'triggered'::alarm_status END, "
                    "severity = CASE WHEN severity = 'P1' THEN 'P2' ELSE 'P1' END, "
                    "created_at = created_at + interval '1 microsecond' "
                    "WHERE id IN (SELECT id FROM alarms ORDER BY id LIMIT :write_rows)"
                ),
                {"write_rows": WRITE_ROWS},
            )
            elapsed_ms = (time.perf_counter() - started) * 1000
        finally:
            await savepoint.rollback()
        if sample_index >= WARMUPS:
            samples.append(elapsed_ms)
    return {"rows_per_trial": WRITE_ROWS, **_distribution(samples)}


def recommendation(
    before_reads: dict[str, Any],
    after_reads: dict[str, Any],
    before_writes: dict[str, Any],
    after_writes: dict[str, Any],
) -> dict[str, Any]:
    """Recommend only a read win without a repeatable write regression over ten percent."""
    before_buffers = [sample["buffer_blocks"] for sample in before_reads["samples"]]
    after_buffers = [sample["buffer_blocks"] for sample in after_reads["samples"]]
    median_before_buffers = statistics.median(before_buffers)
    median_after_buffers = statistics.median(after_buffers)
    buffers_reduced = median_after_buffers < median_before_buffers
    sorting_eliminated = all(sample["sort_present"] for sample in before_reads["samples"]) and all(
        not sample["sort_present"] for sample in after_reads["samples"]
    )

    paired_ratios = [
        after / max(before, 1e-12)
        for before, after in zip(before_writes["runs_ms"], after_writes["runs_ms"], strict=True)
    ]
    regressed_trials = sum(ratio > 1 + WRITE_REGRESSION_LIMIT for ratio in paired_ratios)
    repeatable_write_regression = regressed_trials >= 3
    recommended = (buffers_reduced or sorting_eliminated) and not repeatable_write_regression
    return {
        "recommended": recommended,
        "read_qualification": {
            "buffers_reduced": buffers_reduced,
            "median_buffer_blocks_before": median_before_buffers,
            "median_buffer_blocks_after": median_after_buffers,
            "sorting_eliminated": sorting_eliminated,
        },
        "write_guard": {
            "regression_limit_fraction": WRITE_REGRESSION_LIMIT,
            "paired_after_before_ratios": paired_ratios,
            "trials_over_limit": regressed_trials,
            "repeatable_regression": repeatable_write_regression,
        },
        "decision_rule": (
            "recommend only when median buffer blocks fall or sorting is eliminated, "
            "and fewer than three of five paired write trials regress by more than 10%"
        ),
    }


async def _schema_has_objects(connection: AsyncConnection) -> bool:
    return bool(
        await connection.scalar(
            text(
                "SELECT EXISTS ("
                "SELECT 1 FROM pg_class AS class "
                "JOIN pg_namespace AS namespace ON namespace.oid = class.relnamespace "
                "WHERE namespace.nspname = current_schema() "
                "UNION ALL "
                "SELECT 1 FROM pg_type AS type "
                "JOIN pg_namespace AS namespace ON namespace.oid = type.typnamespace "
                "WHERE namespace.nspname = current_schema() AND type.typtype = 'e'"
                ")"
            )
        )
    )


async def _postgresql_version(connection: AsyncConnection) -> tuple[int, str]:
    version_number = int(await connection.scalar(text("SHOW server_version_num")))
    version = str(await connection.scalar(text("SHOW server_version")))
    return version_number, version


async def _seed(connection: AsyncConnection, alarm_count: int) -> None:
    notification_count = alarm_count * 10
    await connection.execute(
        text(
            "INSERT INTO alarms "
            "(id, status, source, event, created_at, severity, silent, zammad_ticket_id, meta) "
            "SELECT md5('alarm:' || value)::uuid, "
            "(ARRAY['triggered', 'acknowledged', 'resolved', 'cancelled']::alarm_status[])["
            "1 + value % 4], "
            "CASE WHEN value % 97 = 0 THEN 'synthetic-needle' ELSE 'synthetic' END, "
            "CASE WHEN value % 89 = 0 THEN 'benchmark-needle' ELSE 'benchmark' END, "
            "TIMESTAMPTZ '2026-01-01 00:00:00+00' + value * interval '1 second', "
            "'P' || value % 3, true, value - 1, '{}'::json "
            "FROM generate_series(1, :alarm_count) AS value"
        ),
        {"alarm_count": alarm_count},
    )
    await connection.execute(
        text(
            "INSERT INTO alarm_notifications "
            "(id, alarm_id, created_at, channel, target_id, payload, result, logical_delivery_key) "
            "SELECT md5('notification:' || value)::uuid, "
            "md5('alarm:' || (1 + (value - 1) % :alarm_count))::uuid, "
            "TIMESTAMPTZ '2026-02-01 00:00:00+00' + value * interval '1 millisecond', "
            "'zammad', NULL, json_build_object('action', 'ack_update', "
            "'ticket_id', (value - 1) % :alarm_count), "
            "CASE WHEN value % 5 = 0 THEN 'permanent_error' ELSE 'ok' END, "
            "'ack_update:' || ((value - 1) % :alarm_count) "
            "FROM generate_series(1, :notification_count) AS value"
        ),
        {"alarm_count": alarm_count, "notification_count": notification_count},
    )
    await connection.execute(
        text(
            "INSERT INTO alarm_event_outbox "
            "(id, alarm_id, event_type, payload, sequence, created_at, published_at, attempts) "
            "SELECT md5('outbox:' || value)::uuid, "
            "md5('alarm:' || (1 + (value - 1) % LEAST(:alarm_count, 100)))::uuid, "
            "'alarm.state_changed', json_build_object('synthetic', true), "
            "(value - 1) / LEAST(:alarm_count, 100), "
            "TIMESTAMPTZ '2026-03-01 00:00:00+00' + value * interval '1 millisecond', "
            "CASE WHEN value % 4 = 0 THEN TIMESTAMPTZ '2026-03-02 00:00:00+00' ELSE NULL END, "
            "0 FROM generate_series(1, :outbox_rows) AS value"
        ),
        {"alarm_count": alarm_count, "outbox_rows": OUTBOX_ROWS},
    )
    await connection.execute(text("ANALYZE alarms, alarm_notifications, alarm_event_outbox"))


async def _truncate_workload(connection: AsyncConnection) -> None:
    await connection.execute(
        text(
            "TRUNCATE TABLE alarm_event_outbox, alarm_notifications, alarm_notes, alarms "
            "RESTART IDENTITY CASCADE"
        )
    )


async def _benchmark_candidate(connection: AsyncConnection, candidate: Candidate) -> dict[str, Any]:
    before_reads = await _explain_samples(connection, candidate.query_sql)
    before_writes = await _write_samples(connection)
    await connection.execute(text(candidate.create_sql))
    try:
        await connection.execute(text("ANALYZE alarms, alarm_notifications, alarm_event_outbox"))
        after_reads = await _explain_samples(connection, candidate.query_sql)
        after_writes = await _write_samples(connection)
    finally:
        await connection.execute(text(candidate.drop_sql))
    return {
        "name": candidate.name,
        "candidate_index": candidate.create_sql,
        "query": candidate.query_sql,
        "before": {"read": before_reads, "trigger_inclusive_write": before_writes},
        "after": {"read": after_reads, "trigger_inclusive_write": after_writes},
        "recommendation": recommendation(before_reads, after_reads, before_writes, after_writes),
    }


async def benchmark(url: str) -> dict[str, Any]:
    """Run the benchmark and restore the supplied initially-empty schema by rollback."""
    if not url.startswith(("postgresql+asyncpg://", "postgresql+psycopg://")):
        raise ValueError("Index benchmark requires an explicit PostgreSQL async database URL")
    engine = create_async_engine(url)
    report: dict[str, Any] = {
        "seed": SEED,
        "engine": "postgresql",
        "warmups": WARMUPS,
        "repetitions": REPETITIONS,
        "write_rows_per_trial": WRITE_ROWS,
        "candidate_indexes_are_temporary": True,
        "transaction_is_rolled_back": True,
        "extensions_installed": [],
        "workloads": [],
    }
    try:
        async with engine.connect() as connection:
            transaction = await connection.begin()
            try:
                version_number, version = await _postgresql_version(connection)
                if not 160000 <= version_number < 170000:
                    raise ValueError(
                        f"Index benchmark requires PostgreSQL 16; server reports {version}"
                    )
                report["postgresql_version"] = version
                if await _schema_has_objects(connection):
                    raise ValueError("Benchmark requires an empty disposable database schema")
                await connection.run_sync(migrate_disposable)
                for alarm_count in (1000, 10000):
                    await _truncate_workload(connection)
                    await _seed(connection, alarm_count)
                    candidate_results: list[dict[str, Any]] = []
                    workload: dict[str, Any] = {
                        "alarms": alarm_count,
                        "notification_audits": alarm_count * 10,
                        "outbox_rows": OUTBOX_ROWS,
                        "candidates": candidate_results,
                        "substring_diagnostic": {
                            "query": SUBSTRING_QUERY,
                            "extension": None,
                            "measurement": await _explain_samples(connection, SUBSTRING_QUERY),
                            "recommendation": None,
                        },
                    }
                    for candidate in CANDIDATES:
                        candidate_results.append(await _benchmark_candidate(connection, candidate))
                    report["workloads"].append(workload)
            finally:
                await transaction.rollback()
    finally:
        await engine.dispose()
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--disposable-database-url", required=True)
    parser.add_argument(
        "--confirm-empty-disposable-schema",
        action="store_true",
        help="Confirm the target schema is disposable and currently empty.",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not args.confirm_empty_disposable_schema:
        parser.error("--confirm-empty-disposable-schema is required")
    result = asyncio.run(benchmark(args.disposable_database_url))
    args.output.write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()
