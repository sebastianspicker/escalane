"""Notification-owned delivery workflows invoked by worker task adapters."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from escalane.alarms.enrichment import EnrichedAlarmContext, enrich_alarm_context
from escalane.alarms.outbox import EVENT_ALARM_STATE_CHANGED
from escalane.config.settings import Settings
from escalane.notifications.delivery import (
    NotificationAuditError,
    NotificationDeliveryError,
    completed_notification,
    is_retryable_delivery_error,
    log_notification,
    notification_delivery_id,
    safe_delivery_error,
    successful_notification,
)
from escalane.notifications.dispatch import NotificationService
from escalane.notifications.webhooks import (
    WebhookDnsFailure,
    WebhookRejected,
    resolve_webhook_addresses,
)
from escalane.persistence.models import Alarm, AlarmStatus
from escalane.providers.webhook import WebhookClientPool, post_webhook_bytes_to_validated_addresses
from escalane.security.url_validation import redact_url_for_logging
from escalane.telemetry.metrics import observe_latency, record_event

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
    notification: NotificationService,
    enriched: EnrichedAlarmContext,
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


async def deliver_new_alarm(
    session: AsyncSession,
    alarm: Alarm,
    *,
    notification: NotificationService,
    settings: Settings,
    alarm_id: str,
) -> NotificationDeliveryError | None:
    """Enrich a new alarm and attempt its initial delivery.

    Returns:
        The first delivery failure to retry, or None when delivery completed.
    """
    enriched = await enrich_alarm_context(session, alarm)
    ack_url = ack_url_for_alarm(alarm, settings, alarm_id=alarm_id)
    await restore_zammad_ticket_id(session, alarm)
    return await deliver_initial_notifications(
        session,
        alarm,
        notification=notification,
        enriched=enriched,
        ack_url=ack_url,
        settings=settings,
    )


async def deliver_escalation_step(
    session: AsyncSession,
    alarm: Alarm,
    *,
    notification: NotificationService,
    step_no: int,
    settings: Settings,
    alarm_id: str,
) -> None:
    """Notify one escalation step while the alarm is still unacknowledged.

    Raises:
        NotificationDeliveryError: A target delivery must be retried.
    """
    if alarm.status != AlarmStatus.TRIGGERED:
        logger.info(
            "escalation_skipped",
            extra={
                "alarm_id": alarm_id,
                "step_no": step_no,
                "status": alarm.status.value,
            },
        )
        return

    enriched = await enrich_alarm_context(session, alarm)
    ack_url = ack_url_for_alarm(alarm, settings, alarm_id=alarm_id)
    await notification.send(
        session=session,
        alarm=alarm,
        enriched=enriched,
        step_no=step_no,
        ack_url=ack_url,
        settings=settings,
    )
    logger.info(
        "escalation_completed",
        extra={"alarm_id": alarm_id, "step_no": step_no},
    )


async def deliver_acknowledgement_note(
    session: AsyncSession,
    alarm: Alarm,
    *,
    notification: NotificationService,
    acked_by: str | None,
    note: str | None,
    alarm_id: str,
) -> NotificationDeliveryError | None:
    """Add the ACK note to the alarm's Zammad ticket, returning a retryable failure."""
    if not notification.zammad_enabled():
        logger.debug("zammad_disabled", extra={"alarm_id": alarm_id})
        return None

    if not alarm.zammad_ticket_id:
        logger.warning(
            "ack_no_zammad_ticket",
            extra={
                "alarm_id": alarm_id,
                "detail": "Zammad ticket ID is None; ACK note will not be sent. "
                "This may indicate a prior Zammad ticket creation failure.",
            },
        )
        return NotificationDeliveryError("Zammad ticket creation is incomplete")

    success = await notification.add_zammad_ack_note(
        session,
        alarm_id=alarm.id,
        ticket_id=alarm.zammad_ticket_id,
        acked_by=acked_by,
        acked_at=alarm.acked_at or datetime.now(UTC),
        note=note,
    )
    return ack_note_delivery_error(success, alarm_id=alarm_id, ticket_id=alarm.zammad_ticket_id)


async def _log_rejected_webhook(
    session: AsyncSession,
    *,
    alarm: Alarm,
    state: str,
    webhook_url: str,
    error: str,
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
) -> tuple[str, ...] | None:
    """Return pinned addresses or durably record a permanent URL rejection.

    Raises:
        RetryableSSRFError: DNS resolution failed transiently; the attempt was audited.
    """
    resolution = await resolve_webhook_addresses(settings.webhook_url, settings)
    if isinstance(resolution, WebhookDnsFailure):
        await log_notification(
            session,
            alarm_id=alarm.id,
            channel="webhook",
            target_id=None,
            payload={"state": state},
            result="error",
            error=str(resolution.error),
        )
        record_event("webhook_delivery_error")
        raise resolution.error
    if isinstance(resolution, WebhookRejected):
        await _log_rejected_webhook(
            session,
            alarm=alarm,
            state=state,
            webhook_url=settings.webhook_url,
            error=str(resolution.error),
        )
        return None
    return resolution.addresses


def _state_webhook_payload(alarm: Alarm, state: str) -> dict[str, Any]:
    """Build a timestamped state-change webhook payload."""
    return {
        "event": EVENT_ALARM_STATE_CHANGED,
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
    http: httpx.AsyncClient | None,
    *,
    session: AsyncSession,
    alarm: Alarm,
    state: str,
    settings: Settings,
    payload_bytes: bytes,
    delivery_id: str,
    resolved_addresses: Sequence[str],
    webhook_client_pool: WebhookClientPool | None = None,
) -> None:
    """Send and audit a state callback, surfacing only retryable failures."""
    try:
        started_at = time.monotonic()
        try:
            await post_webhook_bytes_to_validated_addresses(
                settings.webhook_url,
                payload_bytes,
                _state_webhook_headers(settings, payload_bytes),
                resolved_addresses,
                delivery_id=delivery_id,
                timeout=settings.webhook_timeout_seconds,
                address_failed_event="webhook_delivery_address_failed",
                log_extra={"alarm_id": str(alarm.id), "state": state},
                client=http,
                client_pool=webhook_client_pool,
            )
        finally:
            observe_latency("provider_delivery", time.monotonic() - started_at)
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
            result="error" if is_retryable_delivery_error(exc) else "permanent_error",
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
    http: httpx.AsyncClient | None,
    webhook_client_pool: WebhookClientPool | None = None,
) -> None:
    """Send one durable state transition callback unless it already succeeded.

    Raises:
        RetryableSSRFError: DNS resolution failed transiently.
        NotificationDeliveryError: Delivery failed in a way that must be retried.
    """
    if await completed_notification(
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
        webhook_client_pool=webhook_client_pool,
    )
