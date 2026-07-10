"""Tests for alarm_broker/services/notification_service.py — notification dispatch."""

from __future__ import annotations

try:
    from tests.assertions import expect
except ModuleNotFoundError:
    from assertions import expect

import uuid
from datetime import UTC, datetime

from sqlalchemy import select

from alarm_broker.connectors.mock import MockSendXmsClient, MockSignalClient, MockZammadClient
from alarm_broker.connectors.zammad import ZammadConfig
from alarm_broker.db.models import (
    Alarm,
    AlarmNotification,
    AlarmStatus,
    EscalationPolicy,
    EscalationStep,
    EscalationTarget,
)
from alarm_broker.services.notification_service import NotificationService, log_notification
from alarm_broker.types import EnrichedAlarmContext

try:
    from tests.constants import value_for_test
except ModuleNotFoundError:
    from constants import value_for_test

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_alarm(alarm_id: uuid.UUID | None = None) -> Alarm:
    """Create a minimal Alarm instance for testing."""
    return Alarm(
        id=alarm_id or uuid.uuid4(),
        status=AlarmStatus.TRIGGERED,
        source="test",
        event="alarm.trigger",
        person_id="ma-012",
        room_id="bg-1.23",
        site_id="bg",
        device_id="ylk-t5-10023",
        severity="P0",
        silent=True,
        ack_token=value_for_test("notification-dispatch") + uuid.uuid4().hex[:8],
        created_at=datetime.now(UTC),
        meta={},
    )


def _enriched() -> EnrichedAlarmContext:
    return EnrichedAlarmContext(
        person_name="Person X",
        room_label="Raum 1.23",
        site_name="Standort BG",
        severity="P0",
    )


def _mock_notification_service() -> NotificationService:
    return NotificationService(
        zammad=MockZammadClient(),
        sendxms=MockSendXmsClient(),
        signal=MockSignalClient(),
    )


async def _add_default_escalation_targets(session) -> None:
    session.add(EscalationPolicy(id="default", name="Default Policy"))
    session.add(
        EscalationTarget(
            id="tgt-sms-2",
            label="SMS Target",
            channel="sms",
            address="+491111",
            enabled=True,
        )
    )
    session.add(
        EscalationTarget(
            id="tgt-signal-2",
            label="Signal Group",
            channel="signal",
            address="group-xyz",
            enabled=True,
        )
    )
    session.add(
        EscalationStep(policy_id="default", step_no=0, after_seconds=0, target_id="tgt-sms-2")
    )
    session.add(
        EscalationStep(policy_id="default", step_no=0, after_seconds=0, target_id="tgt-signal-2")
    )


async def _send_stage_zero_notifications(sessionmaker, alarm_id: uuid.UUID):
    svc = _mock_notification_service()
    async with sessionmaker() as session:
        alarm = await session.get(Alarm, alarm_id)
        await svc.send(
            session=session,
            alarm=alarm,
            enriched=_enriched(),
            step_no=0,
            ack_url="http://localhost:8080/a/tok-dispatch",
        )
        return (
            await session.scalars(
                select(AlarmNotification).where(AlarmNotification.alarm_id == alarm_id)
            )
        ).all()


# ---------------------------------------------------------------------------
# a) test_log_notification_writes_to_db
# ---------------------------------------------------------------------------


async def test_log_notification_writes_to_db(sessionmaker, seeded_db):
    alarm_id = uuid.uuid4()

    async with sessionmaker() as session:
        session.add(_make_alarm(alarm_id))
        await session.commit()

        await log_notification(
            session,
            alarm_id=alarm_id,
            channel="sms",
            target_id="tgt-1",
            payload={"body": "hello"},
            result="ok",
            error=None,
        )

        row = await session.scalar(
            select(AlarmNotification)
            .where(AlarmNotification.alarm_id == alarm_id)
            .where(AlarmNotification.channel == "sms")
        )

    expect(row is not None)
    expect(row.result == "ok")
    expect(row.error is None)
    expect(row.target_id == "tgt-1")
    expect(row.payload["body"] == "hello")


# ---------------------------------------------------------------------------
# b) test_build_notification_payload
# ---------------------------------------------------------------------------


async def test_build_notification_payload():
    svc = _mock_notification_service()
    alarm = _make_alarm()
    enriched = _enriched()

    payload = svc._build_notification_payload(
        alarm=alarm,
        enriched=enriched,
        step_no=0,
        ack_url="http://localhost:8080/a/tok-abc",
    )

    expect(payload["alarm_id"] == str(alarm.id))
    expect(payload["step_no"] == 0)
    expect(payload["priority"] == 3)  # P0 -> 3
    expect("NOTFALLALARM" in payload["title"])
    expect("Person X" in payload["title"])
    expect("Raum 1.23" in payload["title"])
    expect("Person X" in payload["body"])
    expect(isinstance(payload["tags"], list))


# ---------------------------------------------------------------------------
# c) test_build_zammad_ticket_payload
# ---------------------------------------------------------------------------


async def test_build_zammad_ticket_payload():
    svc = _mock_notification_service()
    alarm = _make_alarm()
    enriched = _enriched()

    notification_payload = svc._build_notification_payload(
        alarm=alarm,
        enriched=enriched,
        step_no=0,
        ack_url="http://localhost:8080/a/tok-abc",
    )
    zammad_payload = svc._build_zammad_ticket_payload(notification_payload)

    expect(zammad_payload["title"] == notification_payload["title"])
    expect(zammad_payload["priority_id"] == notification_payload["priority"])
    expect("group" in zammad_payload)
    expect("state_id" in zammad_payload)
    expect("customer_id" in zammad_payload)
    expect(zammad_payload["article"]["body"] == notification_payload["body"])
    expect(zammad_payload["article"]["type"] == "note")
    expect(zammad_payload["article"]["internal"] is True)


# ---------------------------------------------------------------------------
# d) test_get_escalation_targets
# ---------------------------------------------------------------------------


async def test_get_escalation_targets(sessionmaker, seeded_db):
    async with sessionmaker() as session:
        session.add(EscalationPolicy(id="default", name="Default Policy"))
        session.add(
            EscalationTarget(
                id="tgt-sms-1",
                label="SMS Target",
                channel="sms",
                address="+491234",
                enabled=True,
            )
        )
        session.add(
            EscalationTarget(
                id="tgt-signal-1",
                label="Signal Group",
                channel="signal",
                address="group-id-abc",
                enabled=True,
            )
        )
        session.add(
            EscalationTarget(
                id="tgt-disabled",
                label="Disabled Target",
                channel="email",
                address="test@example.org",
                enabled=False,
            )
        )
        session.add(
            EscalationStep(policy_id="default", step_no=0, after_seconds=0, target_id="tgt-sms-1")
        )
        session.add(
            EscalationStep(
                policy_id="default", step_no=0, after_seconds=0, target_id="tgt-signal-1"
            )
        )
        session.add(
            EscalationStep(
                policy_id="default", step_no=0, after_seconds=0, target_id="tgt-disabled"
            )
        )
        await session.commit()

        svc = _mock_notification_service()
        targets = await svc._get_escalation_targets(session, "default", 0)

    # Only enabled targets should be returned
    expect(len(targets) == 2)
    channels = {t.channel for t in targets}
    expect(channels == {"sms", "signal"})


# ---------------------------------------------------------------------------
# e) test_send_dispatches_to_channels
# ---------------------------------------------------------------------------


async def test_send_dispatches_to_channels(sessionmaker, seeded_db):
    alarm_id = uuid.uuid4()

    async with sessionmaker() as session:
        session.add(_make_alarm(alarm_id))
        await _add_default_escalation_targets(session)
        await session.commit()

    rows = await _send_stage_zero_notifications(sessionmaker, alarm_id)
    expect(len(rows) == 2)
    channels = {r.channel for r in rows}
    expect("sms" in channels)
    expect("signal" in channels)
    expect(all(r.result == "ok" for r in rows))


# ---------------------------------------------------------------------------
# f) test_handle_zammad_ticket_success
# ---------------------------------------------------------------------------


async def test_handle_zammad_ticket_success(sessionmaker, seeded_db):
    alarm_id = uuid.uuid4()

    async with sessionmaker() as session:
        session.add(_make_alarm(alarm_id))
        await session.commit()

    svc = _mock_notification_service()

    async with sessionmaker() as session:
        alarm = await session.get(Alarm, alarm_id)
        enriched = _enriched()
        ticket_id = await svc.handle_zammad_ticket(
            session, alarm, enriched, ack_url="http://localhost:8080/a/tok-z", settings=None
        )

    expect(ticket_id is not None)
    expect(isinstance(ticket_id, int))

    async with sessionmaker() as session:
        row = await session.scalar(
            select(AlarmNotification)
            .where(AlarmNotification.alarm_id == alarm_id)
            .where(AlarmNotification.channel == "zammad")
        )
    expect(row is not None)
    expect(row.result == "ok")
    expect(row.payload["action"] == "create_ticket")
    expect(row.payload["ticket_id"] == ticket_id)


# ---------------------------------------------------------------------------
# g) test_handle_zammad_ticket_failure
# ---------------------------------------------------------------------------


async def test_handle_zammad_ticket_failure(sessionmaker, seeded_db):
    alarm_id = uuid.uuid4()

    async with sessionmaker() as session:
        session.add(_make_alarm(alarm_id))
        await session.commit()

    class _FailingZammad:
        @property
        def config(self):
            return ZammadConfig(enabled=True, base_url="http://mock-zammad")

        def enabled(self) -> bool:
            return True

        async def create_ticket(self, payload):
            raise RuntimeError("Zammad connection refused")

    svc = NotificationService(
        zammad=_FailingZammad(),
        sendxms=MockSendXmsClient(),
        signal=MockSignalClient(),
    )

    async with sessionmaker() as session:
        alarm = await session.get(Alarm, alarm_id)
        enriched = _enriched()
        ticket_id = await svc.handle_zammad_ticket(
            session, alarm, enriched, ack_url="http://localhost:8080/a/tok-fail", settings=None
        )

    expect(ticket_id is None)

    async with sessionmaker() as session:
        row = await session.scalar(
            select(AlarmNotification)
            .where(AlarmNotification.alarm_id == alarm_id)
            .where(AlarmNotification.channel == "zammad")
        )
    expect(row is not None)
    expect(row.result == "error")
    expect("connection refused" in row.error.lower())
