"""Tests for escalane/services/notification_service.py: notification dispatch."""

from __future__ import annotations

try:
    from tests.assertions import expect
except ModuleNotFoundError:
    from assertions import expect

import uuid

import httpx
import pytest
from sqlalchemy import select

from escalane.connectors.mock import MockSendXmsClient, MockSignalClient, MockZammadClient
from escalane.connectors.zammad import ZammadConfig
from escalane.core.errors import ConfigurationError
from escalane.db.models import (
    Alarm,
    AlarmNotification,
    EscalationPolicy,
    EscalationStep,
    EscalationTarget,
)
from escalane.services.notification_delivery import NotificationDeliveryError, log_notification
from escalane.services.notification_service import NotificationService
from escalane.types import EnrichedAlarmContext

try:
    from tests.worker_task_helpers import make_alarm
except ModuleNotFoundError:
    from worker_task_helpers import make_alarm

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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


async def _zammad_notification(sessionmaker, alarm_id: uuid.UUID):
    """Fetch the persisted Zammad audit row for a ticket-delivery assertion."""
    async with sessionmaker() as session:
        return await session.scalar(
            select(AlarmNotification)
            .where(AlarmNotification.alarm_id == alarm_id)
            .where(AlarmNotification.channel == "zammad")
        )


async def test_escalation_schedule_rejects_legacy_conflicting_delays(sessionmaker) -> None:
    service = _mock_notification_service()
    async with sessionmaker() as session:
        session.add(EscalationPolicy(id="legacy-conflict", name="Legacy Conflict"))
        session.add_all(
            [
                EscalationTarget(
                    id="legacy-a",
                    label="Legacy A",
                    channel="sms",
                    address="+491111",
                    enabled=True,
                ),
                EscalationTarget(
                    id="legacy-b",
                    label="Legacy B",
                    channel="sms",
                    address="+492222",
                    enabled=True,
                ),
                EscalationStep(
                    policy_id="legacy-conflict",
                    step_no=1,
                    after_seconds=60,
                    target_id="legacy-a",
                ),
                EscalationStep(
                    policy_id="legacy-conflict",
                    step_no=1,
                    after_seconds=120,
                    target_id="legacy-b",
                ),
            ]
        )
        await session.commit()

        with pytest.raises(ConfigurationError, match="conflicting delays"):
            await service.get_escalation_schedule(session, "legacy-conflict")


# ---------------------------------------------------------------------------
# a) test_log_notification_writes_to_db
# ---------------------------------------------------------------------------


async def test_log_notification_writes_to_db(sessionmaker, seeded_db):
    alarm_id = uuid.uuid4()

    async with sessionmaker() as session:
        session.add(make_alarm(alarm_id))
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
    expect(isinstance(row.payload["delivery_id"], str))


# ---------------------------------------------------------------------------
# b) test_build_notification_payload
# ---------------------------------------------------------------------------


async def test_build_notification_payload():
    svc = _mock_notification_service()
    alarm = make_alarm()
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
    alarm = make_alarm()
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
        session.add(make_alarm(alarm_id))
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
        session.add(make_alarm(alarm_id))
        await session.commit()

    svc = _mock_notification_service()

    async with sessionmaker() as session:
        alarm = await session.get(Alarm, alarm_id)
        enriched = _enriched()
        ticket_id = await svc.handle_zammad_ticket(
            session, alarm, enriched, ack_url="http://localhost:8080/a/tok-z"
        )

    expect(ticket_id is not None)
    expect(isinstance(ticket_id, int))

    row = await _zammad_notification(sessionmaker, alarm_id)
    expect(row is not None)
    expect(row.result == "ok")
    expect(row.payload["action"] == "create_ticket")
    expect(row.payload["ticket_id"] == ticket_id)


# ---------------------------------------------------------------------------
# g) test_handle_zammad_ticket_failure
# ---------------------------------------------------------------------------


async def test_handle_zammad_ticket_failure(sessionmaker, seeded_db, caplog):
    alarm_id = uuid.uuid4()

    async with sessionmaker() as session:
        session.add(make_alarm(alarm_id))
        await session.commit()

    class _FailingZammad:
        @property
        def config(self):
            return ZammadConfig(enabled=True, base_url="http://mock-zammad")

        def enabled(self) -> bool:
            return True

        async def create_ticket(self, payload):
            raise httpx.ConnectError("Zammad connection refused with private-provider-value")

    svc = NotificationService(
        zammad=_FailingZammad(),
        sendxms=MockSendXmsClient(),
        signal=MockSignalClient(),
    )

    async with sessionmaker() as session:
        alarm = await session.get(Alarm, alarm_id)
        enriched = _enriched()
        with pytest.raises(NotificationDeliveryError, match="ticket creation"):
            await svc.handle_zammad_ticket(
                session,
                alarm,
                enriched,
                ack_url="http://localhost:8080/a/tok-fail",
            )

    row = await _zammad_notification(sessionmaker, alarm_id)
    expect(row is not None)
    expect(row.result == "error")
    expect(row.error == "Downstream provider transport error")
    expect("private-provider-value" not in caplog.text)
