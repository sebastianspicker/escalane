from __future__ import annotations

try:
    from tests.assertions import expect
except ModuleNotFoundError:
    from assertions import expect

import os
import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine

from alarm_broker.api.main import create_app
from alarm_broker.db.models import Alarm, AlarmStatus, Device, Person, Room, Site
from alarm_broker.db.session import create_sessionmaker
from alarm_broker.settings import Settings

try:
    from tests.constants import EMPTY_SECRET_VALUE, TEST_ADMIN_API_KEY
    from tests.helpers import FakeRedis, ack_with_csrf
except ModuleNotFoundError:
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


async def _trigger_and_ack_postgres_alarm(app, sessionmaker, *, token: str) -> uuid.UUID:
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            trigger = await client.get("/v1/yealink/alarm", params={"token": token})
            expect(trigger.status_code == 200, trigger.text)
            alarm_id = uuid.UUID(trigger.json()["alarm_id"])

            async with sessionmaker() as session:
                alarm = await session.get(Alarm, alarm_id)
                expect(alarm is not None)
                expect(alarm.ack_token is not None)
                ack_token = alarm.ack_token

            ack_response = await ack_with_csrf(
                client,
                ack_token,
                acked_by="Postgres Tester",
                note="ack via postgres smoke",
            )
            expect(ack_response.status_code == 200, ack_response.text)
            return alarm_id


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
        app = create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)
        alarm_id = await _trigger_and_ack_postgres_alarm(app, sessionmaker, token=token)
        await _expect_postgres_alarm_acknowledged(sessionmaker, alarm_id)
    finally:
        await fake_redis.close()
        await engine.dispose()
