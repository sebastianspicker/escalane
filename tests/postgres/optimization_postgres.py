"""Opt-in PostgreSQL revision transaction and concurrent-writer proof."""

from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from sqlalchemy import delete, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from escalane.alarms.dashboard import dashboard_revision
from escalane.persistence.models import Alarm

pytestmark = pytest.mark.skipif(
    not os.environ.get("TEST_POSTGRES_URL"),
    reason="requires disposable TEST_POSTGRES_URL migrated to head",
)


async def test_revision_rollback_and_concurrent_core_writers():
    engine = create_async_engine(os.environ["TEST_POSTGRES_URL"])
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    ids = [uuid.uuid4(), uuid.uuid4()]
    try:
        async with sessions() as session:
            before = await dashboard_revision(session)
            session.add(Alarm(id=ids[0], source="synthetic", event="optimization.postgres"))
            await session.flush()
            assert await dashboard_revision(session) != before
            await session.rollback()
            assert await dashboard_revision(session) == before
            session.add_all(
                [
                    Alarm(id=value, source="synthetic", event="optimization.postgres")
                    for value in ids
                ]
            )
            await session.commit()
            previous = await dashboard_revision(session)

        async def writer(alarm_id):
            async with sessions() as session:
                await session.execute(
                    update(Alarm).where(Alarm.id == alarm_id).values(severity="P2")
                )
                await session.commit()

        await asyncio.wait_for(asyncio.gather(*(writer(value) for value in ids)), timeout=10)
        async with sessions() as session:
            current = await dashboard_revision(session)
            assert current.split(":")[0] == previous.split(":")[0]
            assert int(current.split(":")[1]) == int(previous.split(":")[1]) + 2
    finally:
        async with sessions() as session:
            await session.execute(delete(Alarm).where(Alarm.id.in_(ids)))
            await session.commit()
        await engine.dispose()


async def test_actual_populated_previous_migration_upgrade():
    """Exercise the real PostgreSQL migrations in an isolated synthetic schema."""
    from pathlib import Path

    from alembic.config import Config
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from alembic.script import ScriptDirectory
    from sqlalchemy import text

    engine = create_async_engine(os.environ["TEST_POSTGRES_URL"])
    schema = "optimization_" + uuid.uuid4().hex
    try:
        async with engine.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
            await connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))

            def upgrade_previous(sync_connection):
                config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
                scripts = ScriptDirectory.from_config(config)
                with Operations.context(MigrationContext.configure(sync_connection)):
                    for revision in reversed(list(scripts.walk_revisions())):
                        if revision.revision == "0008":
                            break
                        revision.module.upgrade()

            await connection.run_sync(upgrade_previous)
            alarm_id = uuid.uuid4()
            await connection.execute(
                text(
                    "INSERT INTO alarms (id, source, event) VALUES (:id, 'synthetic', 'migration')"
                ),
                {"id": alarm_id},
            )
            await connection.execute(
                text(
                    "INSERT INTO alarm_notifications (id, alarm_id, channel, payload, result) "
                    "SELECT gen_random_uuid(), :alarm_id, 'zammad', CAST(:payload AS json), 'ok' "
                    "FROM generate_series(1,1005)"
                ),
                {
                    "alarm_id": alarm_id,
                    "payload": (
                        '{"action":"create_ticket","ticket_id":42,"delivery_id":"preserved"}'
                    ),
                },
            )

            def upgrade_current(sync_connection):
                config = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
                revision = ScriptDirectory.from_config(config).get_revision("0008")
                with Operations.context(MigrationContext.configure(sync_connection)):
                    revision.module.upgrade()

            await connection.run_sync(upgrade_current)
            assert (
                await connection.scalar(
                    text(
                        "SELECT count(*) FROM alarm_notifications "
                        "WHERE logical_delivery_key = 'create_ticket' "
                        "AND payload->>'delivery_id' = 'preserved'"
                    )
                )
                == 1005
            )
            previous = await connection.scalar(
                text("SELECT version FROM dashboard_revision WHERE id=1")
            )
            await connection.execute(text("UPDATE alarms SET severity='P2'"))
            assert (
                await connection.scalar(text("SELECT version FROM dashboard_revision WHERE id=1"))
                == previous + 1
            )
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
    finally:
        await engine.dispose()
