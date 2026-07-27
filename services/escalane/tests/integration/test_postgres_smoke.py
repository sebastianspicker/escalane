"""Optional PostgreSQL integration smoke tests for persistence-specific behavior."""

from __future__ import annotations

try:
    from tests.assertions import expect
except ModuleNotFoundError:
    from assertions import expect

import asyncio
import os
import uuid

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import create_async_engine

from escalane.core.errors import ConflictError
from escalane.db.json_merge import merge_json_object
from escalane.db.models import (
    Alarm,
    AlarmEventOutbox,
    AlarmNote,
    AlarmStatus,
    Device,
    Person,
    Room,
    Site,
)
from escalane.db.session import create_sessionmaker
from escalane.services.alarm_service import (
    acknowledge_alarm,
    soft_delete_alarm,
    transition_alarm,
)
from escalane.settings import Settings

try:
    from tests.api_test_helpers import app_client
    from tests.constants import EMPTY_SECRET_VALUE, TEST_ADMIN_API_KEY
    from tests.helpers import FakeRedis, ack_with_csrf
except ModuleNotFoundError:
    from api_test_helpers import app_client
    from constants import EMPTY_SECRET_VALUE, TEST_ADMIN_API_KEY
    from helpers import FakeRedis, ack_with_csrf

pytestmark = [pytest.mark.integration, pytest.mark.postgres]


def _postgres_settings(database_url: str) -> Settings:
    return Settings(
        database_url=database_url,
        redis_url="redis://fake/0",
        base_url="http://localhost:8080",
        admin_api_key=TEST_ADMIN_API_KEY,
        zammad_api_token=EMPTY_SECRET_VALUE,
        sendxms_enabled=False,
        signal_enabled=False,
    )


async def _seed_postgres_device(sessionmaker, *, token: str, device_id: str) -> None:
    async with sessionmaker() as session:
        session.add(Site(id="pg", name="Postgres Site"))
        session.add(Room(id="pg-1.01", site_id="pg", label="Postgres Room", floor="1"))
        session.add(Person(id="pg-person", display_name="Postgres Person", role="Ops", active=True))
        session.add(
            Device(
                id=device_id,
                vendor="yealink",
                model_family="T5",
                account_ext="20001",
                device_token=token,
                person_id="pg-person",
                room_id="pg-1.01",
            )
        )
        await session.commit()


async def _trigger_and_ack_postgres_alarm(
    settings, engine, redis, sessionmaker, *, token: str
) -> uuid.UUID:
    async with app_client(settings=settings, engine=engine, redis=redis) as client:
        alarm_id, ack_token = await _trigger_alarm_and_load_ack_token(client, sessionmaker, token)
        ack_response = await ack_with_csrf(
            client,
            ack_token,
            acked_by="Postgres Tester",
            note="ack via postgres smoke",
        )
        expect(ack_response.status_code == 200, ack_response.text)
        return alarm_id


async def _trigger_alarm_and_load_ack_token(
    client, sessionmaker, token: str
) -> tuple[uuid.UUID, str]:
    """Trigger an alarm and retrieve its persisted acknowledgement token."""
    trigger = await client.get("/v1/yealink/alarm", params={"token": token})
    expect(trigger.status_code in {200}, trigger.text)
    alarm_id = uuid.UUID(trigger.json()["alarm_id"])
    return alarm_id, await _load_ack_token(sessionmaker, alarm_id)


async def _load_ack_token(sessionmaker, alarm_id: uuid.UUID) -> str:
    """Read the acknowledgement token generated for a newly persisted alarm."""
    async with sessionmaker() as session:
        alarm = await session.get(Alarm, alarm_id)
        expect(alarm is not None)
        expect(alarm.ack_token is not None)
        return alarm.ack_token


async def _expect_postgres_alarm_acknowledged(sessionmaker, alarm_id: uuid.UUID) -> None:
    async with sessionmaker() as session:
        alarm = await session.get(Alarm, alarm_id)
        expect(alarm is not None)
        expect(alarm.status == AlarmStatus.ACKNOWLEDGED)
        expect(alarm.acked_by == "Postgres Tester")


@pytest.mark.skipif("TEST_DATABASE_URL" not in os.environ, reason="TEST_DATABASE_URL not set")
async def test_postgres_trigger_and_ack_smoke() -> None:
    database_url = os.environ["TEST_DATABASE_URL"]
    engine = create_async_engine(database_url)
    sessionmaker = create_sessionmaker(engine)
    fake_redis = FakeRedis()
    token = f"PGTOKEN-{uuid.uuid4().hex}"
    device_id = f"pg-device-{uuid.uuid4().hex[:8]}"
    settings = _postgres_settings(database_url)

    try:
        await _seed_postgres_device(sessionmaker, token=token, device_id=device_id)
        alarm_id = await _trigger_and_ack_postgres_alarm(
            settings, engine, fake_redis, sessionmaker, token=token
        )
        await _expect_postgres_alarm_acknowledged(sessionmaker, alarm_id)
    finally:
        await fake_redis.close()
        await engine.dispose()


def _postgres_alarm() -> Alarm:
    return Alarm(
        id=uuid.uuid4(),
        status=AlarmStatus.TRIGGERED,
        source="postgres-concurrency",
        event="alarm.trigger",
        severity="P0",
        silent=True,
        ack_token=f"pg-ack-{uuid.uuid4().hex}",
        meta={},
    )


async def _postgres_alarm_runtime(database_url: str):
    """Create a PostgreSQL engine, session factory, and persisted concurrency-test alarm."""
    engine = create_async_engine(database_url)
    sessionmaker = create_sessionmaker(engine)
    alarm = _postgres_alarm()
    async with sessionmaker() as session:
        session.add(alarm)
        await session.commit()
    return engine, sessionmaker, alarm


@pytest.mark.skipif("TEST_DATABASE_URL" not in os.environ, reason="TEST_DATABASE_URL not set")
async def test_postgres_json_merge_preserves_object_shape_and_existing_keys() -> None:
    engine, sessionmaker, alarm = await _postgres_alarm_runtime(os.environ["TEST_DATABASE_URL"])
    alarm.meta = {"request_id": "keep-me"}
    try:
        async with sessionmaker() as session:
            session.add(alarm)
            await session.commit()
            await session.execute(
                update(Alarm)
                .where(Alarm.id == alarm.id)
                .values(
                    meta=merge_json_object(
                        Alarm.meta,
                        {"operator_context": {"source": "workflow"}},
                        dialect_name=engine.dialect.name,
                    )
                )
                .execution_options(synchronize_session=False)
            )
            await session.commit()
            await session.refresh(alarm)

        assert alarm.meta == {
            "request_id": "keep-me",
            "operator_context": {"source": "workflow"},
        }
    finally:
        await engine.dispose()


@pytest.mark.skipif("TEST_DATABASE_URL" not in os.environ, reason="TEST_DATABASE_URL not set")
async def test_postgres_lifecycle_compare_and_set_has_one_winner() -> None:
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
    sessionmaker = create_sessionmaker(engine)
    alarm = _postgres_alarm()
    try:
        async with sessionmaker() as ack_session, sessionmaker() as resolve_session:
            ack_alarm = await ack_session.get(Alarm, alarm.id)
            resolve_alarm = await resolve_session.get(Alarm, alarm.id)
            assert ack_alarm is not None
            assert resolve_alarm is not None
            results = await asyncio.gather(
                acknowledge_alarm(ack_session, ack_alarm, acked_by="ack-winner"),
                transition_alarm(
                    resolve_session,
                    resolve_alarm,
                    target_status=AlarmStatus.RESOLVED,
                    actor="resolve-winner",
                ),
                return_exceptions=True,
            )

        assert sum(result is True for result in results) == 1
        assert sum(isinstance(result, ConflictError) for result in results) == 1
        async with sessionmaker() as session:
            persisted = await session.get(Alarm, alarm.id)
            event_count = await session.scalar(
                select(func.count())
                .select_from(AlarmEventOutbox)
                .where(AlarmEventOutbox.alarm_id == alarm.id)
            )
        assert persisted is not None
        assert persisted.status in {AlarmStatus.ACKNOWLEDGED, AlarmStatus.RESOLVED}
        assert event_count in {1, 2}
    finally:
        await engine.dispose()


@pytest.mark.skipif("TEST_DATABASE_URL" not in os.environ, reason="TEST_DATABASE_URL not set")
async def test_postgres_concurrent_soft_delete_records_one_winner() -> None:
    engine, sessionmaker, alarm = await _postgres_alarm_runtime(os.environ["TEST_DATABASE_URL"])
    try:
        async with sessionmaker() as first_session, sessionmaker() as second_session:
            first_alarm = await first_session.get(Alarm, alarm.id)
            second_alarm = await second_session.get(Alarm, alarm.id)
            assert first_alarm is not None
            assert second_alarm is not None
            results = await asyncio.gather(
                soft_delete_alarm(
                    first_session,
                    first_alarm,
                    deleted_by="first",
                    note="first note",
                ),
                soft_delete_alarm(
                    second_session,
                    second_alarm,
                    deleted_by="second",
                    note="second note",
                ),
                return_exceptions=True,
            )

        assert sum(result is None for result in results) == 1
        assert sum(isinstance(result, ConflictError) for result in results) == 1
        async with sessionmaker() as session:
            persisted = await session.get(Alarm, alarm.id)
            notes = list(
                (
                    await session.scalars(select(AlarmNote).where(AlarmNote.alarm_id == alarm.id))
                ).all()
            )
        assert persisted is not None
        assert persisted.deleted_by in {"first", "second"}
        assert len(notes) == 1
        assert notes[0].created_by == persisted.deleted_by
    finally:
        await engine.dispose()
