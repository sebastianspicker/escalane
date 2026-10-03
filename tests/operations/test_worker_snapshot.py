"""Worker snapshot publication, bounded reads, expiry, and pool gauges."""

from __future__ import annotations

import json
import time
from types import SimpleNamespace

import pytest

from escalane.operations import worker_snapshot
from escalane.operations.worker_snapshot import (
    WORKER_SNAPSHOT_KEY,
    WORKER_SNAPSHOT_TTL,
    pool_gauges,
    publish_worker_snapshot,
    read_worker_snapshot,
)
from escalane.telemetry.metrics import BUCKETS
from tests.support.fakes import FakeRedis

pytestmark = pytest.mark.unit

NOW = 1_800_000_000.0


class QueueRedis(FakeRedis):
    """FakeRedis with the one sorted-set read the snapshot publisher needs."""

    def __init__(self, queue: list[tuple[bytes, float]] | None = None) -> None:
        super().__init__()
        self.queue = queue or []
        self.zrange_calls: list[tuple] = []

    async def zrangebyscore(self, key, minimum, maximum, *, start, num, withscores):
        self.zrange_calls.append((key, minimum, maximum, start, num, withscores))
        due = [item for item in self.queue if item[1] <= maximum]
        return sorted(due, key=lambda item: item[1])[:num]


class Pool:
    def __init__(self, size: int, checked_out: int, overflow: int) -> None:
        self._values = (size, checked_out, overflow)

    def size(self) -> int:
        return self._values[0]

    def checkedout(self) -> int:
        return self._values[1]

    def overflow(self) -> int:
        return self._values[2]


def _engine(pool: object, capacity: int | None = 10) -> SimpleNamespace:
    options = {} if capacity is None else {"escalane_pool_capacity": capacity}
    return SimpleNamespace(
        pool=pool, sync_engine=SimpleNamespace(get_execution_options=lambda: options)
    )


def _histogram(count: int = 1) -> list:
    return [[count] * len(BUCKETS), count, 0.5]


def _payload(heartbeat: float, **gauges: float) -> str:
    return json.dumps(
        {
            "gauges": {"worker_heartbeat_timestamp_seconds": heartbeat, **gauges},
            "histograms": {"http": _histogram()},
        }
    )


def test_pool_gauges_report_size_checkout_capacity_and_utilization() -> None:
    gauges = pool_gauges(_engine(Pool(5, 4, 0), capacity=8), "worker_")
    assert gauges == {
        "worker_db_pool_size": 5.0,
        "worker_db_pool_checked_out": 4.0,
        "worker_db_pool_capacity": 8.0,
        "worker_db_pool_utilization": 0.5,
    }


def test_pool_gauges_omit_capacity_series_without_configured_capacity() -> None:
    assert pool_gauges(_engine(Pool(5, 1, 0), capacity=None)) == {
        "db_pool_size": 5.0,
        "db_pool_checked_out": 1.0,
    }


def test_pool_gauges_without_pool_methods_are_empty() -> None:
    assert pool_gauges(_engine(object())) == {}
    assert pool_gauges(_engine(SimpleNamespace(size=lambda: 1, checkedout=lambda: 0))) == {}
    assert pool_gauges(_engine(SimpleNamespace(size=1, checkedout=0, overflow=0))) == {}


async def test_publish_then_read_round_trips_gauges_and_histograms(monkeypatch) -> None:
    monkeypatch.setattr(time, "time", lambda: NOW)
    redis = QueueRedis([(b"job-old", (NOW - 30) * 1000), (b"job-future", (NOW + 600) * 1000)])

    await publish_worker_snapshot(redis, _engine(Pool(5, 2, 0), capacity=10))

    assert redis._expiries[WORKER_SNAPSHOT_KEY] > redis._now
    assert redis._expiries[WORKER_SNAPSHOT_KEY] - redis._now == WORKER_SNAPSHOT_TTL
    assert redis.zrange_calls == [("arq:queue", "-inf", int(NOW * 1000), 0, 1, True)]
    gauges, histograms = await read_worker_snapshot(redis)
    assert gauges == {
        "worker_db_pool_size": 5.0,
        "worker_db_pool_checked_out": 2.0,
        "worker_db_pool_capacity": 10.0,
        "worker_db_pool_utilization": 0.2,
        "worker_heartbeat_timestamp_seconds": NOW,
        "worker_overdue_queue_age_seconds": pytest.approx(30.0),
    }
    assert "connection_acquisition" in histograms


async def test_publish_reports_zero_overdue_age_for_empty_or_future_queue(monkeypatch) -> None:
    monkeypatch.setattr(time, "time", lambda: NOW)
    redis = QueueRedis([(b"job-future", (NOW + 600) * 1000)])
    await publish_worker_snapshot(redis, _engine(object()))
    gauges, _ = await read_worker_snapshot(redis)
    assert gauges["worker_overdue_queue_age_seconds"] == 0.0
    assert not any(key.startswith("worker_db_pool") for key in gauges)


async def test_publish_is_best_effort_when_redis_fails(monkeypatch) -> None:
    class Failing(QueueRedis):
        async def zrangebyscore(self, *args, **kwargs):
            raise ConnectionError("redis down")

    redis = Failing()
    await publish_worker_snapshot(redis, _engine(Pool(1, 0, 0)))
    assert await redis.get(WORKER_SNAPSHOT_KEY) is None


async def test_publish_is_best_effort_without_sorted_set_support() -> None:
    redis = FakeRedis()  # has no zrangebyscore: the failure must be swallowed
    await publish_worker_snapshot(redis, _engine(Pool(1, 0, 0)))
    assert await read_worker_snapshot(redis) == ({}, {})


async def test_read_filters_gauges_to_the_allowed_set(monkeypatch) -> None:
    monkeypatch.setattr(time, "time", lambda: NOW)
    redis = FakeRedis()
    await redis.set(
        WORKER_SNAPSHOT_KEY,
        _payload(NOW - 1, worker_db_pool_size=3, unexpected_gauge=99, secret_label=1),
    )
    gauges, histograms = await read_worker_snapshot(redis)
    assert gauges == {
        "worker_heartbeat_timestamp_seconds": NOW - 1,
        "worker_db_pool_size": 3.0,
    }
    assert histograms == {"http": _histogram()}


async def test_read_returns_nothing_without_a_snapshot() -> None:
    assert await read_worker_snapshot(FakeRedis()) == ({}, {})


async def test_heartbeat_expires_after_the_ttl_window(monkeypatch) -> None:
    redis = FakeRedis()
    heartbeat = NOW
    await redis.set(WORKER_SNAPSHOT_KEY, _payload(heartbeat))
    for clock, expect_data in (
        (heartbeat + WORKER_SNAPSHOT_TTL - 0.5, True),
        (heartbeat + WORKER_SNAPSHOT_TTL, False),
        (heartbeat + 3600, False),
        (heartbeat - 5, False),  # a heartbeat from the future is not trusted
    ):
        monkeypatch.setattr(worker_snapshot.time, "time", lambda clock=clock: clock)
        gauges, histograms = await read_worker_snapshot(redis)
        assert bool(gauges) is expect_data
        assert bool(histograms) is expect_data


@pytest.mark.parametrize(
    "raw",
    [
        "x" * 16385,
        "not json",
        "[]",
        "{}",
        json.dumps({"gauges": {}, "histograms": {}}),
        json.dumps({"gauges": {"worker_heartbeat_timestamp_seconds": "soon"}, "histograms": {}}),
        json.dumps({"gauges": {"worker_heartbeat_timestamp_seconds": NOW}}),
        json.dumps(
            {"gauges": {"worker_heartbeat_timestamp_seconds": NOW}, "histograms": []},
        ),
        json.dumps(
            {
                "gauges": {"worker_heartbeat_timestamp_seconds": NOW},
                "histograms": {"http": [[1, 2], 1, 0.5]},
            }
        ),
        json.dumps(
            {
                "gauges": {"worker_heartbeat_timestamp_seconds": NOW},
                "histograms": {"http": [[-1] * len(BUCKETS), 1, 0.5]},
            }
        ),
        json.dumps(
            {
                "gauges": {"worker_heartbeat_timestamp_seconds": NOW},
                "histograms": {"http": [[True] * len(BUCKETS), 1, 0.5]},
            }
        ),
        json.dumps(
            {
                "gauges": {"worker_heartbeat_timestamp_seconds": NOW},
                "histograms": {"http": [[1] * len(BUCKETS), 1, -0.5]},
            }
        ),
    ],
)
async def test_corrupt_or_oversized_snapshots_are_ignored(monkeypatch, raw) -> None:
    monkeypatch.setattr(time, "time", lambda: NOW)
    redis = FakeRedis()
    await redis.set(WORKER_SNAPSHOT_KEY, raw)
    assert await read_worker_snapshot(redis) == ({}, {})


async def test_read_tolerates_redis_failure() -> None:
    class Failing(FakeRedis):
        async def get(self, key):
            raise ConnectionError("redis down")

    assert await read_worker_snapshot(Failing()) == ({}, {})
