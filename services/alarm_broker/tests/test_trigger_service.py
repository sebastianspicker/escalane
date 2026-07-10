from __future__ import annotations

try:
    from tests.assertions import expect
except ModuleNotFoundError:
    from assertions import expect

import uuid

import pytest
from sqlalchemy import select

from alarm_broker.db.models import Alarm
from alarm_broker.services.event_publisher import EventPublisher
from alarm_broker.services.event_service import EventResult, enqueue_alarm_created_event
from alarm_broker.services.trigger_service import TriggerService

try:
    from tests.constants import TEST_DEVICE_TOKEN
except ModuleNotFoundError:
    from constants import TEST_DEVICE_TOKEN

pytestmark = [pytest.mark.integration]


def _trigger_service(session, fake_redis, settings) -> TriggerService:
    return TriggerService(
        session,
        fake_redis,
        settings,
        idempotency_bucket=123,
        rate_limit_bucket=456,
    )


def _expect_delivery_state(alarm: Alarm, *, created: bool, state_changed: bool, error) -> None:
    delivery = alarm.meta["event_delivery"]
    expect(delivery["alarm_created_enqueued"] is created)
    expect(delivery["alarm_state_changed_enqueued"] is state_changed)
    expect(delivery["last_error"] == error)


async def _process_test_trigger(trigger: TriggerService, *, user_agent: str = "pytest"):
    return await trigger.process_trigger(
        token=TEST_DEVICE_TOKEN,
        client_ip="127.0.0.1",
        user_agent=user_agent,
    )


async def test_inflight_duplicate_keeps_reservation_and_returns_conflict(
    sessionmaker, seeded_db, fake_redis, settings, monkeypatch
):
    token = TEST_DEVICE_TOKEN

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

    expect(reserved_alarm_id is not None)

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

        expect(not result.success)
        expect(result.error_code == 409)
        expect(result.error_message == "An alarm for this token is already being created.")
        expect(
            await fake_redis.get(duplicate._get_idempotency_key(token)) == str(reserved_alarm_id)
        )

        alarms = (await session_b.scalars(select(Alarm))).all()
        expect(alarms == [])


async def test_duplicate_retry_recovers_missing_event_enqueue(
    sessionmaker, seeded_db, fake_redis, settings, monkeypatch
):
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
        result = await _process_test_trigger(_trigger_service(session, fake_redis, settings))

        expect(not result.success)
        expect(result.error_code == 503)
        expect(
            result.error_message
            == "Alarm was created, but downstream processing still needs a retry request."
        )

        alarm = await session.scalar(select(Alarm))
        expect(alarm is not None)
        _expect_delivery_state(alarm, created=False, state_changed=False, error="queue unavailable")
        created_alarm_id = alarm.id

    async with sessionmaker() as session:
        recovered = await _process_test_trigger(
            _trigger_service(session, fake_redis, settings),
            user_agent="pytest-retry",
        )

        expect(recovered.success)
        expect(recovered.is_duplicate is True)
        expect(recovered.alarm_id == created_alarm_id)

        alarm = await session.get(Alarm, created_alarm_id)
        expect(alarm is not None)
        _expect_delivery_state(alarm, created=True, state_changed=True, error=None)

    expect(
        [args[0]["event_type"] for _name, args in fake_redis.jobs]
        == ["alarm.created", "alarm.state_changed"]
    )


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

    expect(
        [args[0]["event_type"] for _name, args in fake_redis.jobs]
        == ["alarm.created", "alarm.state_changed", "alarm.state_changed"]
    )
    expect(
        [args[0].get("new_state") for _name, args in fake_redis.jobs[1:]]
        == ["triggered", "acknowledged"]
    )
