"""Create database engines with connection-pool safety and optional slow-query telemetry."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

logger = logging.getLogger("escalane")

if TYPE_CHECKING:
    from escalane.config.settings import Settings


def create_async_engine_from_url(
    database_url: str,
    pool_size: int = 5,
    max_overflow: int = 10,
    pool_timeout: int = 30,
    pool_recycle: int = 1800,
    slow_query_log_ms: int = 200,
) -> AsyncEngine:
    """Create a pre-pinged async pool and attach timing only when operators request it."""
    engine = create_async_engine(
        database_url,
        pool_pre_ping=True,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_timeout=pool_timeout,
        pool_recycle=pool_recycle,
    )

    if slow_query_log_ms > 0:
        _install_slow_query_listener(engine.sync_engine, slow_query_log_ms)

    return engine


def create_async_engine_from_settings(settings: Settings) -> AsyncEngine:
    """Build the application engine from one validated settings object."""
    return create_async_engine_from_url(
        settings.database_url,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_timeout=settings.db_pool_timeout,
        pool_recycle=settings.db_pool_recycle,
        slow_query_log_ms=settings.slow_query_log_ms,
    )


def _install_slow_query_listener(sync_engine: Any, threshold_ms: int) -> None:
    """Emit a WARNING log for any query exceeding threshold_ms."""

    @event.listens_for(sync_engine, "before_cursor_execute")
    def _before(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        """Push nested query start times so concurrent cursor hooks pair correctly."""
        conn.info.setdefault("query_start_time", []).append(time.perf_counter())

    @event.listens_for(sync_engine, "after_cursor_execute")
    def _after(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
        """Emit bounded statement context only after the matching timing entry is available."""
        start_times: list[float] = conn.info.get("query_start_time", [])
        if start_times:
            elapsed_ms = (time.perf_counter() - start_times.pop()) * 1000
            if elapsed_ms >= threshold_ms:
                logger.warning(
                    "slow_query",
                    extra={
                        "elapsed_ms": round(elapsed_ms, 1),
                        "threshold_ms": threshold_ms,
                        "statement": statement[:200],
                    },
                )
