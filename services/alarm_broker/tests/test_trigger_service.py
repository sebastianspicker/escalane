from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from alarm_broker.db.models import Alarm
from alarm_broker.services.event_publisher import EventPublisher
from alarm_broker.services.event_service import EventResult, enqueue_alarm_created_event
from alarm_broker.services.trigger_service import TriggerService

pytestmark = [pytest.mark.integration]


async def test_inflight_duplicate_keeps_reservation_and_returns_conflict(
    sessionmaker, seeded_db, fake_redis, settings, monkeypatch
):
    token = "YLK_T54W_3F9A"

    monkeypatch.setattr(TriggerService, "_IDEMPOTENCY_LOOKUP_DELAY_SECONDS", 0)

    async with sessionmaker() as session_a:
        creator = TriggerService(
            session_a,
            fake_redis,
            settings,
            idempotency_bucket=123,
            rate_limit_bucket=456,
        )
        reserved_alarm_id = await creator.reserve_alarm_id(token)

    assert reserved_alarm_id is not None

    async with sessionmaker() as session_b:
        duplicate = TriggerService(
            session_b,
            fake_redis,
            settings,
            idempotency_bucket=123,
            rate_limit_bucket=456,
        )
        result = await duplicate.process_trigger(
            token=token,
            client_ip="127.0.0.1",
            user_agent="pytest",
        )

        assert not result.success
        assert result.error_code == 409
        assert result.error_message == "An alarm for this token is already being created."
        assert await fake_redis.get(duplicate._get_idempotency_key(token)) == str(reserved_alarm_id)

        alarms = (await session_b.scalars(select(Alarm))).all()
        assert alarms == []


async def test_duplicate_retry_recovers_missing_event_enqueue(
    sessionmaker, seeded_db, fake_redis, settings, monkeypatch
):
    token = "YLK_T54W_3F9A"
    calls = {"created": 0}

    monkeypatch.setattr(TriggerService, "_EVENT_ENQUEUE_ATTEMPTS", 1)

    async def flaky_created(redis, *, alarm_id: uuid.UUID, logger):  # noqa: ANN001
        calls["created"] += 1
        if calls["created"] == 1:
            return EventResult(success=False, error="queue unavailable")
        return await enqueue_alarm_created_event(redis, alarm_id=alarm_id, logger=logger)

    monkeypatch.setattr(
        "alarm_broker.services.trigger_service.enqueue_alarm_created_event",
        flaky_created,
    )

    async with sessionmaker() as session:
        trigger = TriggerService(
            session,
            fake_redis,
            settings,
            idempotency_bucket=123,
            rate_limit_bucket=456,
        )
        result = await trigger.process_trigger(
            token=token,
            client_ip="127.0.0.1",
            user_agent="pytest",
        )

        assert not result.success
        assert result.error_code == 503
        assert result.error_message == (
            "Alarm was created, but downstream processing still needs a retry request."
        )

        alarm = await session.scalar(select(Alarm))
        assert alarm is not None
        delivery = alarm.meta["event_delivery"]
        assert delivery["alarm_created_enqueued"] is False
        assert delivery["alarm_state_changed_enqueued"] is False
        assert delivery["last_error"] == "queue unavailable"
        created_alarm_id = alarm.id

    async with sessionmaker() as session:
        retry = TriggerService(
            session,
            fake_redis,
            settings,
            idempotency_bucket=123,
            rate_limit_bucket=456,
        )
        recovered = await retry.process_trigger(
            token=token,
            client_ip="127.0.0.1",
            user_agent="pytest-retry",
        )

        assert recovered.success
        assert recovered.is_duplicate is True
        assert recovered.alarm_id == created_alarm_id

        alarm = await session.get(Alarm, created_alarm_id)
        assert alarm is not None
        delivery = alarm.meta["event_delivery"]
        assert delivery["alarm_created_enqueued"] is True
        assert delivery["alarm_state_changed_enqueued"] is True
        assert delivery["last_error"] is None

    assert [args[0]["event_type"] for _name, args in fake_redis.jobs] == [
        "alarm.created",
        "alarm.state_changed",
    ]


async def test_event_publisher_deduplicates_by_stable_job_id(fake_redis):
    publisher = EventPublisher(fake_redis)

    await publisher.publish_alarm_created(alarm_id="alarm-1")
    await publisher.publish_alarm_created(alarm_id="alarm-1")
    await publisher.publish_alarm_state_changed(
        alarm_id="alarm-1",
        old_state="none",
        new_state="triggered",
    )
    await publisher.publish_alarm_state_changed(
        alarm_id="alarm-1",
        old_state="none",
        new_state="triggered",
    )
    await publisher.publish_alarm_state_changed(
        alarm_id="alarm-1",
        old_state="triggered",
        new_state="acknowledged",
    )

    assert [args[0]["event_type"] for _name, args in fake_redis.jobs] == [
        "alarm.created",
        "alarm.state_changed",
        "alarm.state_changed",
    ]
    assert [args[0].get("new_state") for _name, args in fake_redis.jobs[1:]] == [
        "triggered",
        "acknowledged",
    ]
