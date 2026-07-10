from __future__ import annotations

try:
    from tests.assertions import expect
except ModuleNotFoundError:
    from assertions import expect

import uuid
from datetime import UTC, datetime

import httpx
import respx
from sqlalchemy import select

from alarm_broker.db.models import Alarm, AlarmNotification, AlarmStatus
from alarm_broker.worker.tasks import alarm_state_changed

try:
    from tests.constants import TEST_WEBHOOK_SECRET, value_for_test
    from tests.helpers import FakeRedis
except ModuleNotFoundError:
    from constants import TEST_WEBHOOK_SECRET, value_for_test
    from helpers import FakeRedis


async def _add_webhook_alarm(sessionmaker, alarm_id: uuid.UUID) -> None:
    async with sessionmaker() as session:
        session.add(
            Alarm(
                id=alarm_id,
                status=AlarmStatus.TRIGGERED,
                source="test",
                event="alarm.trigger",
                person_id="ma-012",
                room_id="bg-1.23",
                site_id="bg",
                device_id="ylk-t5-10023",
                severity="P0",
                silent=True,
                ack_token=value_for_test("webhook"),
                created_at=datetime.now(UTC),
                meta={},
            )
        )
        await session.commit()


def _enable_webhook(settings, *, allowed_hosts: str = "hooks.example.test") -> None:
    settings.webhook_enabled = True
    settings.webhook_url = "https://hooks.example.test/alarm"
    settings.webhook_secret = TEST_WEBHOOK_SECRET
    settings.webhook_timeout_seconds = 5
    settings.webhook_allowed_hosts = allowed_hosts


def _worker_ctx(sessionmaker, settings, http):
    return {
        "sessionmaker": sessionmaker,
        "settings": settings,
        "http": http,
        "redis": FakeRedis(),
    }


async def _latest_webhook_notification(sessionmaker, alarm_id: uuid.UUID):
    async with sessionmaker() as session:
        return await session.scalar(
            select(AlarmNotification)
            .where(AlarmNotification.alarm_id == alarm_id)
            .where(AlarmNotification.channel == "webhook")
            .order_by(AlarmNotification.created_at.desc())
        )


async def test_alarm_state_changed_posts_webhook_and_logs_result(
    sessionmaker,
    seeded_db,
    settings,
    monkeypatch,
):
    alarm_id = uuid.uuid4()
    await _add_webhook_alarm(sessionmaker, alarm_id)
    _enable_webhook(settings)

    http = httpx.AsyncClient()
    ctx = _worker_ctx(sessionmaker, settings, http)

    async def allow_webhook(_url: str) -> None:
        return None

    monkeypatch.setattr("alarm_broker.worker.tasks.validate_url_not_internal", allow_webhook)

    with respx.mock(assert_all_called=True) as mock_router:
        route = mock_router.post("https://hooks.example.test/alarm").respond(200, json={"ok": True})
        await alarm_state_changed(ctx, str(alarm_id), "triggered")
        expect(route.called)

    row = await _latest_webhook_notification(sessionmaker, alarm_id)
    expect(row is not None)
    expect(row.result == "ok")
    expect(row.payload.get("state") == "triggered")
    await http.aclose()


async def test_alarm_state_changed_rejects_loopback_webhook_url(sessionmaker, seeded_db, settings):
    alarm_id = uuid.uuid4()
    now = datetime.now(UTC)

    async with sessionmaker() as session:
        session.add(
            Alarm(
                id=alarm_id,
                status=AlarmStatus.TRIGGERED,
                source="test",
                event="alarm.trigger",
                person_id="ma-012",
                room_id="bg-1.23",
                site_id="bg",
                device_id="ylk-t5-10023",
                severity="P0",
                silent=True,
                ack_token=value_for_test("webhook-loopback"),
                created_at=now,
                meta={},
            )
        )
        await session.commit()

    settings.webhook_enabled = True
    settings.webhook_url = "https://127.0.0.1/hooks"
    settings.webhook_secret = TEST_WEBHOOK_SECRET
    settings.webhook_timeout_seconds = 5
    settings.webhook_allowed_hosts = "127.0.0.1"

    http = httpx.AsyncClient()
    ctx = {
        "sessionmaker": sessionmaker,
        "settings": settings,
        "http": http,
        "redis": FakeRedis(),
    }

    await alarm_state_changed(ctx, str(alarm_id), "triggered")

    async with sessionmaker() as session:
        row = await session.scalar(
            select(AlarmNotification)
            .where(AlarmNotification.alarm_id == alarm_id)
            .where(AlarmNotification.channel == "webhook")
            .order_by(AlarmNotification.created_at.desc())
        )
        expect(row is not None)
        expect(row.result == "error")
        expect(row.error is not None)
        expect("blocked IP range" in row.error)

    await http.aclose()


async def test_alarm_state_changed_rejects_unallowlisted_host_without_egress(
    sessionmaker, seeded_db, settings
):
    alarm_id = uuid.uuid4()
    now = datetime.now(UTC)

    async with sessionmaker() as session:
        session.add(
            Alarm(
                id=alarm_id,
                status=AlarmStatus.TRIGGERED,
                source="test",
                event="alarm.trigger",
                person_id="ma-012",
                room_id="bg-1.23",
                site_id="bg",
                device_id="ylk-t5-10023",
                severity="P0",
                silent=True,
                ack_token=value_for_test("webhook-allowlist"),
                created_at=now,
                meta={},
            )
        )
        await session.commit()

    settings.webhook_enabled = True
    settings.webhook_url = "https://hooks.example.test/alarm"
    settings.webhook_secret = TEST_WEBHOOK_SECRET
    settings.webhook_timeout_seconds = 5
    settings.webhook_allowed_hosts = ""

    class NoEgressClient:
        async def post(self, *args, **kwargs):
            raise AssertionError("network egress must not happen")

    ctx = {
        "sessionmaker": sessionmaker,
        "settings": settings,
        "http": NoEgressClient(),
        "redis": FakeRedis(),
    }

    await alarm_state_changed(ctx, str(alarm_id), "triggered")

    async with sessionmaker() as session:
        row = await session.scalar(
            select(AlarmNotification)
            .where(AlarmNotification.alarm_id == alarm_id)
            .where(AlarmNotification.channel == "webhook")
            .order_by(AlarmNotification.created_at.desc())
        )
        expect(row is not None)
        expect(row.result == "error")
        expect(row.error is not None)
        expect("WEBHOOK_ALLOWED_HOSTS is empty" in row.error)
