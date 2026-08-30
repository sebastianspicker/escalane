"""Shared factories for focused worker-task test modules."""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import httpx

from escalane.contracts.alarms import AlarmStatus
from escalane.persistence.models import Alarm, AlarmNotification
from escalane.providers.mock import MockSendXmsClient, MockSignalClient, MockZammadClient
from tests.support.api_test_helpers import app_client
from tests.support.constants import value_for_test
from tests.support.helpers import FakeRedis


def make_alarm(alarm_id: uuid.UUID | None = None, **overrides: object) -> Alarm:
    """Create a minimal Alarm instance for worker-task tests."""
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
        ack_token=value_for_test("worker-task-ack") + uuid.uuid4().hex[:8],
        created_at=datetime.now(UTC),
        meta={},
    )
    defaults.update(overrides)
    return Alarm(**defaults)


def make_ctx(sessionmaker: object, settings: object, http: httpx.AsyncClient | None = None) -> dict:
    """Build a worker context with deterministic fakes for all external connectors."""
    return {
        "sessionmaker": sessionmaker,
        "settings": settings,
        "http": http or httpx.AsyncClient(verify=False),
        "redis": FakeRedis(),
        "zammad": MockZammadClient(),
        "sendxms": MockSendXmsClient(),
        "signal": MockSignalClient(),
    }


async def persist_alarm(sessionmaker: object, alarm: Alarm) -> None:
    """Store an alarm through a test session factory."""
    async with sessionmaker() as session:
        session.add(alarm)
        await session.commit()


async def latest_notification(sessionmaker: object, alarm_id: uuid.UUID, channel: str):
    """Return the newest persisted notification for an alarm and channel."""
    from sqlalchemy import select

    async with sessionmaker() as session:
        return await session.scalar(
            select(AlarmNotification)
            .where(AlarmNotification.alarm_id == alarm_id)
            .where(AlarmNotification.channel == channel)
            .order_by(AlarmNotification.created_at.desc())
        )


async def load_alarm_notes(sessionmaker: object, alarm_id: uuid.UUID):
    """Load an alarm and its notes for lifecycle assertions."""
    from sqlalchemy import select

    from escalane.persistence.models import AlarmNote

    async with sessionmaker() as session:
        alarm = await session.get(Alarm, alarm_id)
        notes = list(
            (await session.scalars(select(AlarmNote).where(AlarmNote.alarm_id == alarm_id))).all()
        )
    return alarm, notes


@asynccontextmanager
async def open_app_client(*, settings: object, engine: object, redis: object):
    """Run an ASGI app lifespan and expose its in-process HTTP client."""
    async with app_client(settings=settings, engine=engine, redis=redis) as client:
        yield client


def enable_webhook(
    settings: object,
    *,
    url: str,
    secret: str,
    allowed_hosts: str,
    timeout_seconds: int = 5,
) -> None:
    """Configure a worker-test webhook endpoint with its explicit transport boundary."""
    settings.webhook_enabled = True
    settings.webhook_url = url
    settings.webhook_secret = secret
    settings.webhook_timeout_seconds = timeout_seconds
    settings.webhook_allowed_hosts = allowed_hosts


async def resolve_public_webhook(_url: str) -> tuple[str, ...]:
    """Resolve a test webhook to the externally routable address used by respx."""
    return ("1.1.1.1",)


def make_webhook_context(sessionmaker: object, settings: object, monkeypatch=None):
    """Create a worker HTTP context and optionally pin webhook resolution to the public test IP."""
    http = httpx.AsyncClient(verify=False)
    if monkeypatch is not None:
        monkeypatch.setattr(
            "escalane.notifications.workflows.validate_url_not_internal", resolve_public_webhook
        )
    return http, make_ctx(sessionmaker, settings, http)
