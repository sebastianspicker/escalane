"""Public trigger-to-acknowledgement API workflow tests."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from escalane.contracts.alarms import AlarmStatus
from escalane.persistence.models import Alarm
from tests.support.api_test_helpers import app_client
from tests.support.assertions import expect
from tests.support.constants import TEST_DEVICE_TOKEN
from tests.support.helpers import ack_with_csrf

pytestmark = [pytest.mark.integration]


async def _trigger_idempotent_alarm(client, fake_redis) -> uuid.UUID:
    r1 = await client.get("/v1/yealink/alarm", params={"token": TEST_DEVICE_TOKEN})
    expect(r1.status_code == 200, r1.text)
    alarm_id_1 = uuid.UUID(r1.json()["alarm_id"])

    r2 = await client.get("/v1/yealink/alarm", params={"token": TEST_DEVICE_TOKEN})
    expect(r2.status_code == 200, r2.text)
    alarm_id_2 = uuid.UUID(r2.json()["alarm_id"])

    expect(alarm_id_1 == alarm_id_2)
    expect(
        [name for name, _args in fake_redis.jobs] == ["process_alarm_event", "process_alarm_event"]
    )
    expect(
        [args[0]["event_type"] for _name, args in fake_redis.jobs]
        == ["alarm.created", "alarm.state_changed"]
    )
    expect(
        [args[0]["alarm_id"] for _name, args in fake_redis.jobs]
        == [str(alarm_id_1), str(alarm_id_1)]
    )
    return alarm_id_1


async def _load_only_alarm(sessionmaker) -> Alarm:
    async with sessionmaker() as session:
        alarms = (await session.scalars(select(Alarm))).all()
        expect(len(alarms) == 1)
        alarm = alarms[0]
        expect(alarm.status == AlarmStatus.TRIGGERED)
        expect(alarm.ack_token)
        return alarm


async def _ack_alarm_via_page(client, ack_token: str) -> None:
    r3 = await client.get(f"/a/{ack_token}")
    expect(r3.status_code == 200)
    expect("Acknowledge alarm" in r3.text)
    r4 = await ack_with_csrf(client, ack_token, acked_by="Tester", note="On my way")
    expect(r4.status_code == 200)


async def test_yealink_idempotent_and_ack(
    engine, sessionmaker, seeded_db, fake_redis, settings, monkeypatch
):
    monkeypatch.setattr("escalane.web.routes.yealink.minute_bucket", lambda: 456)

    async with app_client(settings=settings, engine=engine, redis=fake_redis) as client:
        alarm_id_1 = await _trigger_idempotent_alarm(client, fake_redis)

    alarm = await _load_only_alarm(sessionmaker)

    async with app_client(settings=settings, engine=engine, redis=fake_redis) as client:
        await _ack_alarm_via_page(client, str(alarm.ack_token))

    async with sessionmaker() as session:
        alarm2 = await session.get(Alarm, alarm_id_1)
        expect(alarm2)
        expect(alarm2.status == AlarmStatus.ACKNOWLEDGED)

    expect(
        [name for name, _args in fake_redis.jobs]
        == [
            "process_alarm_event",
            "process_alarm_event",
            "process_alarm_event",
            "process_alarm_event",
        ]
    )
    expect(
        [args[0]["event_type"] for _name, args in fake_redis.jobs]
        == ["alarm.created", "alarm.state_changed", "alarm.acknowledged", "alarm.state_changed"]
    )


async def test_rate_limit_applies_only_to_new_alarms(
    engine, seeded_db, fake_redis, settings, monkeypatch
):
    # Allow 1 new alarm per minute for the test (settings injected into app)
    settings.rate_limit_per_minute = 1
    settings.simulation_enabled = False
    monkeypatch.setattr("escalane.web.routes.yealink.minute_bucket", lambda: 999)

    async with app_client(settings=settings, engine=engine, redis=fake_redis) as client:
        r1 = await client.get("/v1/yealink/alarm", params={"token": TEST_DEVICE_TOKEN})
        expect(r1.status_code == 200)

        r2 = await client.get("/v1/yealink/alarm", params={"token": TEST_DEVICE_TOKEN})
        expect(r2.status_code == 200)
        expect(r2.json()["alarm_id"] == r1.json()["alarm_id"])

        fake_redis.advance(31)
        rate_limited = await client.get("/v1/yealink/alarm", params={"token": TEST_DEVICE_TOKEN})
        expect(rate_limited.status_code == 429)
