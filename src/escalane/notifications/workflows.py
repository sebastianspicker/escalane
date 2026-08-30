"""Notification-owned delivery workflows invoked by worker task adapters."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from escalane.config import constants
from escalane.config.settings import Settings
from escalane.notifications.delivery import (
    NotificationAuditError,
    NotificationDeliveryError,
    is_retryable_delivery_error,
    notification_delivery_id,
    safe_delivery_error,
    successful_notification,
)
from escalane.operations.metrics import record_event
from escalane.persistence.models import Alarm
from escalane.providers.webhook import post_webhook_bytes_to_validated_addresses
from escalane.security.url_validation import (
    RetryableSSRFError,
    SSRFError,
    redact_url_for_logging,
    validate_url_not_internal,
    validate_webhook_host_allowed,
)

logger = logging.getLogger("escalane")


def ack_url_for_alarm(alarm: Alarm, settings: Settings, *, alarm_id: str) -> str | None:
    """Build an ACK URL, retaining delivery when a historical token is absent."""
    if alarm.ack_token:
        return f"{settings.base_url}/a/{alarm.ack_token}"
    logger.warning("alarm_missing_ack_token", extra={"alarm_id": alarm_id})
    return None


def ack_note_delivery_error(
    success: bool, *, alarm_id: str, ticket_id: int | None
) -> NotificationDeliveryError | None:
    """Log an ACK-note outcome and return a retryable failure when needed."""
    extra = {"alarm_id": alarm_id, "ticket_id": ticket_id}
    if success:
        logger.info("ack_note_added", extra=extra)
        return None
    logger.warning("ack_note_failed", extra=extra)
    return NotificationDeliveryError("Zammad acknowledgment delivery failed")


async def restore_zammad_ticket_id(session: AsyncSession, alarm: Alarm) -> None:
    """Restore a ticket ID after a crash between audit persistence and alarm commit."""
    if alarm.zammad_ticket_id is not None:
        return
    prior_ticket = await successful_notification(
        session,
        alarm_id=alarm.id,
        channel="zammad",
        target_id=None,
        payload_matches={"action": "create_ticket"},
    )
    prior_ticket_id = prior_ticket.payload.get("ticket_id") if prior_ticket else None
    if isinstance(prior_ticket_id, int) and not isinstance(prior_ticket_id, bool):
        alarm.zammad_ticket_id = prior_ticket_id
        await session.commit()


async def deliver_initial_notifications(
    session: AsyncSession,
    alarm: Alarm,
    *,
    notification: Any,
    enriched: Any,
    ack_url: str | None,
    settings: Settings,
) -> NotificationDeliveryError | None:
    """Attempt ticket creation and stage-zero fan-out without masking either failure."""
    errors: list[NotificationDeliveryError] = []
    if alarm.zammad_ticket_id is None:
        try:
            ticket_id = await notification.handle_zammad_ticket(session, alarm, enriched, ack_url)
        except NotificationAuditError:
            raise
        except NotificationDeliveryError as exc:
            errors.append(exc)
        else:
            if ticket_id:
                alarm.zammad_ticket_id = ticket_id
                await session.commit()
    try:
        await notification.send(
            session=session,
            alarm=alarm,
            enriched=enriched,
            step_no=0,
            ack_url=ack_url,
            settings=settings,
        )
    except NotificationDeliveryError as exc:
        errors.append(exc)
    return errors[0] if errors else None


async def _log_rejected_webhook(
    session: AsyncSession,
    *,
    alarm: Alarm,
    state: str,
    webhook_url: str,
    error: str,
    log_notification: Callable[..., Awaitable[None]],
) -> None:
    """Audit a permanent SSRF rejection without scheduling an unsafe retry."""
    logger.warning(
        "webhook_url_rejected",
        extra={
            "alarm_id": str(alarm.id),
            "webhook_url": redact_url_for_logging(webhook_url),
            "error": error,
        },
    )
    await log_notification(
        session,
        alarm_id=alarm.id,
        channel="webhook",
        target_id=None,
        payload={"state": state},
        result="skipped",
        error=error,
    )
    record_event("webhook_delivery_error")


async def _validated_state_webhook_addresses(
    session: AsyncSession,
    *,
    alarm: Alarm,
    state: str,
    settings: Settings,
    log_notification: Callable[..., Awaitable[None]],
    validate_url: Callable[..., Awaitable[Sequence[str] | None]] = validate_url_not_internal,
) -> tuple[str, ...] | None:
    """Return pinned addresses or durably record a permanent URL rejection."""
    try:
        validate_webhook_host_allowed(settings.webhook_url, settings.webhook_allowed_hosts)
        addresses = await validate_url(settings.webhook_url, allow_http=settings.simulation_enabled)
    except RetryableSSRFError as exc:
        await log_notification(
            session,
            alarm_id=alarm.id,
            channel="webhook",
            target_id=None,
            payload={"state": state},
            result="error",
            error=str(exc),
        )
        record_event("webhook_delivery_error")
        raise
    except SSRFError as exc:
        await _log_rejected_webhook(
            session,
            alarm=alarm,
            state=state,
            webhook_url=settings.webhook_url,
            error=str(exc),
            log_notification=log_notification,
        )
        return None
    return tuple(addresses or ())


def _state_webhook_payload(alarm: Alarm, state: str) -> dict[str, Any]:
    """Build a timestamped state-change webhook payload."""
    return {
        "event": constants.EVENT_ALARM_STATE_CHANGED,
        "alarm_id": str(alarm.id),
        "state": state,
        "timestamp": datetime.now(UTC).isoformat(),
        "created_at": alarm.created_at.isoformat() if alarm.created_at else None,
        "acked_at": alarm.acked_at.isoformat() if alarm.acked_at else None,
        "resolved_at": alarm.resolved_at.isoformat() if alarm.resolved_at else None,
        "cancelled_at": alarm.cancelled_at.isoformat() if alarm.cancelled_at else None,
        "person_id": alarm.person_id,
        "room_id": alarm.room_id,
        "site_id": alarm.site_id,
        "device_id": alarm.device_id,
    }


def _state_webhook_headers(settings: Settings, payload_bytes: bytes) -> dict[str, str]:
    """Produce HMAC headers for a state webhook payload."""
    headers = {"Content-Type": "application/json"}
    if settings.webhook_secret:
        signature = hmac.new(
            settings.webhook_secret.encode(), payload_bytes, hashlib.sha256
        ).hexdigest()
        headers["X-Hub-Signature-256"] = f"sha256={signature}"
    return headers


async def _send_state_webhook(
    http: Any,
    *,
    session: AsyncSession,
    alarm: Alarm,
    state: str,
    settings: Settings,
    payload_bytes: bytes,
    delivery_id: str,
    resolved_addresses: Sequence[str],
    log_notification: Callable[..., Awaitable[None]],
) -> None:
    """Send and audit a state callback, surfacing only retryable failures."""
    try:
        await post_webhook_bytes_to_validated_addresses(
            http,
            settings.webhook_url,
            payload_bytes,
            _state_webhook_headers(settings, payload_bytes),
            settings.webhook_timeout_seconds,
            delivery_id,
            resolved_addresses,
            log_extra={"alarm_id": str(alarm.id), "state": state},
        )
    except Exception as exc:
        safe_error = safe_delivery_error(exc)
        logger.error(
            "webhook_delivery_failed",
            extra={"alarm_id": str(alarm.id), "state": state, "error": safe_error},
        )
        await log_notification(
            session,
            alarm_id=alarm.id,
            channel="webhook",
            target_id=None,
            payload={"state": state},
            result="error",
            error=safe_error,
        )
        record_event("webhook_delivery_error")
        if is_retryable_delivery_error(exc):
            raise NotificationDeliveryError("State webhook delivery failed") from exc
        return
    await log_notification(
        session,
        alarm_id=alarm.id,
        channel="webhook",
        target_id=None,
        payload={"state": state},
        result="ok",
    )
    record_event("webhook_delivery_ok")


async def deliver_state_webhook(
    session: AsyncSession,
    alarm: Alarm,
    *,
    state: str,
    settings: Settings,
    http: Any,
    log_notification: Callable[..., Awaitable[None]],
) -> None:
    """Send one durable state transition callback unless it already succeeded."""
    if await successful_notification(
        session,
        alarm_id=alarm.id,
        channel="webhook",
        target_id=None,
        payload_matches={"state": state},
    ):
        logger.info(
            "webhook_delivery_already_complete",
            extra={"alarm_id": str(alarm.id), "state": state},
        )
        return
    resolved_addresses = await _validated_state_webhook_addresses(
        session,
        alarm=alarm,
        state=state,
        settings=settings,
        log_notification=log_notification,
    )
    if resolved_addresses is None:
        return
    payload_bytes = json.dumps(_state_webhook_payload(alarm, state), separators=(",", ":")).encode()
    delivery_id = notification_delivery_id(
        alarm_id=alarm.id,
        channel="webhook",
        target_id=None,
        payload={"state": state},
    )
    await _send_state_webhook(
        http,
        session=session,
        alarm=alarm,
        state=state,
        settings=settings,
        payload_bytes=payload_bytes,
        delivery_id=delivery_id,
        resolved_addresses=resolved_addresses,
        log_notification=log_notification,
    )
