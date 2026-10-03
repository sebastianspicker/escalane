"""Regression coverage for credential-safe device IDs created through the admin API."""

from __future__ import annotations

import logging
import uuid

import pytest
from sqlalchemy import select

from escalane.persistence.models import Alarm, Device
from tests.support.assertions import expect
from tests.support.clients import app_client
from tests.support.constants import TEST_ADMIN_API_KEY, value_for_test

pytestmark = [pytest.mark.integration]


async def test_admin_generated_device_id_never_exposes_bearer_token(
    engine, sessionmaker, seeded_db, fake_redis, settings, caplog
):
    """Generated device IDs must stay opaque in alarms, logs, and queued events."""
    token = value_for_test("admin-created-device-token")
    caplog.set_level(logging.INFO, logger="escalane")
    async with app_client(settings=settings, engine=engine, redis=fake_redis) as client:
        create_response = await client.post(
            "/v1/admin/devices",
            headers={"X-Admin-Key": TEST_ADMIN_API_KEY},
            json={
                "device_token": token,
                "person_id": "ma-012",
                "room_id": "bg-1.23",
            },
        )
        trigger_response = await client.get("/v1/yealink/alarm", params={"token": token})

    expect(create_response.status_code == 201, create_response.text)
    expect(trigger_response.status_code == 200, trigger_response.text)
    device_id = create_response.json()["device_id"]
    expect(device_id.startswith("device:"))
    expect(token not in device_id)
    uuid.UUID(device_id.removeprefix("device:"))

    async with sessionmaker() as session:
        device = await session.scalar(select(Device).where(Device.device_token == token))
        alarm = await session.scalar(
            select(Alarm).where(Alarm.id == uuid.UUID(trigger_response.json()["alarm_id"]))
        )

    expect(device is not None)
    expect(device.id == device_id)
    expect(alarm is not None)
    expect(alarm.device_id == device_id)
    expect(token not in str(alarm.meta))
    expect(all(token not in str(args) for _name, args in fake_redis.jobs))

    trigger_records = [record for record in caplog.records if record.message == "alarm_triggered"]
    expect(len(trigger_records) == 1)
    expect(trigger_records[0].device_id == device_id)
    expect(token not in trigger_records[0].getMessage())
