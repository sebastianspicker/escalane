"""Tests for worker notification retries and escalation handling."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from arq import Retry
from sqlalchemy import select

from escalane.config.settings import Settings
from escalane.contracts.alarms import AlarmStatus
from escalane.notifications.delivery import (
    NotificationAuditError,
    NotificationDeliveryError,
)
from escalane.notifications.workflows import ack_url_for_alarm
from escalane.persistence.models import Alarm, AlarmNotification
from escalane.worker.tasks import _record_delivery_attempt_failure, alarm_created, escalate
from tests.support.assertions import expect
from tests.support.constants import TEST_ADMIN_API_KEY, value_for_test
from tests.support.worker_task_helpers import make_alarm, make_ctx

pytestmark = pytest.mark.unit


def _alarm_task_context(alarm_id: uuid.UUID, ack_token: str, *, job_try: int | None = None):
    """Create an alarm-backed worker context without a database fixture."""
    alarm = MagicMock(spec=Alarm)
    alarm.id = alarm_id
    alarm.deleted_at = None
    alarm.ack_token = ack_token
    alarm.zammad_ticket_id = None
    session = AsyncMock()
    session.get = AsyncMock(return_value=alarm)

    class SessionContext:
        async def __aenter__(self):
            return session

        async def __aexit__(self, *_args):
            return False

    context = {
        "sessionmaker": lambda: SessionContext(),
        "settings": MagicMock(base_url="https://alarm.example.test"),
        "redis": MagicMock(),
    }
    if job_try is not None:
        context["job_try"] = job_try
    return alarm, context


def _ticket_failure_notification() -> MagicMock:
    """Create a stage-zero notifier whose Zammad delivery fails terminally."""
    notification = MagicMock()
    notification.handle_zammad_ticket = AsyncMock(
        side_effect=NotificationDeliveryError("ticket failed")
    )
    notification.send = AsyncMock()
    return notification


def test_ack_url_uses_the_normalized_configured_origin() -> None:
    alarm = MagicMock(spec=Alarm)
    alarm.ack_token = value_for_test("canonical-ack-token")
    settings = Settings(
        _env_file=None,
        simulation_enabled=True,
        admin_api_key=TEST_ADMIN_API_KEY,
        base_url="http://localhost:8080/",
    )

    ack_url = ack_url_for_alarm(
        alarm,
        settings,
        alarm_id=str(uuid.uuid4()),
    )

    expect(ack_url == f"http://localhost:8080/a/{alarm.ack_token}")


def _patch_delivery_dependencies(monkeypatch, notification, enrichment):
    """Install the standard delivery collaborators for an alarm-created retry test."""
    monkeypatch.setattr(
        "escalane.worker.tasks._get_notification_service", lambda _ctx: notification
    )
    monkeypatch.setattr(
        "escalane.worker.tasks.enrich_alarm_context", AsyncMock(return_value=enrichment)
    )
    monkeypatch.setattr("escalane.worker.tasks.restore_zammad_ticket_id", AsyncMock())
    monkeypatch.setattr(
        "escalane.worker.tasks.notification_targets.get_escalation_schedule",
        AsyncMock(return_value=[]),
    )
    delivery = AsyncMock(side_effect=NotificationDeliveryError("ticket failed"))
    monkeypatch.setattr("escalane.worker.tasks.deliver_initial_notifications", delivery)
    return delivery


async def test_alarm_created_schedules_escalations_before_external_side_effects(monkeypatch):
    """A queue failure must remain safe for ARQ to retry."""
    alarm_id = uuid.uuid4()
    _, context = _alarm_task_context(alarm_id, value_for_test("ordering-ack-token"))

    notification = MagicMock()
    notification.handle_zammad_ticket = AsyncMock(return_value=42)
    notification.send = AsyncMock()
    redis = MagicMock()
    redis.enqueue_job = AsyncMock(side_effect=RuntimeError("redis unavailable"))
    enrich = AsyncMock()

    monkeypatch.setattr(
        "escalane.worker.tasks._get_notification_service", lambda _ctx: notification
    )
    monkeypatch.setattr("escalane.worker.tasks.enrich_alarm_context", enrich)
    monkeypatch.setattr(
        "escalane.worker.tasks.notification_targets.get_escalation_schedule",
        AsyncMock(return_value=[(1, 60)]),
    )

    with pytest.raises(Retry) as retry:
        await alarm_created(
            {**context, "redis": redis},
            str(alarm_id),
        )

    expect(retry.value.defer_score == 1_000)
    enrich.assert_not_awaited()
    notification.handle_zammad_ticket.assert_not_awaited()
    notification.send.assert_not_awaited()


async def test_alarm_created_attempts_stage_zero_when_ticket_delivery_fails(monkeypatch):
    """One failed connector must not prevent the remaining stage-zero fan-out."""
    alarm_id = uuid.uuid4()
    _, context = _alarm_task_context(alarm_id, value_for_test("fanout-ack-token"))

    notification = _ticket_failure_notification()
    delivery = _patch_delivery_dependencies(
        monkeypatch,
        notification,
        {
            "person_name": "Test Person",
            "room_label": "Test Room",
            "site_name": "Test Site",
            "severity": "P0",
        },
    )

    with pytest.raises(Retry) as retry:
        await alarm_created(
            context,
            str(alarm_id),
        )

    expect(retry.value.defer_score == 1_000)
    delivery.assert_awaited_once()


async def test_alarm_created_retries_notification_audit_lookup_failure(monkeypatch):
    """Audit-store outages at the task boundary request an ARQ retry."""
    alarm_id = uuid.uuid4()
    _, context = _alarm_task_context(alarm_id, value_for_test("audit-retry-token"))

    notification = MagicMock()
    notification.handle_zammad_ticket = AsyncMock()
    notification.send = AsyncMock()
    monkeypatch.setattr(
        "escalane.worker.tasks._get_notification_service", lambda _ctx: notification
    )
    monkeypatch.setattr("escalane.worker.tasks.restore_zammad_ticket_id", AsyncMock())
    monkeypatch.setattr(
        "escalane.worker.tasks.notification_targets.get_escalation_schedule",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        "escalane.worker.tasks.deliver_initial_notifications",
        AsyncMock(side_effect=NotificationAuditError("audit unavailable")),
    )
    monkeypatch.setattr(
        "escalane.worker.tasks.enrich_alarm_context",
        AsyncMock(return_value={"person_name": "P", "room_label": "R", "severity": "P0"}),
    )

    with pytest.raises(Retry) as retry:
        await alarm_created(
            context,
            str(alarm_id),
        )

    expect(retry.value.defer_score == 1_000)
    notification.handle_zammad_ticket.assert_not_awaited()
    notification.send.assert_not_awaited()


async def test_alarm_created_raises_terminal_delivery_error_at_retry_limit(monkeypatch):
    """The final configured ARQ attempt records exhaustion without requeueing."""
    alarm_id = uuid.uuid4()
    _, context = _alarm_task_context(alarm_id, value_for_test("exhausted-ack-token"), job_try=5)

    notification = _ticket_failure_notification()
    delivery = _patch_delivery_dependencies(monkeypatch, notification, {})
    events: list[str] = []
    monkeypatch.setattr("escalane.worker.tasks.record_event", events.append)

    with pytest.raises(NotificationDeliveryError, match="ticket failed"):
        await alarm_created(
            context,
            str(alarm_id),
        )

    delivery.assert_awaited_once()
    expect(events == ["notification_delivery_exhausted"])


def test_delivery_failure_records_exhaustion_on_final_worker_attempt(monkeypatch):
    events: list[str] = []
    monkeypatch.setattr("escalane.worker.tasks.record_event", events.append)

    _record_delivery_attempt_failure(
        {"job_try": 5}, operation="alarm_created", alarm_id=str(uuid.uuid4())
    )

    expect(events == ["notification_delivery_exhausted"])


@pytest.mark.parametrize(
    ("status", "timestamp_field"),
    [(AlarmStatus.RESOLVED, "resolved_at"), (AlarmStatus.ACKNOWLEDGED, "acked_at")],
)
async def test_escalate_skips_completed_alarm(
    sessionmaker, seeded_db, settings, status, timestamp_field
):
    alarm_id = uuid.uuid4()

    alarm_values = {timestamp_field: datetime.now(UTC)}
    async with sessionmaker() as session:
        session.add(
            make_alarm(
                alarm_id,
                status=status,
                **alarm_values,
            )
        )
        await session.commit()

    await escalate(make_ctx(sessionmaker, settings), str(alarm_id), step_no=1)

    async with sessionmaker() as session:
        rows = (
            await session.scalars(
                select(AlarmNotification).where(AlarmNotification.alarm_id == alarm_id)
            )
        ).all()
    expect(len(rows) == 0)
