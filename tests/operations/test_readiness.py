"""Readiness probes: schema-head status, database probe, and Redis ping fallbacks."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from escalane.operations.readiness import (
    EXPECTED_ALEMBIC_HEAD,
    DatabaseProbe,
    check_schema_version,
    database_responds,
    ping_redis,
    probe_database,
)
from escalane.persistence.session import create_sessionmaker

pytestmark = pytest.mark.unit


async def _set_versions(engine: AsyncEngine, versions: list[str]) -> None:
    async with engine.begin() as connection:
        await connection.execute(text("DELETE FROM alembic_version"))
        for version in versions:
            await connection.execute(
                text("INSERT INTO alembic_version (version_num) VALUES (:v)"), {"v": version}
            )


async def test_schema_version_ok_at_expected_head(sessionmaker) -> None:
    async with sessionmaker() as session:
        status = await check_schema_version(session)
    assert status == {
        "status": "ok",
        "expected": EXPECTED_ALEMBIC_HEAD,
        "actual": EXPECTED_ALEMBIC_HEAD,
    }


async def test_schema_version_missing_table(engine, sessionmaker) -> None:
    async with engine.begin() as connection:
        await connection.execute(text("DROP TABLE alembic_version"))
    async with sessionmaker() as session:
        status = await check_schema_version(session)
    assert status == {"status": "missing", "expected": EXPECTED_ALEMBIC_HEAD}


async def test_schema_version_empty_table(engine, sessionmaker) -> None:
    await _set_versions(engine, [])
    async with sessionmaker() as session:
        status = await check_schema_version(session)
    assert status == {"status": "empty", "expected": EXPECTED_ALEMBIC_HEAD}


async def test_schema_version_multiple_heads(engine, sessionmaker) -> None:
    await _set_versions(engine, [EXPECTED_ALEMBIC_HEAD, "0099"])
    async with sessionmaker() as session:
        status = await check_schema_version(session)
    assert status["status"] == "multiple"
    assert status["expected"] == EXPECTED_ALEMBIC_HEAD
    assert sorted(status["actual"]) == sorted([EXPECTED_ALEMBIC_HEAD, "0099"])


async def test_schema_version_stale(engine, sessionmaker) -> None:
    await _set_versions(engine, ["0001"])
    async with sessionmaker() as session:
        status = await check_schema_version(session)
    assert status == {"status": "stale", "expected": EXPECTED_ALEMBIC_HEAD, "actual": "0001"}


async def test_probe_database_reachable_and_healthy(sessionmaker) -> None:
    probe = await probe_database(sessionmaker)
    assert probe.reachable is True
    assert probe.error is None
    assert probe.schema is not None and probe.schema["status"] == "ok"
    assert probe.healthy is True


async def test_probe_database_reachable_but_stale_schema_is_unhealthy(engine, sessionmaker) -> None:
    await _set_versions(engine, ["0001"])
    probe = await probe_database(sessionmaker)
    assert probe.reachable is True
    assert probe.schema is not None and probe.schema["status"] == "stale"
    assert probe.healthy is False


async def test_probe_database_unreachable_reports_error(tmp_path: Path) -> None:
    missing_directory = tmp_path / "does-not-exist" / "db.sqlite"
    unreachable = create_async_engine(f"sqlite+aiosqlite:///{missing_directory}")
    try:
        probe = await probe_database(create_sessionmaker(unreachable))
    finally:
        await unreachable.dispose()
    assert probe.reachable is False
    assert probe.schema is None
    assert probe.error
    assert probe.healthy is False


async def test_probe_database_swallows_any_session_failure() -> None:
    class ExplodingSessionmaker:
        def __call__(self):
            raise RuntimeError("pool exhausted")

    probe = await probe_database(ExplodingSessionmaker())  # type: ignore[arg-type]
    assert probe == DatabaseProbe(reachable=False, error="pool exhausted")


def test_database_probe_healthy_requires_reachable_and_ok_schema() -> None:
    assert DatabaseProbe(reachable=True, schema={"status": "ok"}).healthy is True
    assert DatabaseProbe(reachable=True, schema=None).healthy is False
    assert DatabaseProbe(reachable=True, schema={"status": "empty"}).healthy is False
    assert DatabaseProbe(reachable=False, schema={"status": "ok"}).healthy is False


async def test_database_responds_true(sessionmaker: async_sessionmaker) -> None:
    async with sessionmaker() as session:
        assert await database_responds(session) is True


async def test_database_responds_propagates_database_errors(tmp_path: Path) -> None:
    unreachable = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'nope' / 'db.sqlite'}")
    try:
        async with create_sessionmaker(unreachable)() as session:
            with pytest.raises(OperationalError):
                await database_responds(session)
    finally:
        await unreachable.dispose()


async def test_ping_redis_prefers_ping() -> None:
    calls: list[str] = []

    class WithPing:
        async def ping(self) -> bool:
            calls.append("ping")
            return True

        async def get(self, key: str) -> None:
            calls.append("get")

    await ping_redis(WithPing(), "probe")
    assert calls == ["ping"]


async def test_ping_redis_falls_back_to_get_with_probe_key() -> None:
    keys: list[str] = []

    class GetOnly:
        async def get(self, key: str) -> None:
            keys.append(key)

    await ping_redis(GetOnly(), "probe-key")
    assert keys == ["probe-key"]


async def test_ping_redis_without_ping_or_get_is_a_noop() -> None:
    await ping_redis(object(), "probe")


async def test_ping_redis_propagates_connection_errors() -> None:
    class Down:
        async def ping(self) -> None:
            raise ConnectionError("redis down")

    with pytest.raises(ConnectionError, match="redis down"):
        await ping_redis(Down(), "probe")
