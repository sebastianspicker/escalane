"""Durable notification audit and retry identity helpers."""

from __future__ import annotations

import logging
import uuid
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from escalane.contracts.notifications import NotificationPayload
from escalane.persistence.models import AlarmNotification

logger = logging.getLogger("escalane")


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


def payload_with_delivery_id(
    *,
    alarm_id: uuid.UUID,
    channel: str,
    target_id: str | None,
    payload: dict[str, Any] | NotificationPayload,
) -> dict[str, Any]:
    """Copy audit payload and attach its stable logical-delivery identity."""
    audit_payload = dict(payload)
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
    try:
        rows = (
            await session.scalars(
                select(AlarmNotification)
                .where(AlarmNotification.alarm_id == alarm_id)
                .where(AlarmNotification.channel == channel)
                .where(target_filter)
                .where(AlarmNotification.result.in_(results))
                .order_by(AlarmNotification.created_at.desc())
            )
        ).all()
    except Exception as exc:
        await _rollback_failed_audit(session, exc)
        raise NotificationAuditError("Notification audit lookup failed") from exc
    for row in rows:
        payload = row.payload if isinstance(row.payload, dict) else {}
        if all(payload.get(key) == value for key, value in payload_matches.items()):
            return row
    return None


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
