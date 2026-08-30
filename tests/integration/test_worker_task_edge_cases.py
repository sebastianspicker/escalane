"""Worker task recovery, retry, and failure edge cases."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from arq import Retry
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from escalane.config import constants
from escalane.notifications.delivery import NotificationDeliveryError
from escalane.persistence.models import Alarm, AlarmEventOutbox, AlarmNotification
from escalane.providers.mock import MockZammadClient
from escalane.security.url_validation import RetryableSSRFError, SSRFError
from escalane.worker.tasks import (
    alarm_acked,
    alarm_created,
    alarm_state_changed,
    process_alarm_event,
    recover_incomplete_alarm_events,
)
from tests.support.assertions import expect
from tests.support.constants import TEST_WEBHOOK_SECRET
from tests.support.worker_task_helpers import make_alarm, make_ctx

# Keep local test calls readable while sharing the factories with sibling worker suites.
_make_alarm = make_alarm
_make_ctx = make_ctx

pytestmark = pytest.mark.unit


async def _persist_terminal_ack_event(sessionmaker, alarm_id: uuid.UUID) -> None:
    """Store an ACK event that is old enough for recovery after terminal delivery failure."""
    async with sessionmaker() as session:
        session.add(_make_alarm(alarm_id, zammad_ticket_id=None))
        session.add(
            AlarmEventOutbox(
                alarm_id=alarm_id,
                event_type=constants.EVENT_ALARM_ACKNOWLEDGED,
                payload={"acknowledged_by": "user"},
                published_at=datetime.now(UTC) - timedelta(minutes=11),
            )
        )
        await session.commit()


async def _set_ticket_id(sessionmaker, alarm_id: uuid.UUID, ticket_id: int = 42) -> None:
    """Make a recovered ACK event deliverable by assigning its Zammad ticket."""
    async with sessionmaker() as session:
        alarm = await session.get(Alarm, alarm_id)
        expect(alarm is not None)
        alarm.zammad_ticket_id = ticket_id
        await session.commit()


async def test_process_alarm_event_created_dispatches(sessionmaker, seeded_db, settings):
    """EVENT_ALARM_CREATED dispatches to alarm_created."""
    alarm_id = uuid.uuid4()

    async with sessionmaker() as session:
        session.add(_make_alarm(alarm_id))
        await session.commit()

    ctx = _make_ctx(sessionmaker, settings)

    # alarm_created calls notification.send: with mock connectors it's a no-op
    await process_alarm_event(
        ctx,
        {
            "event_type": constants.EVENT_ALARM_CREATED,
            "alarm_id": str(alarm_id),
        },
    )


async def test_process_alarm_event_acknowledged_dispatches(sessionmaker, seeded_db, settings):
    """EVENT_ALARM_ACKNOWLEDGED dispatches to alarm_acked."""
    alarm_id = uuid.uuid4()

    async with sessionmaker() as session:
        session.add(_make_alarm(alarm_id, zammad_ticket_id=42))
        await session.commit()

    ctx = _make_ctx(sessionmaker, settings)

    await process_alarm_event(
        ctx,
        {
            "event_type": constants.EVENT_ALARM_ACKNOWLEDGED,
            "alarm_id": str(alarm_id),
            "acknowledged_by": "user@test.com",
            "note": "looks good",
        },
    )


async def test_process_alarm_event_missing_alarm_id_returns_early(
    sessionmaker, seeded_db, settings
):
    """Missing alarm_id in payload returns early without raising."""
    ctx = _make_ctx(sessionmaker, settings)

    await process_alarm_event(
        ctx,
        {"event_type": constants.EVENT_ALARM_CREATED},  # alarm_id missing
    )


async def test_process_alarm_event_missing_event_type_returns_early(
    sessionmaker, seeded_db, settings
):
    """Missing event_type in payload returns early without raising."""
    ctx = _make_ctx(sessionmaker, settings)

    await process_alarm_event(
        ctx,
        {"alarm_id": str(uuid.uuid4())},  # event_type missing
    )


# ── alarm_created: alarm not found in DB ─────────────────────────────


async def test_alarm_created_alarm_not_found_returns_early(sessionmaker, seeded_db, settings):
    """alarm_created logs warning and returns if alarm not in DB."""
    ctx = _make_ctx(sessionmaker, settings)
    nonexistent_id = str(uuid.uuid4())

    # Should not raise
    await alarm_created(ctx, nonexistent_id)


async def test_alarm_created_missing_ack_token_still_sends(sessionmaker, seeded_db, settings):
    """alarm_created works when ack_token is None: sends with ack_url=None."""
    alarm_id = uuid.uuid4()

    async with sessionmaker() as session:
        session.add(_make_alarm(alarm_id, ack_token=None))
        await session.commit()

    ctx = _make_ctx(sessionmaker, settings)

    # Should not raise
    await alarm_created(ctx, str(alarm_id))


# ── escalate: alarm not in DB ─────────────────────────────────────────


async def test_escalate_alarm_not_found_returns_early(sessionmaker, seeded_db, settings):
    """escalate logs warning and returns if alarm not in DB."""
    from escalane.worker.tasks import escalate

    ctx = _make_ctx(sessionmaker, settings)

    await escalate(ctx, str(uuid.uuid4()), step_no=1)


# ── alarm_acked: no zammad_ticket_id ─────────────────────────────────


async def test_alarm_acked_no_ticket_id_retries_when_zammad_enabled(
    sessionmaker, seeded_db, settings
):
    """ACK waits for an in-flight Zammad ticket instead of losing its note."""
    alarm_id = uuid.uuid4()

    async with sessionmaker() as session:
        session.add(_make_alarm(alarm_id, zammad_ticket_id=None))
        await session.commit()

    ctx = _make_ctx(sessionmaker, settings)

    with pytest.raises(Retry) as retry:
        await alarm_acked(ctx, str(alarm_id), acked_by="user", note=None)

    expect(retry.value.defer_score == 1_000)


async def test_alarm_acked_no_ticket_id_raises_terminal_error_at_retry_limit(
    sessionmaker, seeded_db, settings
):
    """The ACK task stops retrying after the configured delivery budget."""
    alarm_id = uuid.uuid4()

    async with sessionmaker() as session:
        session.add(_make_alarm(alarm_id, zammad_ticket_id=None))
        await session.commit()

    ctx = _make_ctx(sessionmaker, settings)
    ctx["job_try"] = 5

    with pytest.raises(NotificationDeliveryError, match="ticket creation is incomplete"):
        await alarm_acked(ctx, str(alarm_id), acked_by="user", note=None)


async def test_terminal_ack_failure_rearms_durable_outbox_event(sessionmaker, seeded_db, settings):
    """A terminal ACK remains durable and is replayed after its ticket appears."""
    alarm_id = uuid.uuid4()
    await _persist_terminal_ack_event(sessionmaker, alarm_id)

    ctx = _make_ctx(sessionmaker, settings)
    ctx["job_try"] = 5
    with pytest.raises(NotificationDeliveryError, match="ticket creation is incomplete"):
        await process_alarm_event(
            ctx,
            {
                "event_type": constants.EVENT_ALARM_ACKNOWLEDGED,
                "alarm_id": str(alarm_id),
                "acknowledged_by": "user",
            },
        )

    async with sessionmaker() as session:
        event = await session.scalar(
            select(AlarmEventOutbox).where(AlarmEventOutbox.alarm_id == alarm_id)
        )
    expect(event is not None)
    expect(event.published_at is not None)
    expect(event.last_error == "Zammad ticket creation is incomplete")

    await _set_ticket_id(sessionmaker, alarm_id)

    await recover_incomplete_alarm_events(ctx)
    queued_payloads = [args[0] for name, args in ctx["redis"].jobs if name == "process_alarm_event"]
    expect(len(queued_payloads) == 1)
    expect(queued_payloads[0]["event_type"] == constants.EVENT_ALARM_ACKNOWLEDGED)


async def test_ack_recovery_survives_failure_record_database_error(
    sessionmaker, seeded_db, settings
):
    """The stale-event reconciler closes a failed terminal-error audit write."""
    alarm_id = uuid.uuid4()
    await _persist_terminal_ack_event(sessionmaker, alarm_id)

    ctx = _make_ctx(sessionmaker, settings)
    ctx["job_try"] = 5
    with (
        patch(
            "escalane.worker.tasks.record_published_alarm_event_failure",
            AsyncMock(side_effect=SQLAlchemyError("database unavailable")),
        ),
        pytest.raises(NotificationDeliveryError, match="ticket creation is incomplete"),
    ):
        await process_alarm_event(
            ctx,
            {
                "event_type": constants.EVENT_ALARM_ACKNOWLEDGED,
                "alarm_id": str(alarm_id),
                "acknowledged_by": "user",
            },
        )

    await _set_ticket_id(sessionmaker, alarm_id)

    await recover_incomplete_alarm_events(ctx)
    queued_payloads = [args[0] for name, args in ctx["redis"].jobs if name == "process_alarm_event"]
    expect(len(queued_payloads) == 1)


# ── alarm_state_changed: SSRF-blocked webhook URL ────────────────────


async def test_alarm_state_changed_ssrf_blocked_logs_error(sessionmaker, seeded_db, settings):
    """alarm_state_changed logs error and returns if webhook URL is SSRF-blocked."""
    alarm_id = uuid.uuid4()

    async with sessionmaker() as session:
        session.add(_make_alarm(alarm_id))
        await session.commit()

    settings.webhook_enabled = True
    settings.webhook_url = "http://169.254.169.254/metadata"
    settings.webhook_secret = TEST_WEBHOOK_SECRET
    settings.webhook_timeout_seconds = 5
    settings.webhook_allowed_hosts = "169.254.169.254"

    ctx = _make_ctx(sessionmaker, settings)

    with patch(
        "escalane.notifications.workflows.validate_url_not_internal",
        new_callable=AsyncMock,
        side_effect=SSRFError("SSRF blocked"),
    ):
        await alarm_state_changed(ctx, str(alarm_id), "triggered")


async def test_alarm_state_changed_retries_dns_resolution_failures(
    sessionmaker, seeded_db, settings
):
    """Transient resolver failures reach ARQ as Retry instead of being terminally skipped."""
    alarm_id = uuid.uuid4()

    async with sessionmaker() as session:
        session.add(_make_alarm(alarm_id))
        await session.commit()

    settings.webhook_enabled = True
    settings.webhook_url = "https://hooks.example.test/notify"
    settings.webhook_secret = TEST_WEBHOOK_SECRET
    settings.webhook_timeout_seconds = 5
    settings.webhook_allowed_hosts = "hooks.example.test"
    ctx = _make_ctx(sessionmaker, settings)

    with patch(
        "escalane.notifications.workflows.validate_url_not_internal",
        new_callable=AsyncMock,
        side_effect=RetryableSSRFError("resolver unavailable"),
    ):
        with pytest.raises(Retry) as retry:
            await alarm_state_changed(ctx, str(alarm_id), "triggered")

    expect(retry.value.defer_score == 1_000)
    async with sessionmaker() as session:
        audit = await session.scalar(
            select(AlarmNotification)
            .where(AlarmNotification.alarm_id == alarm_id)
            .where(AlarmNotification.channel == "webhook")
        )
    expect(audit is not None)
    expect(audit.result == "error")
    expect(audit.error is not None)


# ── alarm_state_changed: webhook disabled ─────────────────────────────


async def test_alarm_state_changed_webhook_disabled_returns_early(
    sessionmaker, seeded_db, settings
):
    """alarm_state_changed returns immediately if webhook is not enabled."""
    alarm_id = uuid.uuid4()

    async with sessionmaker() as session:
        session.add(_make_alarm(alarm_id))
        await session.commit()

    settings.webhook_enabled = False
    ctx = _make_ctx(sessionmaker, settings)

    # No mock router: would fail if an HTTP call was made
    await alarm_state_changed(ctx, str(alarm_id), "triggered")


# ── alarm_state_changed: alarm not found ──────────────────────────────


async def test_alarm_state_changed_alarm_not_found_returns_early(sessionmaker, seeded_db, settings):
    """alarm_state_changed returns early if alarm is not in DB."""
    settings.webhook_enabled = True
    settings.webhook_url = "https://hooks.example.test/nf"
    settings.webhook_timeout_seconds = 5
    settings.webhook_allowed_hosts = "hooks.example.test"

    ctx = _make_ctx(sessionmaker, settings)

    async def allow_webhook(_url: str) -> None:
        return None

    with patch("escalane.notifications.workflows.validate_url_not_internal", allow_webhook):
        await alarm_state_changed(ctx, str(uuid.uuid4()), "triggered")


# ── recover_incomplete_alarm_events: empty DB ─────────────────────────


async def test_recover_incomplete_alarm_events_empty_db(sessionmaker, seeded_db, settings):
    """recover_incomplete_alarm_events returns cleanly when no alarms exist."""
    from escalane.worker.tasks import recover_incomplete_alarm_events

    ctx = _make_ctx(sessionmaker, settings)

    await recover_incomplete_alarm_events(ctx)


# ── recover_incomplete_alarm_events: complete alarm is skipped ─────────


async def test_recover_incomplete_alarm_events_skips_complete_alarm(
    sessionmaker, seeded_db, settings
):
    """Recovery does not enqueue work when the durable outbox is empty."""
    from escalane.worker.tasks import recover_incomplete_alarm_events

    alarm_id = uuid.uuid4()

    async with sessionmaker() as session:
        session.add(_make_alarm(alarm_id, meta={}))
        await session.commit()

    ctx = _make_ctx(sessionmaker, settings)

    await recover_incomplete_alarm_events(ctx)
    expect(ctx["redis"].jobs == [])


# ── alarm_created: escalation schedule schedules future steps ─────────


async def test_alarm_created_schedules_escalation_steps(sessionmaker, seeded_db, settings):
    """alarm_created enqueues escalation jobs when the schedule has future steps."""
    from escalane.persistence.models import EscalationPolicy, EscalationStep, EscalationTarget

    alarm_id = uuid.uuid4()

    async with sessionmaker() as session:
        session.add(_make_alarm(alarm_id))
        session.add(EscalationPolicy(id="default", name="Default"))
        session.add(
            EscalationTarget(
                id="tgt-esc", label="SMS", channel="sms", address="+491234", enabled=True
            )
        )
        session.add(
            EscalationStep(policy_id="default", step_no=1, after_seconds=300, target_id="tgt-esc")
        )
        await session.commit()

    ctx = _make_ctx(sessionmaker, settings)

    await alarm_created(ctx, str(alarm_id))

    # The FakeRedis should have an "escalate" job enqueued
    job_names = [name for name, _args in ctx["redis"].jobs]
    expect("escalate" in job_names)


# ── escalate: alarm has no ack_token ─────────────────────────────────


async def test_escalate_alarm_without_ack_token(sessionmaker, seeded_db, settings):
    """escalate runs without raising when alarm.ack_token is None."""
    from escalane.worker.tasks import escalate

    alarm_id = uuid.uuid4()

    async with sessionmaker() as session:
        session.add(_make_alarm(alarm_id, ack_token=None))
        await session.commit()

    ctx = _make_ctx(sessionmaker, settings)

    # Should not raise: logs warning and continues with ack_url=None
    await escalate(ctx, str(alarm_id), step_no=1)


# ── alarm_acked: zammad disabled ──────────────────────────────────────


async def test_alarm_acked_zammad_disabled_returns_early(sessionmaker, seeded_db, settings):
    """alarm_acked logs debug and returns early when zammad is disabled."""
    alarm_id = uuid.uuid4()

    async with sessionmaker() as session:
        session.add(_make_alarm(alarm_id, zammad_ticket_id=42))
        await session.commit()

    disabled_zammad = MockZammadClient()
    disabled_zammad._enabled = False

    ctx = _make_ctx(sessionmaker, settings)
    ctx["zammad"] = disabled_zammad

    # Should not raise; logs debug and returns early
    await alarm_acked(ctx, str(alarm_id), acked_by="user", note=None)


# ── alarm_acked: ack_note_failed warning ──────────────────────────────


async def test_alarm_acked_ack_note_failed_logs_warning(sessionmaker, seeded_db, settings):
    """alarm_acked logs warning when add_zammad_ack_note returns False."""
    alarm_id = uuid.uuid4()

    async with sessionmaker() as session:
        session.add(_make_alarm(alarm_id, zammad_ticket_id=99, acked_at=datetime.now(UTC)))
        await session.commit()

    failing_zammad = MockZammadClient()
    failing_zammad.add_internal_note = AsyncMock(
        side_effect=httpx.ConnectError(
            "zammad down",
            request=httpx.Request("PUT", "https://zammad.example.test/api/v1/tickets/99"),
        )
    )

    ctx = _make_ctx(sessionmaker, settings)
    ctx["zammad"] = failing_zammad

    with pytest.raises(Retry) as retry:
        await alarm_acked(ctx, str(alarm_id), acked_by="user", note="test note")

    expect(retry.value.defer_score == 1_000)
