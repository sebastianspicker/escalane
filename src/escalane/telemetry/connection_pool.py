"""Connection-pool instrumentation that records bounded acquisition latency."""

from __future__ import annotations

from escalane.telemetry.metrics import observe_acquisition


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
