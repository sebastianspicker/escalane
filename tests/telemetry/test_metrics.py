"""Bounded metric identity, histogram units, and connection-acquisition counters."""

from __future__ import annotations

from unittest.mock import patch

from escalane.persistence.models import Alarm
from escalane.telemetry import metrics
from tests.support.clients import app_client


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

    from escalane.telemetry.metrics import acquisition_snapshot

    before = acquisition_snapshot()[1]
    async with sessionmaker() as session:
        for _ in range(3):
            await session.execute(text("SELECT 1"))
        assert acquisition_snapshot()[1] == before + 1
        await session.commit()
        await session.execute(text("SELECT 1"))
        assert acquisition_snapshot()[1] == before + 2


async def test_acquisition_includes_flush_and_recreated_pool(sessionmaker, engine):
    from escalane.telemetry.metrics import acquisition_snapshot

    before = acquisition_snapshot()[1]
    async with sessionmaker() as session:
        session.add(Alarm(source="synthetic", event="flush-acquisition"))
        await session.commit()
    assert acquisition_snapshot()[1] == before + 1
    await engine.dispose()
    async with engine.connect():
        assert acquisition_snapshot()[1] == before + 2
