"""Tests for notification_service.py — channel dispatch paths, disabled/error branches."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from alarm_broker.db.models import Alarm, AlarmStatus, EscalationTarget
from alarm_broker.services.notification_service import NotificationService, log_notification
from alarm_broker.types import EnrichedAlarmContext

pytestmark = [pytest.mark.unit]


# ── helpers ────────────────────────────────────────────────────────────

_ALARM_ID = uuid.uuid4()
_NOW = datetime.now(UTC)


def _make_alarm() -> Alarm:
    alarm = MagicMock(spec=Alarm)
    alarm.id = _ALARM_ID
    alarm.status = AlarmStatus.TRIGGERED
    alarm.created_at = _NOW
    alarm.zammad_ticket_id = None
    return alarm


def _make_enriched(**kwargs: Any) -> EnrichedAlarmContext:
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
    t = MagicMock(spec=EscalationTarget)
    t.id = target_id
    t.channel = channel
    t.address = address
    t.enabled = enabled
    return t


def _make_svc(*, zammad_enabled: bool = True) -> NotificationService:
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
    sendxms.send_sms = AsyncMock()
    signal = MagicMock()
    signal.send_group_message = AsyncMock()
    return NotificationService(zammad=zammad, sendxms=sendxms, signal=signal)


async def _noop_session() -> AsyncMock:
    session = AsyncMock()
    session.add = MagicMock()
    session.commit = AsyncMock()
    return session


# ── _build_notification_payload / helpers ──────────────────────────────


def test_build_title_step_zero():
    svc = _make_svc()
    enriched = _make_enriched(person_name="Alice", room_label="R1")
    title = svc._build_title(enriched, step_no=0)
    assert "NOTFALLALARM" in title
    assert "Alice" in title


def test_build_title_escalation_step():
    svc = _make_svc()
    enriched = _make_enriched(person_name="Bob", room_label="R2")
    title = svc._build_title(enriched, step_no=2)
    assert "ESKALATION" in title
    assert "2" in title


def test_build_tags_step_zero_critical():
    svc = _make_svc()
    tags = svc._build_tags(step_no=0, severity="P0")
    assert len(tags) == 2  # emergency + silent


def test_build_tags_step_one_no_emergency():
    svc = _make_svc()
    tags = svc._build_tags(step_no=1, severity="P1")
    assert len(tags) == 0


def test_get_priority_for_known_severity():
    svc = _make_svc()
    assert svc._get_priority_for_severity("P0") == 3
    assert svc._get_priority_for_severity("P1") == 2
    assert svc._get_priority_for_severity("P3") == 1


def test_get_priority_unknown_defaults_to_critical():
    svc = _make_svc()
    assert svc._get_priority_for_severity("unknown") == 3


# ── _send_to_channel: disabled target ─────────────────────────────────


async def test_send_skips_disabled_target():
    svc = _make_svc()
    session = await _noop_session()
    target = _make_target(enabled=False)

    # _send_to_channel should never be called for disabled targets
    # We exercise the send() method with one disabled target and check no dispatch
    with patch.object(svc, "_send_to_channel", new_callable=AsyncMock) as mock_dispatch:
        with patch.object(
            svc,
            "_get_escalation_targets",
            new_callable=AsyncMock,
            return_value=[target],
        ):
            await svc.send(
                session,
                alarm=_make_alarm(),
                enriched=_make_enriched(),
                step_no=0,
                ack_url="http://x/a/tok",
            )

    mock_dispatch.assert_not_called()


# ── _send_email_notifications ──────────────────────────────────────────


async def test_send_email_zammad_disabled_logs_error():
    svc = _make_svc(zammad_enabled=False)
    session = await _noop_session()
    target = _make_target(channel="email")
    payload = svc._build_notification_payload(
        alarm=_make_alarm(), enriched=_make_enriched(), step_no=0, ack_url=None
    )

    await svc._send_email_notifications(session, target, payload)

    # Should have committed (logged error) but not called create_ticket
    svc._zammad.create_ticket.assert_not_called()


async def test_send_email_zammad_create_ticket_exception_logs_error():
    svc = _make_svc(zammad_enabled=True)
    svc._zammad.create_ticket = AsyncMock(side_effect=RuntimeError("zammad down"))
    session = await _noop_session()
    target = _make_target(channel="email")
    payload = svc._build_notification_payload(
        alarm=_make_alarm(), enriched=_make_enriched(), step_no=0, ack_url=None
    )

    # Should not raise — best-effort
    await svc._send_email_notifications(session, target, payload)

    session.commit.assert_called()


# ── _send_via_signal ───────────────────────────────────────────────────


async def test_send_via_signal_exception_does_not_raise():
    svc = _make_svc()
    svc._signal.send_group_message = AsyncMock(side_effect=OSError("signal down"))
    session = await _noop_session()
    target = _make_target(channel="signal", address="group-id")
    payload = svc._build_notification_payload(
        alarm=_make_alarm(), enriched=_make_enriched(), step_no=0, ack_url=None
    )

    await svc._send_via_signal(session, target, payload["body"], payload)

    session.commit.assert_called()


async def test_send_via_signal_success():
    svc = _make_svc()
    session = await _noop_session()
    target = _make_target(channel="signal", address="group-id")
    payload = svc._build_notification_payload(
        alarm=_make_alarm(), enriched=_make_enriched(), step_no=0, ack_url=None
    )

    await svc._send_via_signal(session, target, payload["body"], payload)

    svc._signal.send_group_message.assert_called_once()


# ── _send_via_sendxms ─────────────────────────────────────────────────


async def test_send_via_sendxms_exception_does_not_raise():
    svc = _make_svc()
    svc._sendxms.send_sms = AsyncMock(side_effect=OSError("sms down"))
    session = await _noop_session()
    target = _make_target(channel="sms", address="+491234")
    payload = svc._build_notification_payload(
        alarm=_make_alarm(), enriched=_make_enriched(), step_no=0, ack_url=None
    )

    await svc._send_via_sendxms(session, target, payload["body"], payload)

    session.commit.assert_called()


# ── _send_webhook_notifications ───────────────────────────────────────


async def test_send_webhook_no_url_logs_error():
    svc = _make_svc()
    session = await _noop_session()
    target = _make_target(channel="webhook", address="")
    payload = svc._build_notification_payload(
        alarm=_make_alarm(), enriched=_make_enriched(), step_no=0, ack_url=None
    )

    await svc._send_webhook_notifications(session, target, payload)

    session.commit.assert_called()


async def test_send_webhook_ssrf_blocked():
    svc = _make_svc()
    session = await _noop_session()
    target = _make_target(channel="webhook", address="http://169.254.169.254/metadata")
    payload = svc._build_notification_payload(
        alarm=_make_alarm(), enriched=_make_enriched(), step_no=0, ack_url=None
    )

    from alarm_broker.core.url_validation import SSRFError

    with patch(
        "alarm_broker.services.notification_service.validate_url_not_internal",
        new_callable=AsyncMock,
        side_effect=SSRFError("SSRF blocked"),
    ):
        await svc._send_webhook_notifications(session, target, payload)

    session.commit.assert_called()


async def test_send_webhook_http_error():
    svc = _make_svc()
    session = await _noop_session()
    target = _make_target(channel="webhook", address="http://valid-external.example.com/hook")
    payload = svc._build_notification_payload(
        alarm=_make_alarm(), enriched=_make_enriched(), step_no=0, ack_url=None
    )

    with patch(
        "alarm_broker.services.notification_service.validate_url_not_internal",
        new_callable=AsyncMock,
    ):
        with patch(
            "alarm_broker.services.notification_service.httpx.AsyncClient",
            side_effect=OSError("network error"),
        ):
            await svc._send_webhook_notifications(session, target, payload)

    session.commit.assert_called()


async def test_send_webhook_unknown_channel_logs_warning():
    svc = _make_svc()
    session = await _noop_session()
    target = _make_target(channel="unknown_channel", address="x")
    payload = svc._build_notification_payload(
        alarm=_make_alarm(), enriched=_make_enriched(), step_no=0, ack_url=None
    )

    # Should not raise
    await svc._send_to_channel(session, target, payload)


# ── _send_to_channel: channel routing ─────────────────────────────────


async def test_send_to_channel_dispatches_email():
    """_send_to_channel routes email channel to _send_email_notifications."""
    svc = _make_svc()
    session = await _noop_session()
    target = _make_target(channel="email")
    payload = svc._build_notification_payload(
        alarm=_make_alarm(), enriched=_make_enriched(), step_no=0, ack_url=None
    )

    with patch.object(svc, "_send_email_notifications", new_callable=AsyncMock) as mock_email:
        await svc._send_to_channel(session, target, payload)

    mock_email.assert_called_once_with(session, target, payload)


async def test_send_to_channel_dispatches_webhook():
    """_send_to_channel routes webhook channel to _send_webhook_notifications."""
    svc = _make_svc()
    session = await _noop_session()
    target = _make_target(channel="webhook", address="http://example.com/hook")
    payload = svc._build_notification_payload(
        alarm=_make_alarm(), enriched=_make_enriched(), step_no=0, ack_url=None
    )

    with patch.object(svc, "_send_webhook_notifications", new_callable=AsyncMock) as mock_hook:
        await svc._send_to_channel(session, target, payload)

    mock_hook.assert_called_once_with(session, target, payload)


async def test_send_to_channel_swallows_unexpected_exception():
    """An unexpected exception inside _send_to_channel is caught and logged."""
    svc = _make_svc()
    session = await _noop_session()
    target = _make_target(channel="sms")
    payload = svc._build_notification_payload(
        alarm=_make_alarm(), enriched=_make_enriched(), step_no=0, ack_url=None
    )

    with patch.object(
        svc, "_send_sms_notifications", new_callable=AsyncMock, side_effect=RuntimeError("boom")
    ):
        # Must not raise — outer except catches it
        await svc._send_to_channel(session, target, payload)


# ── _send_email_notifications: success path ───────────────────────────


async def test_send_email_zammad_success_logs_ok():
    """When create_ticket succeeds, _log_notification_result is called with 'ok'."""
    svc = _make_svc(zammad_enabled=True)
    svc._zammad.create_ticket = AsyncMock(return_value=55)
    session = await _noop_session()
    target = _make_target(channel="email")
    payload = svc._build_notification_payload(
        alarm=_make_alarm(), enriched=_make_enriched(), step_no=0, ack_url=None
    )

    with patch.object(svc, "_log_notification_result", new_callable=AsyncMock) as mock_log:
        await svc._send_email_notifications(session, target, payload)

    mock_log.assert_called_once_with(session, target, payload, "ok")


# ── _send_webhook_notifications: success path ─────────────────────────


async def test_send_webhook_success_logs_ok():
    """When the HTTP POST succeeds, _log_notification_result is called with 'ok'."""
    import httpx

    svc = _make_svc()
    session = await _noop_session()
    target = _make_target(channel="webhook", address="http://valid-external.example.com/hook")
    payload = svc._build_notification_payload(
        alarm=_make_alarm(), enriched=_make_enriched(), step_no=0, ack_url=None
    )

    mock_response = MagicMock(spec=httpx.Response)
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=mock_response)

    with patch(
        "alarm_broker.services.notification_service.validate_url_not_internal",
        new_callable=AsyncMock,
    ):
        with patch(
            "alarm_broker.services.notification_service.httpx.AsyncClient",
            return_value=mock_client,
        ):
            with patch.object(svc, "_log_notification_result", new_callable=AsyncMock) as mock_log:
                await svc._send_webhook_notifications(session, target, payload)

    mock_log.assert_called_once_with(session, target, payload, "ok")


# ── handle_zammad_ticket ───────────────────────────────────────────────


async def test_handle_zammad_ticket_disabled_returns_none():
    svc = _make_svc(zammad_enabled=False)
    session = await _noop_session()

    result = await svc.handle_zammad_ticket(
        session,
        alarm=_make_alarm(),
        enriched=_make_enriched(),
        ack_url=None,
        settings=None,
    )

    assert result is None


async def test_handle_zammad_ticket_create_exception_returns_none():
    svc = _make_svc(zammad_enabled=True)
    svc._zammad.create_ticket = AsyncMock(side_effect=RuntimeError("503"))
    session = await _noop_session()

    result = await svc.handle_zammad_ticket(
        session,
        alarm=_make_alarm(),
        enriched=_make_enriched(),
        ack_url=None,
        settings=None,
    )

    assert result is None


async def test_handle_zammad_ticket_success_returns_ticket_id():
    svc = _make_svc(zammad_enabled=True)
    svc._zammad.create_ticket = AsyncMock(return_value=42)
    session = await _noop_session()

    result = await svc.handle_zammad_ticket(
        session,
        alarm=_make_alarm(),
        enriched=_make_enriched(),
        ack_url="http://x/a/tok",
        settings=None,
    )

    assert result == 42


# ── add_zammad_ack_note ────────────────────────────────────────────────


async def test_add_zammad_ack_note_disabled_returns_false():
    svc = _make_svc(zammad_enabled=False)
    session = await _noop_session()

    result = await svc.add_zammad_ack_note(
        session,
        alarm_id=_ALARM_ID,
        ticket_id=10,
        acked_by="user",
        acked_at=_NOW,
        note=None,
    )

    assert result is False


async def test_add_zammad_ack_note_exception_returns_false():
    svc = _make_svc(zammad_enabled=True)
    svc._zammad.add_internal_note = AsyncMock(side_effect=RuntimeError("zammad error"))
    session = await _noop_session()

    result = await svc.add_zammad_ack_note(
        session,
        alarm_id=_ALARM_ID,
        ticket_id=10,
        acked_by="user",
        acked_at=_NOW,
        note="note text",
    )

    assert result is False


# ── log_notification (module-level) ───────────────────────────────────


async def test_log_notification_module_fn():
    session = await _noop_session()

    await log_notification(
        session,
        alarm_id=_ALARM_ID,
        channel="sms",
        target_id="t1",
        payload={"msg": "test"},
        result="ok",
    )

    session.add.assert_called_once()
    session.commit.assert_called_once()
