"""Worker-driven state-webhook delivery and outbound-URL boundary tests."""

from __future__ import annotations

try:
    from tests.assertions import expect
except ModuleNotFoundError:
    from assertions import expect

import uuid

import pytest
import respx

from escalane.worker.tasks import alarm_state_changed

try:
    from tests.constants import TEST_WEBHOOK_SECRET
    from tests.worker_task_helpers import (
        enable_webhook,
        latest_notification,
        make_alarm,
        make_ctx,
        make_webhook_context,
        persist_alarm,
    )
except ModuleNotFoundError:
    from constants import TEST_WEBHOOK_SECRET
    from worker_task_helpers import (
        enable_webhook,
        latest_notification,
        make_alarm,
        make_ctx,
        make_webhook_context,
        persist_alarm,
    )

pytestmark = pytest.mark.unit


async def test_alarm_state_changed_posts_webhook_and_logs_result(
    sessionmaker,
    seeded_db,
    settings,
    monkeypatch,
):
    alarm_id = uuid.uuid4()
    await persist_alarm(sessionmaker, make_alarm(alarm_id))
    enable_webhook(
        settings,
        url="https://hooks.example.test/alarm",
        secret=TEST_WEBHOOK_SECRET,
        allowed_hosts="hooks.example.test",
    )

    http, ctx = make_webhook_context(sessionmaker, settings, monkeypatch)

    with respx.mock(assert_all_called=True) as mock_router:
        route = mock_router.post("https://1.1.1.1/alarm").respond(200, json={"ok": True})
        await alarm_state_changed(ctx, str(alarm_id), "triggered")
        expect(route.called)

    row = await latest_notification(sessionmaker, alarm_id, "webhook")
    expect(row is not None)
    expect(row.result == "ok")
    expect(row.payload.get("state") == "triggered")
    await http.aclose()


def _expect_skipped_webhook(row, error_fragment: str) -> None:
    """Assert the persisted failure contract for a safely skipped webhook delivery."""
    expect(row is not None)
    expect(row.result == "skipped")
    expect(row.error is not None)
    expect(error_fragment in row.error)


async def test_alarm_state_changed_rejects_loopback_webhook_url(sessionmaker, seeded_db, settings):
    alarm_id = uuid.uuid4()
    await persist_alarm(sessionmaker, make_alarm(alarm_id))

    enable_webhook(
        settings,
        url="https://127.0.0.1/hooks",
        secret=TEST_WEBHOOK_SECRET,
        allowed_hosts="127.0.0.1",
    )

    http, ctx = make_webhook_context(sessionmaker, settings)

    await alarm_state_changed(ctx, str(alarm_id), "triggered")

    row = await latest_notification(sessionmaker, alarm_id, "webhook")
    _expect_skipped_webhook(row, "blocked IP range")

    await http.aclose()


async def test_alarm_state_changed_rejects_unallowlisted_host_without_egress(
    sessionmaker, seeded_db, settings
):
    alarm_id = uuid.uuid4()
    await persist_alarm(sessionmaker, make_alarm(alarm_id))

    enable_webhook(
        settings,
        url="https://hooks.example.test/alarm",
        secret=TEST_WEBHOOK_SECRET,
        allowed_hosts="",
    )

    class NoEgressClient:
        async def post(self, *args, **kwargs):
            raise AssertionError("network egress must not happen")

    ctx = make_ctx(sessionmaker, settings, NoEgressClient())

    await alarm_state_changed(ctx, str(alarm_id), "triggered")

    row = await latest_notification(sessionmaker, alarm_id, "webhook")
    _expect_skipped_webhook(row, "WEBHOOK_ALLOWED_HOSTS is empty")
