"""Recovery, fan-out, and state-webhook behavior for notification workflows."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from escalane.config.settings import Settings
from escalane.contracts.alarms import AlarmStatus
from escalane.notifications import workflows
from escalane.notifications.delivery import NotificationDeliveryError
from escalane.persistence.models import Alarm
from escalane.security.url_validation import RetryableSSRFError, SSRFError


def _alarm(*, ticket: int | None = None, token: str | None = "ack") -> Alarm:
    return Alarm(
        id=uuid.uuid4(),
        status=AlarmStatus.TRIGGERED,
        source="test",
        event="alarm.trigger",
        created_at=datetime.now(UTC),
        ack_token=token,
        zammad_ticket_id=ticket,
        meta={},
    )


def _settings() -> Settings:
    return Settings(
        _env_file=None,
        simulation_enabled=True,
        admin_api_key="workflow-test-key",
        webhook_enabled=True,
        webhook_url="https://hooks.example.test/events",
        webhook_allowed_hosts="hooks.example.test",
        webhook_secret="x" * 32,
    )


def test_ack_url_and_ack_note_failure_have_stable_operator_contracts(
    caplog: pytest.LogCaptureFixture,
) -> None:
    alarm = _alarm(token=None)
    assert workflows.ack_url_for_alarm(alarm, _settings(), alarm_id=str(alarm.id)) is None
    assert workflows.ack_note_delivery_error(True, alarm_id=str(alarm.id), ticket_id=7) is None
    error = workflows.ack_note_delivery_error(False, alarm_id=str(alarm.id), ticket_id=7)
    assert isinstance(error, NotificationDeliveryError)
    assert "alarm_missing_ack_token" in caplog.text


@pytest.mark.asyncio
async def test_restore_ticket_uses_only_a_non_boolean_durable_ticket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = MagicMock()
    session.commit = AsyncMock()
    alarm = _alarm()
    monkeypatch.setattr(
        workflows,
        "successful_notification",
        AsyncMock(return_value=SimpleNamespace(payload={"ticket_id": 44})),
    )
    await workflows.restore_zammad_ticket_id(session, alarm)
    assert alarm.zammad_ticket_id == 44
    session.commit.assert_awaited_once()

    alarm = _alarm()
    session.commit.reset_mock()
    monkeypatch.setattr(
        workflows,
        "successful_notification",
        AsyncMock(return_value=SimpleNamespace(payload={"ticket_id": True})),
    )
    await workflows.restore_zammad_ticket_id(session, alarm)
    assert alarm.zammad_ticket_id is None
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_initial_delivery_continues_stage_zero_after_ticket_failure() -> None:
    session = MagicMock()
    session.commit = AsyncMock()
    alarm = _alarm()
    notification = MagicMock()
    notification.handle_zammad_ticket = AsyncMock(
        side_effect=NotificationDeliveryError("ticket down")
    )
    notification.send = AsyncMock()

    result = await workflows.deliver_initial_notifications(
        session, alarm, notification=notification, enriched={}, ack_url=None, settings=_settings()
    )

    assert isinstance(result, NotificationDeliveryError)
    notification.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_validated_webhook_addresses_audit_permanent_and_retryable_failures() -> None:
    session = MagicMock()
    alarm = _alarm()
    audit = AsyncMock()

    permanent = await workflows._validated_state_webhook_addresses(
        session,
        alarm=alarm,
        state="triggered",
        settings=_settings(),
        log_notification=audit,
        validate_url=AsyncMock(side_effect=SSRFError("blocked target")),
    )
    assert permanent is None
    assert audit.await_args.kwargs["result"] == "skipped"

    audit.reset_mock()
    with pytest.raises(RetryableSSRFError, match="resolver unavailable"):
        await workflows._validated_state_webhook_addresses(
            session,
            alarm=alarm,
            state="triggered",
            settings=_settings(),
            log_notification=audit,
            validate_url=AsyncMock(side_effect=RetryableSSRFError("resolver unavailable")),
        )
    assert audit.await_args.kwargs["result"] == "error"


@pytest.mark.asyncio
async def test_state_webhook_records_safe_failure_or_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = MagicMock()
    alarm = _alarm()
    audit = AsyncMock()
    monkeypatch.setattr(
        workflows,
        "post_webhook_bytes_to_validated_addresses",
        AsyncMock(side_effect=httpx.ConnectError("downstream unavailable")),
    )
    with pytest.raises(NotificationDeliveryError):
        await workflows._send_state_webhook(
            MagicMock(),
            session=session,
            alarm=alarm,
            state="triggered",
            settings=_settings(),
            payload_bytes=b"{}",
            delivery_id="delivery",
            resolved_addresses=("1.1.1.1",),
            log_notification=audit,
        )
    assert audit.await_args.kwargs["result"] == "error"

    audit.reset_mock()
    monkeypatch.setattr(workflows, "post_webhook_bytes_to_validated_addresses", AsyncMock())
    await workflows._send_state_webhook(
        MagicMock(),
        session=session,
        alarm=alarm,
        state="acknowledged",
        settings=_settings(),
        payload_bytes=b"{}",
        delivery_id="delivery",
        resolved_addresses=("1.1.1.1",),
        log_notification=audit,
    )
    assert audit.await_args.kwargs["result"] == "ok"
