"""Bounded connection-acquisition observations owned by persistence."""

from __future__ import annotations

from threading import Lock

BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0)
_lock = Lock()
_counts = [0] * len(BUCKETS)
_count = 0
_total = 0.0


def observe_acquisition(seconds: float) -> None:
    global _count, _total
    seconds = max(0.0, seconds)
    with _lock:
        _count += 1
        _total += seconds
        for index, boundary in enumerate(BUCKETS):
            if seconds <= boundary:
                _counts[index] += 1


def acquisition_snapshot() -> tuple[list[int], int, float]:
    with _lock:
        return list(_counts), _count, _total


def _instrument_pool(sync_engine) -> None:
    """Wrap the public checkout entry point, including pool wait and setup."""
    from time import perf_counter

    pool = sync_engine.pool
    if getattr(pool, "_escalane_acquisition_measured", False):
        return
    connect = pool.connect

    def measured_connect():
        start = perf_counter()
        try:
            return connect()
        finally:
            observe_acquisition(perf_counter() - start)

    pool.connect = measured_connect
    pool._escalane_acquisition_measured = True


def instrument_engine(engine) -> None:
    """Install once per pool and reattach when SQLAlchemy replaces a disposed pool."""
    from sqlalchemy import event

    _instrument_pool(engine.sync_engine)
    if not event.contains(engine.sync_engine, "engine_disposed", _instrument_pool):
        event.listen(engine.sync_engine, "engine_disposed", _instrument_pool)
