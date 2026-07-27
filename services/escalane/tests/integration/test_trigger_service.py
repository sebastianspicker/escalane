"""Trigger-service idempotency, persistence, and event-publication tests."""

from __future__ import annotations

try:
    from tests.assertions import expect
except ModuleNotFoundError:
    from assertions import expect

import pytest
from sqlalchemy import select

from escalane.db.models import Alarm, AlarmEventOutbox
from escalane.services.event_publisher import EventPublisher
from escalane.services.trigger_service import TriggerService

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
        rate_limit_bucket=456,
    )


async def _process_test_trigger(trigger: TriggerService, *, user_agent: str = "pytest"):
    return await trigger.process_trigger(
        token=TEST_DEVICE_TOKEN,
        client_ip="127.0.0.1",
        user_agent=user_agent,
    )


async def _alarm_events(session, alarm_id) -> list[AlarmEventOutbox]:
    """Load one alarm's durable events in publish order."""
    return list(
        (
            await session.scalars(
                select(AlarmEventOutbox)
                .where(AlarmEventOutbox.alarm_id == alarm_id)
                .order_by(AlarmEventOutbox.sequence)
            )
        ).all()
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
            rate_limit_bucket=456,
        )
        reserved_alarm_id = await creator.reserve_alarm_id(token)

    expect(reserved_alarm_id is not None)

    async with sessionmaker() as session_b:
        duplicate = TriggerService(
            session_b,
            fake_redis,
            settings,
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


async def test_trigger_persists_initial_events_and_duplicate_retries_pending_outbox(
    sessionmaker, seeded_db, fake_redis, settings, monkeypatch
):
    original_enqueue = fake_redis.enqueue_job

    async def unavailable(*_args, **_kwargs):
        raise ConnectionError("queue unavailable")

    monkeypatch.setattr(fake_redis, "enqueue_job", unavailable)

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
        created_alarm_id = alarm.id
        events = await _alarm_events(session, created_alarm_id)
        expect([event.event_type for event in events] == ["alarm.created", "alarm.state_changed"])
        expect([event.sequence for event in events] == [0, 1])
        expect([event.attempts for event in events] == [1, 0])
        expect("event_delivery" not in alarm.meta)

    monkeypatch.setattr(fake_redis, "enqueue_job", original_enqueue)

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
        events = await _alarm_events(session, created_alarm_id)
        expect(all(event.published_at is not None for event in events))

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
