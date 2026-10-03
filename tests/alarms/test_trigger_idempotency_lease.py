"""Regression coverage for trigger idempotency lease ownership."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from escalane.alarms.triggers import TriggerService
from escalane.persistence.models import Alarm
from tests.support.assertions import expect
from tests.support.constants import TEST_DEVICE_TOKEN

pytestmark = [pytest.mark.integration]


async def test_idempotency_lease_survives_adjacent_time_buckets(
    sessionmaker, seeded_db, fake_redis, settings
):
    """A 30-second reservation must not change ownership at a 10-second boundary."""
    async with sessionmaker() as creator_session:
        creator = TriggerService(
            creator_session,
            fake_redis,
            settings,
            rate_limit_bucket=200,
        )
        first = await creator.process_trigger(
            token=TEST_DEVICE_TOKEN,
            client_ip="127.0.0.1",
            user_agent="first",
        )

    async with sessionmaker() as duplicate_session:
        duplicate = TriggerService(
            duplicate_session,
            fake_redis,
            settings,
            rate_limit_bucket=200,
        )
        second = await duplicate.process_trigger(
            token=TEST_DEVICE_TOKEN,
            client_ip="127.0.0.1",
            user_agent="adjacent-boundary",
        )

        alarms = (await duplicate_session.scalars(select(Alarm))).all()

    expect(first.success)
    expect(second.success)
    expect(second.is_duplicate is True)
    expect(second.alarm_id == first.alarm_id)
    expect(len(alarms) == 1)
