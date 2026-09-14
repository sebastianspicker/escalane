"""Bounded metric identity, cache expiry, and worker liveness regressions."""

import json
import time
from unittest.mock import patch

from escalane.operations import metrics
from escalane.operations.queries import historical_metrics
from escalane.operations.worker_snapshot import WORKER_SNAPSHOT_KEY, read_worker_snapshot
from escalane.persistence.models import Alarm
from tests.support.api_test_helpers import app_client


async def test_dynamic_urls_and_methods_have_bounded_labels(settings, engine, fake_redis):
    with (
        patch.object(metrics, "_http_requests_total", metrics.Counter()),
        patch.object(metrics, "_http_request_duration_ms_total", metrics.Counter()),
    ):
        async with app_client(settings=settings, engine=engine, redis=fake_redis) as client:
            for index in range(20):
                await client.get(f"/a/synthetic-capability-{index}")
                await client.request(f"SYNTHETIC{index}", f"/missing-{index}")
                await client.get(f"/admin/assets/missing-{index}.css")
        keys = metrics._http_requests_total
        assert len(keys) <= 4
        assert {key[1] for key in keys} == {"/a/{ack_token}", "unmatched", "/admin/assets/{path}"}
        assert {key[0] for key in keys} == {"GET", "OTHER"}
        assert "synthetic-capability" not in repr(keys)


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


def test_histogram_buckets_are_seconds_and_bounded():
    with patch.object(metrics, "_histograms", {}):
        metrics.observe_latency("provider_delivery", 0.02)
        counts, count, total = metrics.latency_snapshot()["provider_delivery"]
        assert count == 1 and total == 0.02
        assert counts[:3] == [0, 0, 1]
        output = metrics.render_prometheus_metrics(alarm_counts={}, notification_counts=[])
        assert 'escalane_provider_delivery_duration_seconds_bucket{le="0.025"} 1' in output
        assert "including pool wait and connection setup" in output


async def test_connection_acquisition_counts_checkouts_not_query_reuse(sessionmaker):
    from sqlalchemy import text

    from escalane.persistence.telemetry import acquisition_snapshot

    before = acquisition_snapshot()[1]
    async with sessionmaker() as session:
        for _ in range(3):
            await session.execute(text("SELECT 1"))
        assert acquisition_snapshot()[1] == before + 1
        await session.commit()
        await session.execute(text("SELECT 1"))
        assert acquisition_snapshot()[1] == before + 2


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


async def test_acquisition_includes_flush_and_recreated_pool(sessionmaker, engine):
    from escalane.persistence.telemetry import acquisition_snapshot

    before = acquisition_snapshot()[1]
    async with sessionmaker() as session:
        session.add(Alarm(source="synthetic", event="flush-acquisition"))
        await session.commit()
    assert acquisition_snapshot()[1] == before + 1
    await engine.dispose()
    async with engine.connect():
        assert acquisition_snapshot()[1] == before + 2
