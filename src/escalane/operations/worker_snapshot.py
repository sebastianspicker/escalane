"""Expiring, bounded observations from the most recently reporting worker."""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from escalane.operations.metrics import latency_snapshot
from escalane.persistence.telemetry import BUCKETS

logger = logging.getLogger("escalane")
WORKER_SNAPSHOT_KEY = "escalane:metrics:worker"
WORKER_SNAPSHOT_TTL = 45


def pool_gauges(engine: Any, prefix: str = "") -> dict[str, float]:
    pool = engine.pool
    if not all(callable(getattr(pool, name, None)) for name in ("size", "checkedout", "overflow")):
        return {}
    size = pool.size()
    checked_out = pool.checkedout()
    capacity = engine.sync_engine.get_execution_options().get("escalane_pool_capacity")
    gauges = {
        f"{prefix}db_pool_size": float(size),
        f"{prefix}db_pool_checked_out": float(checked_out),
    }
    if capacity:
        gauges[f"{prefix}db_pool_capacity"] = float(capacity)
        gauges[f"{prefix}db_pool_utilization"] = checked_out / capacity
    return gauges


async def publish_worker_snapshot(redis: Any, engine: Any) -> None:
    """Diagnostics are best effort and never alter queue or transaction outcomes."""
    try:
        gauges = pool_gauges(engine, "worker_")
        gauges["worker_heartbeat_timestamp_seconds"] = time.time()
        # ARQ queue scores are enqueue/defer Unix milliseconds. Future scheduled
        # escalations are deliberately excluded from the overdue observation.
        oldest = await redis.zrangebyscore(
            "arq:queue", "-inf", int(time.time() * 1000), start=0, num=1, withscores=True
        )
        gauges["worker_overdue_queue_age_seconds"] = (
            max(0.0, time.time() - oldest[0][1] / 1000) if oldest else 0.0
        )
        await redis.set(
            WORKER_SNAPSHOT_KEY,
            json.dumps({"gauges": gauges, "histograms": latency_snapshot()}),
            ex=WORKER_SNAPSHOT_TTL,
        )
    except Exception:
        logger.debug("worker_snapshot_cache_unavailable")


async def read_worker_snapshot(redis: Any) -> tuple[dict[str, float], dict]:
    try:
        raw = await redis.get(WORKER_SNAPSHOT_KEY)
        if raw is None or len(raw) > 16384:
            return {}, {}
        value = json.loads(raw)
        gauges = value["gauges"]
        heartbeat = gauges["worker_heartbeat_timestamp_seconds"]
        if not 0 <= time.time() - heartbeat < WORKER_SNAPSHOT_TTL:
            return {}, {}
        allowed = {
            "worker_db_pool_capacity",
            "worker_db_pool_size",
            "worker_db_pool_checked_out",
            "worker_db_pool_utilization",
            "worker_heartbeat_timestamp_seconds",
            "worker_overdue_queue_age_seconds",
        }
        histograms = value["histograms"]
        if not isinstance(histograms, dict) or not all(
            isinstance(observation, list)
            and len(observation) == 3
            and isinstance(observation[0], list)
            and len(observation[0]) == len(BUCKETS)
            and all(type(count) is int and count >= 0 for count in observation[0])
            and type(observation[1]) is int
            and observation[1] >= 0
            and isinstance(observation[2], (int, float))
            and observation[2] >= 0
            for observation in histograms.values()
        ):
            return {}, {}
        return {key: float(item) for key, item in gauges.items() if key in allowed}, histograms
    except TypeError, ValueError, KeyError:
        return {}, {}
    except Exception:
        return {}, {}
