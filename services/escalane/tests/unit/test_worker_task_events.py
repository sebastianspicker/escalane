"""Tests for worker event dispatch and durable event recovery."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
import respx
from sqlalchemy import select

from escalane import constants
from escalane.db.models import AlarmEventOutbox
from escalane.worker.tasks import (
    alarm_acked,
    alarm_created,
    alarm_state_changed,
    escalate,
    process_alarm_event,
    recover_incomplete_alarm_events,
)

try:
    from tests.assertions import expect
    from tests.constants import TEST_WEBHOOK_SECRET
    from tests.worker_task_helpers import (
        enable_webhook,
        make_alarm,
        make_ctx,
        make_webhook_context,
        persist_alarm,
    )
except ModuleNotFoundError:
    from assertions import expect
    from constants import TEST_WEBHOOK_SECRET
    from worker_task_helpers import (
        enable_webhook,
        make_alarm,
        make_ctx,
        make_webhook_context,
        persist_alarm,
    )

pytestmark = pytest.mark.unit


async def test_deleted_alarm_worker_handlers_skip_external_work(
    sessionmaker, seeded_db, settings, monkeypatch
):
    """All worker entry points stop before enrichment, delivery, or webhooks after deletion."""
    alarm_id = uuid.uuid4()
    await persist_alarm(sessionmaker, make_alarm(alarm_id, deleted_at=datetime.now(UTC)))

    enrich = AsyncMock()
    operations_factory = MagicMock()
    monkeypatch.setattr("escalane.worker.tasks.enrich_alarm_context", enrich)
    monkeypatch.setattr(
        "escalane.worker.tasks._state_webhook_operations",
        operations_factory,
    )
    settings.webhook_enabled = True
    settings.webhook_url = "https://hooks.example.test/deleted"
    settings.webhook_allowed_hosts = "hooks.example.test"
    ctx = make_ctx(sessionmaker, settings)

    await alarm_created(ctx, str(alarm_id))
    await escalate(ctx, str(alarm_id), step_no=1)
    await alarm_acked(ctx, str(alarm_id), acked_by="operator")
    await alarm_state_changed(ctx, str(alarm_id), "triggered")

    expect(enrich.await_count == 0)
    expect(operations_factory.call_count == 0)


async def test_process_alarm_event_dispatches_state_changed(
    sessionmaker, seeded_db, settings, monkeypatch
):
    """process_alarm_event dispatches alarm.state_changed to alarm_state_changed."""
    alarm_id = uuid.uuid4()

    await persist_alarm(sessionmaker, make_alarm(alarm_id))

    enable_webhook(
        settings,
        url="https://hooks.example.test/event",
        secret=TEST_WEBHOOK_SECRET,
        allowed_hosts="hooks.example.test",
    )

    http, ctx = make_webhook_context(sessionmaker, settings, monkeypatch)

    with respx.mock(assert_all_called=True) as mock_router:
        route = mock_router.post("https://1.1.1.1/event").respond(200, json={"ok": True})

        await process_alarm_event(
            ctx,
            {
                "event_type": constants.EVENT_ALARM_STATE_CHANGED,
                "alarm_id": str(alarm_id),
                "new_state": "triggered",
            },
        )

        expect(route.called)

    await http.aclose()


async def test_process_alarm_event_unknown_type_does_not_crash(sessionmaker, seeded_db, settings):
    """Unknown event types are logged but do not raise."""
    await process_alarm_event(
        make_ctx(sessionmaker, settings),
        {
            "event_type": "alarm.unknown_event",
            "alarm_id": str(uuid.uuid4()),
        },
    )


async def test_process_alarm_event_missing_payload(sessionmaker, seeded_db, settings):
    """Invalid payloads (missing event_type) return early without raising."""
    await process_alarm_event(make_ctx(sessionmaker, settings), {})


async def test_recover_incomplete_alarm_events_enqueues_missing_jobs(
    sessionmaker, seeded_db, settings
):
    alarm_id = uuid.uuid4()

    async with sessionmaker() as session:
        session.add(make_alarm(alarm_id))
        session.add(
            AlarmEventOutbox(
                alarm_id=alarm_id,
                event_type=constants.EVENT_ALARM_CREATED,
                payload={},
                sequence=0,
            )
        )
        await session.commit()

    ctx = make_ctx(sessionmaker, settings)

    await recover_incomplete_alarm_events(ctx)

    expect([args[0]["event_type"] for _name, args in ctx["redis"].jobs] == ["alarm.created"])

    async with sessionmaker() as session:
        event = await session.scalar(
            select(AlarmEventOutbox).where(AlarmEventOutbox.alarm_id == alarm_id)
        )
        expect(event is not None)
        expect(event.published_at is not None)
