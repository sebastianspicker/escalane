"""Worker-context and persistence helpers for worker-task tests."""

from __future__ import annotations

import uuid

import httpx

from escalane.persistence.models import Alarm, AlarmNotification
from escalane.providers.mock import MockSendXmsClient, MockSignalClient, MockZammadClient
from tests.support.fakes import FakeRedis


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
            "escalane.notifications.webhooks.validate_url_not_internal", resolve_public_webhook
        )
    return http, make_ctx(sessionmaker, settings, http)
