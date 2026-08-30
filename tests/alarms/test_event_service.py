"""Worker-queue contract tests for durable alarm outbox publication."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest

from escalane.alarms.outbox import dispatch_pending_alarm_events
from escalane.config import constants
from escalane.contracts.alarms import AlarmStatus
from escalane.persistence.models import Alarm, AlarmEventOutbox
from tests.support.constants import value_for_test

pytestmark = pytest.mark.integration
logger = logging.getLogger("escalane.tests")


class _CapturingRedis:
    """Capture the ARQ wire contract without depending on ARQ internals."""

    def __init__(self) -> None:
        self.jobs: list[tuple[str, dict[str, str | None], dict[str, Any]]] = []

    async def enqueue_job(self, name: str, payload: dict[str, str | None], **kwargs: Any) -> object:
        self.jobs.append((name, payload, kwargs))
        return object()


def _alarm() -> Alarm:
    return Alarm(
        id=uuid.uuid4(),
        status=AlarmStatus.TRIGGERED,
        source="outbox-contract-test",
        event="alarm.trigger",
        person_id="ma-012",
        room_id="bg-1.23",
        site_id="bg",
        device_id="ylk-t5-10023",
        severity="P0",
        silent=True,
        ack_token=value_for_test("outbox-contract-ack"),
        meta={},
    )


async def test_outbox_publishes_the_stable_worker_contract(sessionmaker, seeded_db) -> None:
    alarm = _alarm()
    redis = _CapturingRedis()
    async with sessionmaker() as session:
        session.add_all(
            [
                alarm,
                AlarmEventOutbox(
                    alarm_id=alarm.id,
                    event_type=constants.EVENT_ALARM_CREATED,
                    payload={},
                    sequence=0,
                ),
                AlarmEventOutbox(
                    alarm_id=alarm.id,
                    event_type=constants.EVENT_ALARM_ACKNOWLEDGED,
                    payload={"acknowledged_by": "Ops", "note": "On it"},
                    sequence=1,
                ),
                AlarmEventOutbox(
                    alarm_id=alarm.id,
                    event_type=constants.EVENT_ALARM_STATE_CHANGED,
                    payload={"old_state": "triggered", "new_state": "acknowledged"},
                    sequence=2,
                ),
            ]
        )
        await session.commit()

        assert (
            await dispatch_pending_alarm_events(session, redis, logger=logger, alarm_id=alarm.id)
            == 3
        )

    expected_payloads = [
        {
            "event_type": constants.EVENT_ALARM_CREATED,
            "alarm_id": str(alarm.id),
        },
        {
            "event_type": constants.EVENT_ALARM_ACKNOWLEDGED,
            "alarm_id": str(alarm.id),
            "acknowledged_by": "Ops",
            "note": "On it",
        },
        {
            "event_type": constants.EVENT_ALARM_STATE_CHANGED,
            "alarm_id": str(alarm.id),
            "old_state": "triggered",
            "new_state": "acknowledged",
        },
    ]
    assert [name for name, _payload, _kwargs in redis.jobs] == ["process_alarm_event"] * 3
    assert [
        {key: value for key, value in payload.items() if key != "timestamp"}
        for _name, payload, _kwargs in redis.jobs
    ] == expected_payloads
    assert [kwargs for _name, _payload, kwargs in redis.jobs] == [
        {"_job_id": f"process_alarm_event:alarm.created:{alarm.id}"},
        {"_job_id": f"process_alarm_event:alarm.acknowledged:{alarm.id}"},
        {"_job_id": f"process_alarm_event:alarm.state_changed:{alarm.id}:acknowledged"},
    ]
    for _name, payload, _kwargs in redis.jobs:
        assert datetime.fromisoformat(payload["timestamp"] or "").tzinfo is UTC
