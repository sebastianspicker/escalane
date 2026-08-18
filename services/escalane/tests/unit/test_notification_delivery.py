"""Characterize durable notification-delivery audit and retry boundaries."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from escalane.services.notification_delivery import (
    NotificationAuditError,
    NotificationDeliveryError,
    completed_notification,
    is_retryable_delivery_error,
    log_notification,
    notification_delivery_id,
    safe_delivery_error,
    successful_notification,
    zammad_ack_note,
)

try:
    from tests.notification_dispatch_helpers import _ALARM_ID, _noop_session
except ModuleNotFoundError:
    from notification_dispatch_helpers import _ALARM_ID, _noop_session


pytestmark = pytest.mark.unit


def _status_error(status_code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://provider.example.test/send?token=very-secret")
    response = httpx.Response(status_code, request=request)
    return httpx.HTTPStatusError(
        "provider rejected query token=very-secret", request=request, response=response
    )


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (NotificationDeliveryError("audit unavailable"), True),
        (httpx.ConnectError("unreachable"), True),
        (OSError("network down"), True),
        *[(_status_error(status), True) for status in (408, 425, 429, 500, 503)],
        *[(_status_error(status), False) for status in (400, 401, 404)],
        (ValueError("invalid payload"), False),
    ],
)
def test_retry_classification_exception_and_status_matrix(error: Exception, expected: bool) -> None:
    assert is_retryable_delivery_error(error) is expected


async def test_log_notification_adds_deterministic_delivery_payload_and_commits() -> None:
    session = await _noop_session()
    payload = {"action": "ack", "ticket_id": "42", "body": "Alarm"}
    original_payload = dict(payload)
    events: list[str] = []
    session.add.side_effect = lambda record: events.append("add")

    async def commit() -> None:
        events.append("commit")

    session.commit.side_effect = commit

    await log_notification(
        session,
        alarm_id=_ALARM_ID,
        channel="signal",
        target_id="group-1",
        payload=payload,
        result="ok",
    )

    record = session.add.call_args.args[0]
    expected_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{_ALARM_ID}:signal:group-1:ack:42"))
    assert payload == original_payload
    assert record.payload == {**original_payload, "delivery_id": expected_id}
    assert events == ["add", "commit"]
    session.commit.assert_awaited_once()


async def test_log_notification_rolls_back_and_raises_auditable_error() -> None:
    session = await _noop_session()
    events: list[str] = []
    session.add.side_effect = lambda record: events.append("add")

    async def commit() -> None:
        events.append("commit")
        raise RuntimeError("database unavailable")

    async def rollback() -> None:
        events.append("rollback")

    session.commit.side_effect = commit
    session.rollback = AsyncMock(side_effect=rollback)

    with pytest.raises(NotificationAuditError, match="Notification audit persistence failed"):
        await log_notification(
            session,
            alarm_id=_ALARM_ID,
            channel="sms",
            target_id="target-1",
            payload={"step_no": 1},
            result="error",
        )

    session.add.assert_called_once()
    session.rollback.assert_awaited_once()
    assert events == ["add", "commit", "rollback"]


async def test_audit_rollback_failure_is_logged_without_masking_audit_error() -> None:
    session = await _noop_session()
    session.commit.side_effect = RuntimeError("commit failed")
    session.rollback.side_effect = RuntimeError("rollback failed")

    with patch("escalane.services.notification_delivery.logger.exception") as logged:
        with pytest.raises(NotificationAuditError, match="Notification audit persistence failed"):
            await log_notification(
                session,
                alarm_id=_ALARM_ID,
                channel="sms",
                target_id="target-1",
                payload={"step_no": 1},
                result="error",
            )

    logged.assert_called_once_with(
        "notification_audit_rollback_failed", extra={"audit_error": "commit failed"}
    )


def test_delivery_id_is_uuid5_stable_and_changes_with_delivery_identity_inputs() -> None:
    baseline = {"action": "notify", "ticket_id": "9"}
    stable = notification_delivery_id(
        alarm_id=_ALARM_ID, channel="sms", target_id="target-1", payload=baseline
    )
    assert stable == notification_delivery_id(
        alarm_id=_ALARM_ID, channel="sms", target_id="target-1", payload=baseline
    )
    assert uuid.UUID(stable).version == 5
    assert stable != notification_delivery_id(
        alarm_id=uuid.uuid4(), channel="sms", target_id="target-1", payload=baseline
    )
    assert stable != notification_delivery_id(
        alarm_id=_ALARM_ID, channel="signal", target_id="target-1", payload=baseline
    )
    assert stable != notification_delivery_id(
        alarm_id=_ALARM_ID, channel="sms", target_id="target-2", payload=baseline
    )
    assert stable != notification_delivery_id(
        alarm_id=_ALARM_ID,
        channel="sms",
        target_id="target-1",
        payload={"action": "ack", "ticket_id": "9"},
    )
    assert stable != notification_delivery_id(
        alarm_id=_ALARM_ID,
        channel="sms",
        target_id="target-1",
        payload={"action": "notify", "ticket_id": "10"},
    )
    assert notification_delivery_id(
        alarm_id=_ALARM_ID, channel="sms", target_id=None, payload=baseline
    ) == str(uuid.uuid5(uuid.NAMESPACE_URL, f"{_ALARM_ID}:sms::notify:9"))
    assert notification_delivery_id(
        alarm_id=_ALARM_ID, channel="sms", target_id="target-1", payload={"state": "open"}
    ) != notification_delivery_id(
        alarm_id=_ALARM_ID, channel="sms", target_id="target-1", payload={"state": "closed"}
    )
    assert notification_delivery_id(
        alarm_id=_ALARM_ID, channel="sms", target_id="target-1", payload={"step_no": 1}
    ) != notification_delivery_id(
        alarm_id=_ALARM_ID, channel="sms", target_id="target-1", payload={"step_no": 2}
    )


def test_safe_delivery_error_is_bounded_and_never_uses_provider_query_text() -> None:
    status = safe_delivery_error(_status_error(503))
    timeout = safe_delivery_error(httpx.TimeoutException("late"))
    transport = safe_delivery_error(httpx.ConnectError("unreachable"))
    delivery = safe_delivery_error(NotificationDeliveryError("audit unavailable"))
    generic = safe_delivery_error(RuntimeError(f"secret-token={'x' * 10_000}"))

    assert status == "Downstream provider returned HTTP 503"
    assert "very-secret" not in status
    assert timeout == "Downstream provider request timed out"
    assert transport == "Downstream provider transport error"
    assert delivery == "audit unavailable"
    assert generic == "Downstream provider error (RuntimeError)"
    assert len(generic) < 128


async def test_successful_and_completed_lookup_use_their_public_result_sets() -> None:
    matching = SimpleNamespace(payload={"delivery_id": "same"})
    non_matching = SimpleNamespace(payload={"delivery_id": "other"})
    rows = MagicMock()
    rows.all.return_value = [non_matching, matching]
    session = await _noop_session()
    session.scalars = AsyncMock(return_value=rows)

    assert (
        await successful_notification(
            session,
            alarm_id=_ALARM_ID,
            channel="sms",
            target_id="target-1",
            payload_matches={"delivery_id": "same"},
        )
        is matching
    )
    successful_params = session.scalars.await_args.args[0].compile().params.values()
    assert ["ok"] in successful_params

    assert (
        await completed_notification(
            session,
            alarm_id=_ALARM_ID,
            channel="sms",
            target_id="target-1",
            payload_matches={"delivery_id": "same"},
        )
        is matching
    )
    completed_params = session.scalars.await_args.args[0].compile().params.values()
    assert ["ok", "permanent_error"] in completed_params


async def test_lookup_failure_rolls_back_and_raises_notification_audit_error() -> None:
    session = await _noop_session()
    session.scalars.side_effect = RuntimeError("database unavailable")
    session.rollback = AsyncMock()

    with pytest.raises(NotificationAuditError, match="Notification audit lookup failed"):
        await completed_notification(
            session,
            alarm_id=_ALARM_ID,
            channel="sms",
            target_id=None,
            payload_matches={"delivery_id": "same"},
        )

    session.rollback.assert_awaited_once()


def test_zammad_ack_note_keeps_subject_and_optional_note_contract() -> None:
    acked_at = datetime(2026, 8, 11, 12, 30, tzinfo=UTC)
    assert zammad_ack_note("operator", acked_at, "resolved") == (
        "Alarm quittiert",
        "ACK durch: operator\nZeit: 2026-08-11T12:30:00+00:00\nNotiz: resolved",
    )
    assert zammad_ack_note(None, acked_at, None) == (
        "Alarm quittiert",
        "ACK durch: -\nZeit: 2026-08-11T12:30:00+00:00",
    )
