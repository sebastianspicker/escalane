"""Metric history caching, worker liveness snapshots, and pool gauges."""

from __future__ import annotations

import json
import time
from unittest.mock import patch

from escalane.operations.queries import historical_metrics
from escalane.operations.worker_snapshot import WORKER_SNAPSHOT_KEY, read_worker_snapshot
from escalane.persistence.models import Alarm


async def test_history_cache_and_expired_worker_omission(sessionmaker, fake_redis):
    async with sessionmaker() as session:
        assert (await historical_metrics(session, fake_redis))[0]["triggered"] == 0
        session.add(Alarm(source="synthetic", event="history"))
        await session.commit()
        assert (await historical_metrics(session, fake_redis))[0]["triggered"] == 0
        fake_redis.advance(11)
        assert (await historical_metrics(session, fake_redis))[0]["triggered"] == 1
        with patch.object(fake_redis, "get", side_effect=ConnectionError):
            assert (await historical_metrics(session, fake_redis))[0]["triggered"] == 1
    for timestamp in (time.time(), time.time() - 60):
        await fake_redis.set(
            WORKER_SNAPSHOT_KEY,
            json.dumps(
                {
                    "gauges": {"worker_heartbeat_timestamp_seconds": timestamp},
                    "histograms": {},
                }
            ),
            ex=45,
        )
        gauges, _ = await read_worker_snapshot(fake_redis)
        assert bool(gauges) == (time.time() - timestamp < 45)


async def test_pool_utilization_uses_configured_capacity(tmp_path):
    from escalane.operations.worker_snapshot import pool_gauges
    from escalane.persistence.engine import create_async_engine_from_url

    engine = create_async_engine_from_url(
        f"sqlite+aiosqlite:///{tmp_path / 'pool.db'}", pool_size=1, max_overflow=2
    )
    async with engine.connect():
        async with engine.connect():
            gauges = pool_gauges(engine)
            assert gauges["db_pool_capacity"] == 3
            assert gauges["db_pool_checked_out"] == 2
            assert gauges["db_pool_utilization"] == 2 / 3
    await engine.dispose()


async def test_corrupt_metric_caches_fall_back_or_omit(sessionmaker, fake_redis):
    await fake_redis.set(
        "escalane:metrics:history:v1", '{"alarms": [], "notifications": null}', ex=10
    )
    async with sessionmaker() as session:
        assert (await historical_metrics(session, fake_redis))[0]["triggered"] == 0
    await fake_redis.set(
        WORKER_SNAPSHOT_KEY,
        json.dumps(
            {
                "gauges": {"worker_heartbeat_timestamp_seconds": time.time()},
                "histograms": {"provider_delivery": [[1], 1, 1]},
            }
        ),
        ex=45,
    )
    assert await read_worker_snapshot(fake_redis) == ({}, {})
