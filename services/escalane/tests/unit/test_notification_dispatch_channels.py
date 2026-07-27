"""Channel dispatch and payload tests for NotificationService."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from escalane.services.notification_delivery import NotificationDeliveryError

try:
    from tests.assertions import expect
    from tests.notification_dispatch_helpers import (
        _delivery_context,
        _make_alarm,
        _make_enriched,
        _make_settings,
        _make_svc,
        _make_target,
        _noop_session,
    )
except ModuleNotFoundError:
    from assertions import expect
    from notification_dispatch_helpers import (
        _delivery_context,
        _make_alarm,
        _make_enriched,
        _make_settings,
        _make_svc,
        _make_target,
        _noop_session,
    )

pytestmark = [pytest.mark.unit]


# ── _build_notification_payload / helpers ──────────────────────────────


def test_build_title_step_zero():
    svc = _make_svc()
    enriched = _make_enriched(person_name="Alice", room_label="R1")
    title = svc._build_title(enriched, step_no=0)
    expect("NOTFALLALARM" in title)
    expect("Alice" in title)


def test_build_title_escalation_step():
    svc = _make_svc()
    enriched = _make_enriched(person_name="Bob", room_label="R2")
    title = svc._build_title(enriched, step_no=2)
    expect("ESKALATION" in title)
    expect("2" in title)


def test_build_tags_step_zero_critical():
    svc = _make_svc()
    tags = svc._build_tags(step_no=0, severity="P0")
    expect(len(tags) == 2)  # emergency + silent


def test_build_tags_step_one_no_emergency():
    svc = _make_svc()
    tags = svc._build_tags(step_no=1, severity="P1")
    expect(len(tags) == 0)


def test_get_priority_for_known_severity():
    svc = _make_svc()
    expect(svc._get_priority_for_severity("P0") == 3)
    expect(svc._get_priority_for_severity("P1") == 2)
    expect(svc._get_priority_for_severity("P3") == 1)


def test_get_priority_unknown_defaults_to_critical():
    svc = _make_svc()
    expect(svc._get_priority_for_severity("unknown") == 3)


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


async def test_send_attempts_every_target_then_raises_for_retryable_failures():
    svc = _make_svc()
    session = await _noop_session()
    successful = _make_target(channel="signal", target_id="successful")
    failed = _make_target(channel="sms", target_id="failed")

    with patch.object(
        svc,
        "_get_escalation_targets",
        new_callable=AsyncMock,
        return_value=[successful, failed],
    ):
        with patch.object(
            svc,
            "_send_to_channel",
            new_callable=AsyncMock,
            side_effect=[True, False],
        ) as dispatch:
            with pytest.raises(NotificationDeliveryError, match="failed"):
                await svc.send(
                    session,
                    alarm=_make_alarm(),
                    enriched=_make_enriched(),
                    step_no=0,
                    ack_url=None,
                )

    expect(dispatch.await_count == 2)


async def test_send_retry_skips_a_target_with_durable_success():
    svc = _make_svc()
    session = await _noop_session()
    already_delivered = _make_target(channel="signal", target_id="already-delivered")
    still_pending = _make_target(channel="sms", target_id="still-pending")
    successful_row = MagicMock()
    successful_row.payload = {"step_no": 0}
    successful_result = MagicMock()
    successful_result.all.return_value = [successful_row]
    pending_result = MagicMock()
    pending_result.all.return_value = []
    session.scalars = AsyncMock(side_effect=[successful_result, pending_result])

    with patch.object(
        svc,
        "_get_escalation_targets",
        new_callable=AsyncMock,
        return_value=[already_delivered, still_pending],
    ):
        with patch.object(
            svc, "_send_to_channel", new_callable=AsyncMock, return_value=True
        ) as dispatch:
            await svc.send(
                session,
                alarm=_make_alarm(),
                enriched=_make_enriched(),
                step_no=0,
                ack_url=None,
            )

    dispatch.assert_awaited_once()
    expect(dispatch.await_args.args[1] is still_pending)


# ── _send_email_notifications ──────────────────────────────────────────


async def test_send_email_zammad_disabled_logs_skipped():
    svc, session, target, payload = await _delivery_context("email", zammad_enabled=False)

    with patch.object(svc, "_log_notification_result", new_callable=AsyncMock) as mock_log:
        await svc._send_email_notifications(session, target, payload)

    svc._zammad.create_ticket.assert_not_called()
    mock_log.assert_called_once_with(session, target, payload, "skipped", "Zammad not enabled")


async def test_send_email_zammad_create_ticket_exception_logs_error():
    svc, session, target, payload = await _delivery_context("email")
    svc._zammad.create_ticket = AsyncMock(side_effect=RuntimeError("zammad down"))

    # Should not raise: best-effort
    await svc._send_email_notifications(session, target, payload)

    session.commit.assert_called()


# ── _send_via_signal ───────────────────────────────────────────────────


async def test_send_via_signal_exception_does_not_raise():
    svc, session, target, payload = await _delivery_context("signal", address="group-id")
    svc._signal.send_group_message = AsyncMock(side_effect=OSError("signal down"))

    await svc._send_via_signal(session, target, payload["body"], payload)

    session.commit.assert_called()


async def test_send_via_signal_success():
    svc, session, target, payload = await _delivery_context("signal", address="group-id")

    await svc._send_via_signal(session, target, payload["body"], payload)

    svc._signal.send_group_message.assert_called_once()


async def test_send_via_signal_disabled_logs_skipped() -> None:
    svc, session = _make_svc(), await _noop_session()
    svc._signal.enabled.return_value = False
    target = _make_target(channel="signal", address="group-id")
    payload = svc._build_notification_payload(
        alarm=_make_alarm(), enriched=_make_enriched(), step_no=0, ack_url=None
    )

    with patch.object(svc, "_log_notification_result", new_callable=AsyncMock) as mock_log:
        await svc._send_via_signal(session, target, payload["body"], payload)

    svc._signal.send_group_message.assert_not_called()
    mock_log.assert_called_once_with(session, target, payload, "skipped", "Signal not enabled")


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


async def test_send_via_sendxms_disabled_logs_skipped() -> None:
    svc, session = _make_svc(), await _noop_session()
    svc._sendxms.enabled.return_value = False
    target = _make_target(channel="sms", address="+491234")
    payload = svc._build_notification_payload(
        alarm=_make_alarm(), enriched=_make_enriched(), step_no=0, ack_url=None
    )

    with patch.object(svc, "_log_notification_result", new_callable=AsyncMock) as mock_log:
        await svc._send_via_sendxms(session, target, payload["body"], payload)

    svc._sendxms.send_sms.assert_not_called()
    mock_log.assert_called_once_with(session, target, payload, "skipped", "SendXMS not enabled")


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
        settings = _make_settings(webhook_allowed_hosts="example.com")
        await svc._send_to_channel(session, target, payload, settings)

    mock_hook.assert_called_once_with(session, target, payload, settings)


async def test_send_to_channel_propagates_unexpected_exception():
    """Dispatch must not hide programming or audit-persistence failures."""
    svc = _make_svc()
    session = await _noop_session()
    target = _make_target(channel="sms")
    payload = svc._build_notification_payload(
        alarm=_make_alarm(), enriched=_make_enriched(), step_no=0, ack_url=None
    )

    with patch.object(
        svc, "_send_sms_notifications", new_callable=AsyncMock, side_effect=RuntimeError("boom")
    ):
        with pytest.raises(RuntimeError, match="boom"):
            await svc._send_to_channel(session, target, payload)


# ── _send_email_notifications: success path ───────────────────────────


async def test_send_email_zammad_success_logs_ok():
    """When create_ticket succeeds, _log_notification_result is called with 'ok'."""
    svc, session, target, payload = await _delivery_context("email")
    svc._zammad.create_ticket = AsyncMock(return_value=55)

    with patch.object(svc, "_log_notification_result", new_callable=AsyncMock) as mock_log:
        await svc._send_email_notifications(session, target, payload)

    mock_log.assert_called_once_with(session, target, payload, "ok")
