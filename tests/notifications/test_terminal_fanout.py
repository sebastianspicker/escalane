"""Permanent target failures stay terminal when a sibling target needs a retry."""

from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import select

from escalane.notifications import workflows
from escalane.notifications.delivery import NotificationDeliveryError, log_notification
from escalane.notifications.dispatch import NotificationService
from escalane.persistence.models import Alarm, AlarmNotification, EscalationTarget


def _status_error(code):
    request = httpx.Request("POST", "https://synthetic.example.test/send")
    return httpx.HTTPStatusError(
        "synthetic", request=request, response=httpx.Response(code, request=request)
    )


async def test_permanent_target_not_repeated_on_sibling_retry(sessionmaker, monkeypatch):
    calls = {"permanent": 0, "transient": 0}

    class Sms:
        def enabled(self):
            return True

        async def send_sms(self, address, message):
            calls[address] += 1
            if address == "permanent":
                raise _status_error(400)
            if calls[address] == 1:
                raise _status_error(503)

    targets = [
        EscalationTarget(id=name, address=name, label="Synthetic", channel="sms", enabled=True)
        for name in calls
    ]
    monkeypatch.setattr(
        "escalane.notifications.targets.get_escalation_targets", AsyncMock(return_value=targets)
    )
    service = NotificationService(None, Sms(), None)
    context = {
        "person_name": "Synthetic",
        "room_label": "Synthetic",
        "site_name": None,
        "severity": "P0",
    }
    async with sessionmaker() as session:
        alarm = Alarm(source="synthetic", event="terminal")
        session.add_all([alarm, *targets])
        await session.commit()
        with pytest.raises(NotificationDeliveryError):
            await service.send(session, alarm, context, step_no=0, ack_url=None)
        await service.send(session, alarm, context, step_no=0, ack_url=None)
        rows = (
            await session.scalars(
                select(AlarmNotification).where(AlarmNotification.alarm_id == alarm.id)
            )
        ).all()
        assert calls == {"permanent": 1, "transient": 2}
        assert sorted(row.result for row in rows) == ["error", "ok", "permanent_error"]


async def test_permanent_state_webhook_not_repeated(sessionmaker, settings, monkeypatch):
    post = AsyncMock(side_effect=_status_error(400))
    monkeypatch.setattr(workflows, "post_webhook_bytes_to_validated_addresses", post)
    monkeypatch.setattr(
        workflows, "_validated_state_webhook_addresses", AsyncMock(return_value=("1.1.1.1",))
    )
    async with sessionmaker() as session:
        alarm = Alarm(source="synthetic", event="terminal-state")
        session.add(alarm)
        await session.commit()
        for _ in range(2):
            await workflows.deliver_state_webhook(
                session,
                alarm,
                state="resolved",
                settings=settings,
                http=None,
                log_notification=log_notification,
            )
        assert post.await_count == 1
        row = await session.scalar(
            select(AlarmNotification).where(AlarmNotification.alarm_id == alarm.id)
        )
        assert row.result == "permanent_error"


async def test_permanent_ticket_and_ack_rejections_are_terminal(sessionmaker):
    from datetime import UTC, datetime

    from escalane.notifications import zammad

    provider = AsyncMock()
    provider.create_ticket.side_effect = _status_error(400)
    provider.add_internal_note.side_effect = _status_error(400)
    async with sessionmaker() as session:
        alarm = Alarm(source="synthetic", event="terminal-ticket")
        session.add(alarm)
        await session.commit()
        for _ in range(2):
            assert await zammad.create_ticket(session, alarm, provider, {}) is None
            assert await zammad.add_ack_note(
                session, alarm.id, 42, "Synthetic", datetime.now(UTC), None, provider
            )
        assert provider.create_ticket.await_count == provider.add_internal_note.await_count == 1
        rows = (
            await session.scalars(
                select(AlarmNotification).where(AlarmNotification.alarm_id == alarm.id)
            )
        ).all()
        assert len(rows) == 2
        assert all(row.result == "permanent_error" for row in rows)
