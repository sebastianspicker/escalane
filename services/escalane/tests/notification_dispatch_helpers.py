"""Shared factories for notification-dispatch tests."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from escalane.db.models import Alarm, AlarmStatus, EscalationTarget
from escalane.services.notification_service import NotificationService
from escalane.settings import Settings
from escalane.types import EnrichedAlarmContext, NotificationPayload

try:
    from tests.constants import EMPTY_SECRET_VALUE, TEST_ADMIN_API_KEY
except ModuleNotFoundError:
    from constants import EMPTY_SECRET_VALUE, TEST_ADMIN_API_KEY


_ALARM_ID = uuid.uuid4()
_NOW = datetime.now(UTC)


def _make_alarm() -> Alarm:
    """Return a stable triggered alarm double for notification payload tests."""
    alarm = MagicMock(spec=Alarm)
    alarm.id = _ALARM_ID
    alarm.status = AlarmStatus.TRIGGERED
    alarm.created_at = _NOW
    alarm.zammad_ticket_id = None
    return alarm


def _make_enriched(**kwargs: Any) -> EnrichedAlarmContext:
    """Create the smallest enriched context, allowing a test to override one field."""
    base: EnrichedAlarmContext = {
        "person_name": "Person X",
        "room_label": "Room 1",
        "site_name": "Site A",
        "severity": "P0",
    }
    base.update(kwargs)  # type: ignore[typeddict-item]
    return base


def _make_target(
    channel: str = "sms",
    address: str = "+491234",
    enabled: bool = True,
    target_id: str = "t1",
) -> EscalationTarget:
    """Return an escalation target double with channel-routing attributes."""
    t = MagicMock(spec=EscalationTarget)
    t.id = target_id
    t.channel = channel
    t.address = address
    t.enabled = enabled
    return t


def _make_svc(*, zammad_enabled: bool = True) -> NotificationService:
    """Build a notification service with observable connector doubles."""
    zammad = MagicMock()
    zammad.enabled.return_value = zammad_enabled
    zammad.create_ticket = AsyncMock(return_value=99)
    zammad.add_internal_note = AsyncMock()
    zammad.config = MagicMock(
        group="Support",
        state_id_new=1,
        customer="guess:test@example.org",
    )
    sendxms = MagicMock()
    sendxms.enabled.return_value = True
    sendxms.send_sms = AsyncMock()
    signal = MagicMock()
    signal.enabled.return_value = True
    signal.send_group_message = AsyncMock()
    return NotificationService(zammad=zammad, sendxms=sendxms, signal=signal)


async def _noop_session() -> AsyncMock:
    """Provide a session double whose reads are empty and writes are accepted."""
    session = AsyncMock()
    session.add = MagicMock()
    session.commit = AsyncMock()
    scalar_result = MagicMock()
    scalar_result.all.return_value = []
    session.scalars = AsyncMock(return_value=scalar_result)
    return session


async def _delivery_context(
    channel: str, *, zammad_enabled: bool = True, address: str | None = None
) -> tuple[NotificationService, AsyncMock, EscalationTarget, NotificationPayload]:
    """Build the common service, session, target, and payload for channel tests."""
    svc = _make_svc(zammad_enabled=zammad_enabled)
    session = await _noop_session()
    target_options = {"address": address} if address is not None else {}
    target = _make_target(channel=channel, **target_options)
    payload = svc._build_notification_payload(
        alarm=_make_alarm(), enriched=_make_enriched(), step_no=0, ack_url=None
    )
    return svc, session, target, payload


def _make_settings(**overrides: Any) -> Settings:
    """Return safe simulation settings, with explicit overrides per test."""
    payload: dict[str, Any] = {
        "admin_api_key": TEST_ADMIN_API_KEY,
        "simulation_enabled": True,
        "zammad_api_token": EMPTY_SECRET_VALUE,
        "sendxms_enabled": False,
        "signal_enabled": False,
    }
    payload.update(overrides)
    return Settings(**payload)
