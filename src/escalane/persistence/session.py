"""Construct request-scoped sessions from the application-owned async engine."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from escalane.telemetry.connection_pool import instrument_engine


def create_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Keep loaded values usable after commits made by service-level transactions."""
    instrument_engine(engine)
    return async_sessionmaker(engine, expire_on_commit=False)
