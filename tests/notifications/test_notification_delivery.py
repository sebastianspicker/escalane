"""Characterize durable notification-delivery audit and retry boundaries."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from escalane.notifications.delivery import (
    NotificationAuditError,
    NotificationDeliveryError,
    completed_notification,
    is_retryable_delivery_error,
    log_notification,
    logical_delivery_key,
    notification_delivery_id,
    safe_delivery_error,
    successful_notification,
    zammad_ack_note,
)
from tests.support.notifications import ALARM_ID, noop_session

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
    session = await noop_session()
    payload = {"action": "ack", "ticket_id": "42", "body": "Alarm"}
    original_payload = dict(payload)
    events: list[str] = []
    session.add.side_effect = lambda record: events.append("add")

    async def commit() -> None:
        events.append("commit")

    session.commit.side_effect = commit

    await log_notification(
        session,
        alarm_id=ALARM_ID,
        channel="signal",
        target_id="group-1",
        payload=payload,
        result="ok",
    )

    record = session.add.call_args.args[0]
    expected_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{ALARM_ID}:signal:group-1:ack:42"))
    assert payload == original_payload
    assert record.payload == {**original_payload, "delivery_id": expected_id}
    assert record.logical_delivery_key is None
    assert events == ["add", "commit"]
    session.commit.assert_awaited_once()


async def test_log_notification_recursively_redacts_acknowledgement_urls_from_audit_copy() -> None:
    session = await noop_session()
    secret = "ack-secret-capability"
    ack_url = f"https://alarm.example.test/a/{secret}?lang=de"
    payload = {
        "body": f"Alarm\nQuittieren: {ack_url}",
        "nested": [{"url": ack_url}],
    }

    await log_notification(
        session,
        alarm_id=ALARM_ID,
        channel="signal",
        target_id="group-1",
        payload=payload,
        result="ok",
    )

    audit_payload = session.add.call_args.args[0].payload
    assert secret in payload["body"]
    assert secret not in str(audit_payload)
    assert audit_payload["body"].endswith("[acknowledgement URL redacted]")
    assert audit_payload["nested"] == [{"url": "[acknowledgement URL redacted]"}]


async def test_log_notification_redacts_acknowledgement_url_with_uppercase_scheme() -> None:
    session = await noop_session()
    secret = "uppercase-scheme-secret"

    await log_notification(
        session,
        alarm_id=ALARM_ID,
        channel="sms",
        target_id="target-1",
        payload={"body": f"Quittieren: HTTP://localhost:8080/a/{secret}"},
        result="ok",
    )

    assert secret not in str(session.add.call_args.args[0].payload)


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        ({"action": "create_ticket", "ticket_id": 42}, "create_ticket"),
        ({"action": "ack_update", "ticket_id": 42}, "ack_update:42"),
        ({"state": "acknowledged"}, "state:acknowledged"),
        ({"step_no": 3}, "step:3"),
    ],
)
def test_logical_delivery_keys_are_stable_and_ignore_ticket_creation_result(
    payload: dict[str, object], expected: str
) -> None:
    assert logical_delivery_key(payload) == expected


async def test_log_notification_rolls_back_and_raises_auditable_error() -> None:
    session = await noop_session()
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
            alarm_id=ALARM_ID,
            channel="sms",
            target_id="target-1",
            payload={"step_no": 1},
            result="error",
        )

    session.add.assert_called_once()
    session.rollback.assert_awaited_once()
    assert events == ["add", "commit", "rollback"]


async def test_audit_rollback_failure_is_logged_without_masking_audit_error() -> None:
    session = await noop_session()
    session.commit.side_effect = RuntimeError("commit failed")
    session.rollback.side_effect = RuntimeError("rollback failed")

    with patch("escalane.notifications.delivery.logger.exception") as logged:
        with pytest.raises(NotificationAuditError, match="Notification audit persistence failed"):
            await log_notification(
                session,
                alarm_id=ALARM_ID,
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
        alarm_id=ALARM_ID, channel="sms", target_id="target-1", payload=baseline
    )
    assert stable == notification_delivery_id(
        alarm_id=ALARM_ID, channel="sms", target_id="target-1", payload=baseline
    )
    assert uuid.UUID(stable).version == 5
    assert stable != notification_delivery_id(
        alarm_id=uuid.uuid4(), channel="sms", target_id="target-1", payload=baseline
    )
    assert stable != notification_delivery_id(
        alarm_id=ALARM_ID, channel="signal", target_id="target-1", payload=baseline
    )
    assert stable != notification_delivery_id(
        alarm_id=ALARM_ID, channel="sms", target_id="target-2", payload=baseline
    )
    assert stable != notification_delivery_id(
        alarm_id=ALARM_ID,
        channel="sms",
        target_id="target-1",
        payload={"action": "ack", "ticket_id": "9"},
    )
    assert stable != notification_delivery_id(
        alarm_id=ALARM_ID,
        channel="sms",
        target_id="target-1",
        payload={"action": "notify", "ticket_id": "10"},
    )
    assert notification_delivery_id(
        alarm_id=ALARM_ID, channel="sms", target_id=None, payload=baseline
    ) == str(uuid.uuid5(uuid.NAMESPACE_URL, f"{ALARM_ID}:sms::notify:9"))
    assert notification_delivery_id(
        alarm_id=ALARM_ID, channel="sms", target_id="target-1", payload={"state": "open"}
    ) != notification_delivery_id(
        alarm_id=ALARM_ID, channel="sms", target_id="target-1", payload={"state": "closed"}
    )
    assert notification_delivery_id(
        alarm_id=ALARM_ID, channel="sms", target_id="target-1", payload={"step_no": 1}
    ) != notification_delivery_id(
        alarm_id=ALARM_ID, channel="sms", target_id="target-1", payload={"step_no": 2}
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
    matching = object()
    session = await noop_session()
    session.scalar = AsyncMock(return_value=matching)

    assert (
        await successful_notification(
            session,
            alarm_id=ALARM_ID,
            channel="sms",
            target_id="target-1",
            payload_matches={"delivery_id": "same"},
        )
        is matching
    )
    successful_statement = session.scalar.await_args.args[0]
    successful_params = successful_statement.compile().params.values()
    assert ["ok"] in successful_params
    assert successful_statement._limit_clause is not None

    assert (
        await completed_notification(
            session,
            alarm_id=ALARM_ID,
            channel="sms",
            target_id="target-1",
            payload_matches={"delivery_id": "same"},
        )
        is matching
    )
    completed_params = session.scalar.await_args.args[0].compile().params.values()
    assert ["ok", "permanent_error"] in completed_params


async def test_lookup_failure_rolls_back_and_raises_notification_audit_error() -> None:
    session = await noop_session()
    session.scalar.side_effect = RuntimeError("database unavailable")
    session.rollback = AsyncMock()

    with pytest.raises(NotificationAuditError, match="Notification audit lookup failed"):
        await completed_notification(
            session,
            alarm_id=ALARM_ID,
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
