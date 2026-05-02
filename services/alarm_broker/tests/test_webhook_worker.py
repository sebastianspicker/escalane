from __future__ import annotations

import uuid
from datetime import UTC, datetime

import httpx
import respx
from sqlalchemy import select

from alarm_broker.db.models import Alarm, AlarmNotification, AlarmStatus
from alarm_broker.worker.tasks import alarm_state_changed

try:
    from tests.helpers import FakeRedis
except ModuleNotFoundError:
    from helpers import FakeRedis


async def test_alarm_state_changed_posts_webhook_and_logs_result(
    sessionmaker,
    seeded_db,
    settings,
    monkeypatch,
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
                ack_token="webhook-test",
                created_at=now,
                meta={},
            )
        )
        await session.commit()

    settings.webhook_enabled = True
    settings.webhook_url = "https://hooks.example.test/alarm"
    settings.webhook_secret = "test-secret"
    settings.webhook_timeout_seconds = 5
    settings.webhook_allowed_hosts = "hooks.example.test"

    http = httpx.AsyncClient()
    ctx = {
        "sessionmaker": sessionmaker,
        "settings": settings,
        "http": http,
        "redis": FakeRedis(),
    }

    async def allow_webhook(_url: str) -> None:
        return None

    monkeypatch.setattr("alarm_broker.worker.tasks.validate_url_not_internal", allow_webhook)

    with respx.mock(assert_all_called=True) as mock_router:
        route = mock_router.post("https://hooks.example.test/alarm").respond(200, json={"ok": True})
        await alarm_state_changed(ctx, str(alarm_id), "triggered")
        assert route.called

    async with sessionmaker() as session:
        row = await session.scalar(
            select(AlarmNotification)
            .where(AlarmNotification.alarm_id == alarm_id)
            .where(AlarmNotification.channel == "webhook")
            .order_by(AlarmNotification.created_at.desc())
        )
        assert row is not None
        assert row.result == "ok"
        assert row.payload.get("state") == "triggered"

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
                ack_token="webhook-loopback-test",
                created_at=now,
                meta={},
            )
        )
        await session.commit()

    settings.webhook_enabled = True
    settings.webhook_url = "https://127.0.0.1/hooks"
    settings.webhook_secret = "test-secret"
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
        assert row is not None
        assert row.result == "error"
        assert row.error is not None
        assert "blocked IP range" in row.error

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
                ack_token="webhook-allowlist-test",
                created_at=now,
                meta={},
            )
        )
        await session.commit()

    settings.webhook_enabled = True
    settings.webhook_url = "https://hooks.example.test/alarm"
    settings.webhook_secret = "test-secret"
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
        assert row is not None
        assert row.result == "error"
        assert row.error is not None
        assert "WEBHOOK_ALLOWED_HOSTS is empty" in row.error
