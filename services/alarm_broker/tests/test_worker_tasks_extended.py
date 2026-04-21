"""Additional worker/tasks.py tests targeting uncovered branches."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import httpx

from alarm_broker import constants
from alarm_broker.connectors.mock import MockSendXmsClient, MockSignalClient, MockZammadClient
from alarm_broker.core.url_validation import SSRFError
from alarm_broker.db.models import Alarm, AlarmStatus
from alarm_broker.worker.tasks import (
    alarm_acked,
    alarm_created,
    alarm_state_changed,
    process_alarm_event,
)

try:
    from tests.helpers import FakeRedis
except ModuleNotFoundError:
    from helpers import FakeRedis


def _make_alarm(alarm_id: uuid.UUID | None = None, **overrides) -> Alarm:
    defaults = dict(
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
        ack_token="tok-" + uuid.uuid4().hex[:8],
        created_at=datetime.now(UTC),
        meta={},
    )
    defaults.update(overrides)
    return Alarm(**defaults)


def _make_ctx(sessionmaker, settings, http=None) -> dict:
    return {
        "sessionmaker": sessionmaker,
        "settings": settings,
        "http": http or httpx.AsyncClient(),
        "redis": FakeRedis(),
        "zammad": MockZammadClient(),
        "sendxms": MockSendXmsClient(),
        "signal": MockSignalClient(),
    }


# ── process_alarm_event: resolved and cancelled dispatch ─────────────


async def test_process_alarm_event_resolved_logs_and_returns(sessionmaker, seeded_db, settings):
    """EVENT_ALARM_RESOLVED is handled without raising."""
    ctx = _make_ctx(sessionmaker, settings)

    # Should not raise
    await process_alarm_event(
        ctx,
        {
            "event_type": constants.EVENT_ALARM_RESOLVED,
            "alarm_id": str(uuid.uuid4()),
        },
    )


async def test_process_alarm_event_cancelled_logs_and_returns(sessionmaker, seeded_db, settings):
    """EVENT_ALARM_CANCELLED is handled without raising."""
    ctx = _make_ctx(sessionmaker, settings)

    await process_alarm_event(
        ctx,
        {
            "event_type": constants.EVENT_ALARM_CANCELLED,
            "alarm_id": str(uuid.uuid4()),
        },
    )


async def test_process_alarm_event_created_dispatches(sessionmaker, seeded_db, settings):
    """EVENT_ALARM_CREATED dispatches to alarm_created."""
    alarm_id = uuid.uuid4()

    async with sessionmaker() as session:
        session.add(_make_alarm(alarm_id))
        await session.commit()

    ctx = _make_ctx(sessionmaker, settings)

    # alarm_created calls notification.send — with mock connectors it's a no-op
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
        session.add(_make_alarm(alarm_id, zammad_ticket_id=None))
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
    """alarm_created works when ack_token is None — sends with ack_url=None."""
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
    from alarm_broker.worker.tasks import escalate

    ctx = _make_ctx(sessionmaker, settings)

    await escalate(ctx, str(uuid.uuid4()), step_no=1)


# ── alarm_acked: no zammad_ticket_id ─────────────────────────────────


async def test_alarm_acked_no_ticket_id_logs_and_returns(sessionmaker, seeded_db, settings):
    """alarm_acked logs warning and returns if zammad_ticket_id is None."""
    alarm_id = uuid.uuid4()

    async with sessionmaker() as session:
        session.add(_make_alarm(alarm_id, zammad_ticket_id=None))
        await session.commit()

    ctx = _make_ctx(sessionmaker, settings)

    # Should not raise
    await alarm_acked(ctx, str(alarm_id), acked_by="user", note=None)


# ── alarm_state_changed: SSRF-blocked webhook URL ────────────────────


async def test_alarm_state_changed_ssrf_blocked_logs_error(sessionmaker, seeded_db, settings):
    """alarm_state_changed logs error and returns if webhook URL is SSRF-blocked."""
    alarm_id = uuid.uuid4()

    async with sessionmaker() as session:
        session.add(_make_alarm(alarm_id))
        await session.commit()

    settings.webhook_enabled = True
    settings.webhook_url = "http://169.254.169.254/metadata"
    settings.webhook_timeout_seconds = 5

    ctx = _make_ctx(sessionmaker, settings)

    with patch(
        "alarm_broker.worker.tasks.validate_url_not_internal",
        new_callable=AsyncMock,
        side_effect=SSRFError("SSRF blocked"),
    ):
        await alarm_state_changed(ctx, str(alarm_id), "triggered")


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

    # No mock router — would fail if an HTTP call was made
    await alarm_state_changed(ctx, str(alarm_id), "triggered")


# ── alarm_state_changed: alarm not found ──────────────────────────────


async def test_alarm_state_changed_alarm_not_found_returns_early(sessionmaker, seeded_db, settings):
    """alarm_state_changed returns early if alarm is not in DB."""
    settings.webhook_enabled = True
    settings.webhook_url = "https://hooks.example.test/nf"
    settings.webhook_timeout_seconds = 5

    ctx = _make_ctx(sessionmaker, settings)

    async def allow_webhook(_url: str) -> None:
        return None

    with patch("alarm_broker.worker.tasks.validate_url_not_internal", allow_webhook):
        await alarm_state_changed(ctx, str(uuid.uuid4()), "triggered")


# ── recover_incomplete_alarm_events: empty DB ─────────────────────────


async def test_recover_incomplete_alarm_events_empty_db(sessionmaker, seeded_db, settings):
    """recover_incomplete_alarm_events returns cleanly when no alarms exist."""
    from alarm_broker.worker.tasks import recover_incomplete_alarm_events

    ctx = _make_ctx(sessionmaker, settings)

    # Should not raise; hits the `if not alarms: break` branch (line 372)
    await recover_incomplete_alarm_events(ctx)


# ── recover_incomplete_alarm_events: complete alarm is skipped ─────────


async def test_recover_incomplete_alarm_events_skips_complete_alarm(
    sessionmaker, seeded_db, settings
):
    """Alarms without event_delivery meta are skipped (lines 106, 376)."""
    from alarm_broker.worker.tasks import recover_incomplete_alarm_events

    alarm_id = uuid.uuid4()

    async with sessionmaker() as session:
        # meta={} → _has_incomplete_event_delivery returns False → continue
        session.add(_make_alarm(alarm_id, meta={}))
        await session.commit()

    ctx = _make_ctx(sessionmaker, settings)

    # Should not raise; no recovery jobs should be enqueued
    await recover_incomplete_alarm_events(ctx)
    assert ctx["redis"].jobs == []


# ── alarm_created: escalation schedule schedules future steps ─────────


async def test_alarm_created_schedules_escalation_steps(sessionmaker, seeded_db, settings):
    """alarm_created enqueues escalation jobs when the schedule has future steps (lines 145-152)."""
    from alarm_broker.db.models import EscalationPolicy, EscalationStep, EscalationTarget

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
    assert "escalate" in job_names


# ── escalate: alarm has no ack_token ─────────────────────────────────


async def test_escalate_alarm_without_ack_token(sessionmaker, seeded_db, settings):
    """escalate runs without raising when alarm.ack_token is None (lines 198-202)."""
    from alarm_broker.worker.tasks import escalate

    alarm_id = uuid.uuid4()

    async with sessionmaker() as session:
        session.add(_make_alarm(alarm_id, ack_token=None))
        await session.commit()

    ctx = _make_ctx(sessionmaker, settings)

    # Should not raise — logs warning and continues with ack_url=None
    await escalate(ctx, str(alarm_id), step_no=1)


# ── alarm_acked: zammad disabled (lines 252-253) ──────────────────────


async def test_alarm_acked_zammad_disabled_returns_early(sessionmaker, seeded_db, settings):
    """alarm_acked logs debug and returns early when zammad is disabled (lines 252-253)."""
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


# ── alarm_acked: ack_note_failed warning (line 273) ───────────────────


async def test_alarm_acked_ack_note_failed_logs_warning(sessionmaker, seeded_db, settings):
    """alarm_acked logs warning when add_zammad_ack_note returns False (line 273)."""
    alarm_id = uuid.uuid4()

    async with sessionmaker() as session:
        session.add(_make_alarm(alarm_id, zammad_ticket_id=99, acked_at=datetime.now(UTC)))
        await session.commit()

    failing_zammad = MockZammadClient()
    failing_zammad.add_internal_note = AsyncMock(side_effect=RuntimeError("zammad down"))

    ctx = _make_ctx(sessionmaker, settings)
    ctx["zammad"] = failing_zammad

    # Should not raise — logs warning on failure
    await alarm_acked(ctx, str(alarm_id), acked_by="user", note="test note")
