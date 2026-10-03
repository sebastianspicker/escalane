"""Durable notification audit and retry identity helpers."""

from __future__ import annotations

import logging
import re
import uuid
from typing import Any, cast

import httpx
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from escalane.notifications.payloads import NotificationPayload
from escalane.persistence.models import AlarmNotification

logger = logging.getLogger("escalane")

_ACKNOWLEDGEMENT_URL = re.compile(
    r"https?://[^\s<>\"']+/a/[A-Za-z0-9_-]+(?:\?[^\s<>\"']*)?", re.IGNORECASE
)
_REDACTED_ACKNOWLEDGEMENT_URL = "[acknowledgement URL redacted]"


class NotificationDeliveryError(RuntimeError):
    """A retryable downstream notification delivery failure."""


class NotificationAuditError(NotificationDeliveryError):
    """A retryable failure to persist or read a notification delivery record."""


def safe_delivery_error(error: Exception) -> str:
    """Return bounded provider diagnostics without persisting request secrets."""
    if isinstance(error, httpx.HTTPStatusError):
        return f"Downstream provider returned HTTP {error.response.status_code}"
    if isinstance(error, httpx.TimeoutException):
        return "Downstream provider request timed out"
    if isinstance(error, (httpx.TransportError, OSError)):
        return "Downstream provider transport error"
    if isinstance(error, NotificationDeliveryError):
        return str(error)
    return f"Downstream provider error ({type(error).__name__})"


def is_retryable_delivery_error(error: Exception) -> bool:
    """Return whether the worker should retry an externally visible delivery.

    Providers can safely be retried only for ambiguous transport failures and
    the HTTP statuses documented as transient.  All other provider responses
    are durable failures: they are audited but must not make ARQ repeat an
    already-rejected request.
    """
    if isinstance(error, NotificationDeliveryError):
        return True
    if isinstance(error, (httpx.TransportError, OSError)):
        return True
    if isinstance(error, httpx.HTTPStatusError):
        return error.response.status_code in {408, 425, 429} or error.response.status_code >= 500
    return False


def notification_delivery_id(
    *,
    alarm_id: uuid.UUID,
    channel: str,
    target_id: str | None,
    payload: dict[str, Any] | NotificationPayload,
) -> str:
    """Return a deterministic identity for one logical delivery.

    It stays stable over ARQ retries and is safe to expose to generic webhook
    receivers for their own deduplication.
    """
    payload_data: dict[str, Any] = dict(payload)
    action = str(
        payload_data.get("action")
        or (
            f"state:{payload_data['state']}"
            if payload_data.get("state") is not None
            else f"step:{payload_data.get('step_no', '')}"
        )
    )
    ticket_id = str(payload_data.get("ticket_id", ""))
    seed = ":".join((str(alarm_id), channel, target_id or "", action, ticket_id))
    return str(uuid.uuid5(uuid.NAMESPACE_URL, seed))


def logical_delivery_key(payload: dict[str, Any] | NotificationPayload) -> str | None:
    """Return the indexed identity for one logical notification delivery."""
    payload_data: dict[str, Any] = dict(payload)
    action = payload_data.get("action")
    if action == "create_ticket":
        return "create_ticket"
    if action == "ack_update" and payload_data.get("ticket_id") is not None:
        return f"ack_update:{payload_data['ticket_id']}"
    if payload_data.get("state") is not None:
        return f"state:{payload_data['state']}"
    if payload_data.get("step_no") is not None:
        return f"step:{payload_data['step_no']}"
    return None


def _redact_audit_secrets(value: Any) -> Any:
    """Recursively remove acknowledgement capabilities from an audit-only copy."""
    if isinstance(value, str):
        return _ACKNOWLEDGEMENT_URL.sub(_REDACTED_ACKNOWLEDGEMENT_URL, value)
    if isinstance(value, dict):
        return {key: _redact_audit_secrets(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_audit_secrets(item) for item in value]
    return value


def payload_with_delivery_id(
    *,
    alarm_id: uuid.UUID,
    channel: str,
    target_id: str | None,
    payload: dict[str, Any] | NotificationPayload,
) -> dict[str, Any]:
    """Copy audit payload and attach its stable logical-delivery identity."""
    audit_payload = cast(dict[str, Any], _redact_audit_secrets(dict(payload)))
    audit_payload["delivery_id"] = notification_delivery_id(
        alarm_id=alarm_id,
        channel=channel,
        target_id=target_id,
        payload=payload,
    )
    return audit_payload


async def _rollback_failed_audit(session: AsyncSession, error: Exception) -> None:
    try:
        await session.rollback()
    except Exception:
        logger.exception(
            "notification_audit_rollback_failed",
            extra={"audit_error": str(error)},
        )


async def log_notification(
    session: AsyncSession,
    *,
    alarm_id: uuid.UUID,
    channel: str,
    target_id: str | None,
    payload: dict[str, Any] | NotificationPayload,
    result: str,
    error: str | None = None,
) -> None:
    """Persist one notification attempt before the worker returns or retries."""
    try:
        session.add(
            AlarmNotification(
                alarm_id=alarm_id,
                channel=channel,
                target_id=target_id,
                logical_delivery_key=logical_delivery_key(payload),
                payload=payload_with_delivery_id(
                    alarm_id=alarm_id,
                    channel=channel,
                    target_id=target_id,
                    payload=payload,
                ),
                result=result,
                error=error,
            )
        )
        await session.commit()
    except Exception as exc:
        await _rollback_failed_audit(session, exc)
        raise NotificationAuditError("Notification audit persistence failed") from exc


async def _matching_notification(
    session: AsyncSession,
    *,
    alarm_id: uuid.UUID,
    channel: str,
    target_id: str | None,
    payload_matches: dict[str, Any],
    results: tuple[str, ...],
) -> AlarmNotification | None:
    """Return the newest matching audit row with one of the requested outcomes."""
    target_filter = (
        AlarmNotification.target_id.is_(None)
        if target_id is None
        else AlarmNotification.target_id == target_id
    )
    key = logical_delivery_key(payload_matches)
    legacy_matches = []
    for payload_key, value in payload_matches.items():
        payload_value = AlarmNotification.payload[payload_key]
        if value is None:
            legacy_matches.append(payload_value.as_string().is_(None))
        elif isinstance(value, bool):
            legacy_matches.append(payload_value.as_boolean() == value)
        elif isinstance(value, int):
            legacy_matches.append(payload_value.as_integer() == value)
        elif isinstance(value, float):
            legacy_matches.append(payload_value.as_float() == value)
        else:
            legacy_matches.append(payload_value.as_string() == str(value))

    identity_filter = and_(*legacy_matches)
    if key is not None:
        identity_filter = or_(
            AlarmNotification.logical_delivery_key == key,
            and_(AlarmNotification.logical_delivery_key.is_(None), *legacy_matches),
        )

    try:
        return cast(
            AlarmNotification | None,
            await session.scalar(
                select(AlarmNotification)
                .where(AlarmNotification.alarm_id == alarm_id)
                .where(AlarmNotification.channel == channel)
                .where(target_filter)
                .where(AlarmNotification.result.in_(results))
                .where(identity_filter)
                .order_by(AlarmNotification.created_at.desc(), AlarmNotification.id.desc())
                .limit(1)
            ),
        )
    except Exception as exc:
        await _rollback_failed_audit(session, exc)
        raise NotificationAuditError("Notification audit lookup failed") from exc


async def successful_notification(
    session: AsyncSession,
    *,
    alarm_id: uuid.UUID,
    channel: str,
    target_id: str | None,
    payload_matches: dict[str, Any],
) -> AlarmNotification | None:
    """Return a durable successful attempt matching one logical delivery."""
    return await _matching_notification(
        session,
        alarm_id=alarm_id,
        channel=channel,
        target_id=target_id,
        payload_matches=payload_matches,
        results=("ok",),
    )


async def completed_notification(
    session: AsyncSession,
    *,
    alarm_id: uuid.UUID,
    channel: str,
    target_id: str | None,
    payload_matches: dict[str, Any],
) -> AlarmNotification | None:
    """Return a success or permanent rejection that must not be retried."""
    return await _matching_notification(
        session,
        alarm_id=alarm_id,
        channel=channel,
        target_id=target_id,
        payload_matches=payload_matches,
        results=("ok", "permanent_error"),
    )


def zammad_ack_note(acked_by: str | None, acked_at: Any, note: str | None) -> tuple[str, str]:
    """Build the subject and body for a Zammad acknowledgment note."""
    body_parts = [
        f"ACK durch: {acked_by or '-'}",
        f"Zeit: {acked_at.isoformat()}",
    ]
    if note:
        body_parts.append(f"Notiz: {note}")
    return "Alarm quittiert", "\n".join(body_parts)
