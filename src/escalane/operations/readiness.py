"""Dependency probes for liveness, readiness, and operator health views."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

# Keep this value synchronized with the single Alembic head packaged in
# migrations/versions. The regression test verifies it.
EXPECTED_ALEMBIC_HEAD = "0008"


@dataclass(frozen=True, slots=True)
class DatabaseProbe:
    """Outcome of one connectivity and schema-head probe."""

    reachable: bool
    schema: dict[str, Any] | None = None
    error: str | None = None

    @property
    def healthy(self) -> bool:
        """Return whether the database is reachable at the packaged migration head."""
        return self.reachable and self.schema is not None and self.schema["status"] == "ok"


async def check_schema_version(session: AsyncSession) -> dict[str, Any]:
    """Return a fail-closed status for the database's Alembic revision."""
    try:
        result = await session.execute(text("SELECT version_num FROM alembic_version"))
    except SQLAlchemyError:
        return {"status": "missing", "expected": EXPECTED_ALEMBIC_HEAD}

    versions = [str(version) for version in result.scalars().all()]
    if not versions:
        return {"status": "empty", "expected": EXPECTED_ALEMBIC_HEAD}
    if len(versions) != 1:
        return {
            "status": "multiple",
            "expected": EXPECTED_ALEMBIC_HEAD,
            "actual": versions,
        }
    if versions[0] != EXPECTED_ALEMBIC_HEAD:
        return {
            "status": "stale",
            "expected": EXPECTED_ALEMBIC_HEAD,
            "actual": versions[0],
        }
    return {"status": "ok", "expected": EXPECTED_ALEMBIC_HEAD, "actual": versions[0]}


async def database_responds(session: AsyncSession) -> bool:
    """Run a trivial query on an existing session; database errors propagate."""
    return (await session.scalar(select(1))) is not None


async def probe_database(sessionmaker: async_sessionmaker[AsyncSession]) -> DatabaseProbe:
    """Run a trivial query and the schema-head check in one short session."""
    try:
        async with sessionmaker() as session:
            await session.execute(text("SELECT 1"))
            schema = await check_schema_version(session)
    except Exception as error:
        return DatabaseProbe(reachable=False, error=str(error))
    return DatabaseProbe(reachable=True, schema=schema)


async def ping_redis(redis: Any, probe_key: str) -> None:
    """Round-trip Redis, falling back to a read for clients without ``ping``."""
    if hasattr(redis, "ping"):
        await redis.ping()
    elif hasattr(redis, "get"):
        await redis.get(probe_key)
