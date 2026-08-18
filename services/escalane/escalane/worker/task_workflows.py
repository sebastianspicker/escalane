"""Small delivery workflows used by ARQ task entry points."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from escalane import constants
from escalane.core.metrics import record_event
from escalane.core.url_validation import (
    RetryableSSRFError,
    SSRFError,
    pin_url_to_address,
    redact_url_for_logging,
    validate_url_not_internal,
    validate_webhook_host_allowed,
)
from escalane.db.models import Alarm
from escalane.services.notification_delivery import (
    NotificationAuditError,
    NotificationDeliveryError,
    is_retryable_delivery_error,
    notification_delivery_id,
    safe_delivery_error,
)
from escalane.settings import Settings


@dataclass(frozen=True)
class StateWebhookOperations:
    """Replaceable operations for one state-webhook delivery workflow."""

    successful: Callable[..., Awaitable[Any]]
    url_allowed: Callable[..., Awaitable[tuple[str, ...] | None]]
    build_payload: Callable[[Alarm, str], dict[str, Any]]
    headers_for_payload: Callable[[Settings, bytes], dict[str, str]]
    send: Callable[..., Awaitable[None]]
    log: logging.Logger


async def load_active_alarm(
    session: AsyncSession,
    alarm_id: uuid.UUID,
    *,
    log: logging.Logger,
    log_extra: dict[str, Any],
) -> Alarm | None:
    """Load an alarm unless it was removed, logging either no-op condition."""
    alarm = await session.get(Alarm, alarm_id)
    if not alarm:
        log.warning("alarm_not_found", extra=log_extra)
        return None
    if alarm.deleted_at is not None:
        log.info("alarm_deleted", extra=log_extra)
        return None
    return alarm


def ack_url_for_alarm(
    alarm: Alarm,
    settings: Settings,
    *,
    alarm_id: str,
    log: logging.Logger,
    log_extra: dict[str, Any] | None = None,
) -> str | None:
    """Build an ACK URL, retaining delivery when a historical token is absent."""
    if alarm.ack_token:
        return f"{settings.base_url}/a/{alarm.ack_token}"
    log.warning("alarm_missing_ack_token", extra=log_extra or {"alarm_id": alarm_id})
    return None


def ack_note_delivery_error(
    success: bool,
    *,
    alarm_id: str,
    ticket_id: int | None,
    log: logging.Logger,
) -> NotificationDeliveryError | None:
    """Log an ACK-note outcome and return a retryable failure when needed."""
    extra = {"alarm_id": alarm_id, "ticket_id": ticket_id}
    if success:
        log.info("ack_note_added", extra=extra)
        return None
    log.warning("ack_note_failed", extra=extra)
    return NotificationDeliveryError("Zammad acknowledgment delivery failed")


async def enqueue_escalations(
    redis: Any,
    schedule: Sequence[tuple[int, int]],
    *,
    alarm_id: str,
    log: logging.Logger,
) -> None:
    """Queue deterministic escalation jobs before external notification effects."""
    for step_no, after_seconds in schedule:
        try:
            await redis.enqueue_job(
                "escalate",
                alarm_id,
                step_no,
                _defer_by=int(after_seconds),
                _job_id=f"escalate:{alarm_id}:{step_no}",
            )
        except Exception as exc:
            raise NotificationDeliveryError("Escalation scheduling failed") from exc
        log.info(
            "escalation_scheduled",
            extra={"alarm_id": alarm_id, "step_no": step_no, "after_seconds": after_seconds},
        )


async def restore_zammad_ticket_id(
    session: AsyncSession,
    alarm: Alarm,
    successful: Callable[..., Awaitable[Any]],
) -> None:
    """Restore a ticket ID after a crash between audit persistence and alarm commit."""
    if alarm.zammad_ticket_id is not None:
        return
    prior_ticket = await successful(
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
    """Attempt ticket creation and stage zero fan-out without masking either failure."""
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


async def deliver_state_webhook(
    session: AsyncSession,
    alarm: Alarm,
    *,
    alarm_id: str,
    state: str,
    settings: Settings,
    http: Any,
    operations: StateWebhookOperations,
) -> None:
    """Send one durable state transition callback unless it already succeeded."""
    if await operations.successful(
        session,
        alarm_id=alarm.id,
        channel="webhook",
        target_id=None,
        payload_matches={"state": state},
    ):
        operations.log.info(
            "webhook_delivery_already_complete", extra={"alarm_id": alarm_id, "state": state}
        )
        return
    resolved_addresses = await operations.url_allowed(
        session,
        alarm=alarm,
        state=state,
        settings=settings,
    )
    if resolved_addresses is None:
        return
    payload_bytes = json.dumps(
        operations.build_payload(alarm, state), separators=(",", ":")
    ).encode()
    delivery_id = notification_delivery_id(
        alarm_id=alarm.id,
        channel="webhook",
        target_id=None,
        payload={"state": state},
    )
    headers = operations.headers_for_payload(settings, payload_bytes)
    headers["X-Alarm-Delivery-ID"] = delivery_id
    await operations.send(
        http=http,
        webhook_url=settings.webhook_url,
        payload_bytes=payload_bytes,
        headers=headers,
        timeout=settings.webhook_timeout_seconds,
        delivery=WebhookDelivery(alarm_id=alarm.id, session=session, state=state),
        resolved_addresses=resolved_addresses,
    )


@dataclass(frozen=True)
class WebhookDelivery:
    """Inputs retained for webhook audit logging after retries."""

    alarm_id: uuid.UUID
    session: AsyncSession
    state: str


@dataclass(frozen=True)
class _PinnedWebhookRequest:
    """Transport inputs shared by each approved address attempt."""

    http: Any
    webhook_url: str
    payload_bytes: bytes
    headers: dict[str, str]
    timeout: float
    delivery: WebhookDelivery


async def log_rejected_webhook(
    session: AsyncSession,
    *,
    alarm: Alarm,
    state: str,
    webhook_url: str,
    error: str,
    log_notification: Callable[..., Awaitable[None]],
) -> None:
    """Audit a permanent SSRF rejection without scheduling an unsafe retry."""
    alarm_id = str(alarm.id)
    log = logging.getLogger("escalane")
    log.warning(
        "webhook_url_rejected",
        extra={
            "alarm_id": alarm_id,
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


async def webhook_url_allowed(
    session: AsyncSession,
    *,
    alarm: Alarm,
    state: str,
    settings: Settings,
    log_notification: Callable[..., Awaitable[None]],
    validate_url: Callable[[str], Awaitable[Sequence[str] | None]] = validate_url_not_internal,
) -> tuple[str, ...] | None:
    """Return pinned addresses or durably record a permanent URL rejection."""
    try:
        validate_webhook_host_allowed(settings.webhook_url, settings.webhook_allowed_hosts)
        addresses = await validate_url(settings.webhook_url)
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
        await log_rejected_webhook(
            session,
            alarm=alarm,
            state=state,
            webhook_url=settings.webhook_url,
            error=str(exc),
            log_notification=log_notification,
        )
        return None
    return tuple(addresses or ())


def webhook_headers(settings: Settings, payload_bytes: bytes) -> dict[str, str]:
    """Produce HMAC headers for a state webhook payload."""
    headers: dict[str, str] = {"Content-Type": "application/json"}
    if settings.webhook_secret:
        signature = hmac.new(
            settings.webhook_secret.encode(), payload_bytes, hashlib.sha256
        ).hexdigest()
        headers["X-Hub-Signature-256"] = f"sha256={signature}"
    return headers


def build_webhook_payload(alarm: Alarm, state: str) -> dict[str, Any]:
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


async def post_webhook(
    http: Any,
    url: str,
    payload_bytes: bytes,
    headers: dict[str, str],
    timeout: float,
    extensions: dict[str, Any] | None = None,
) -> None:
    """POST a webhook once; the ARQ task owns retry policy."""
    response = await http.post(
        url,
        content=payload_bytes,
        headers=headers,
        timeout=float(timeout),
        extensions=extensions or {},
    )
    response.raise_for_status()


def webhook_request_details(
    webhook_url: str, headers: dict[str, str], resolved_address: str
) -> tuple[str, dict[str, str], dict[str, Any]]:
    """Pin a request to an approved address while retaining Host and SNI."""
    request_headers = dict(headers)
    request_url, host_header, sni_hostname = pin_url_to_address(webhook_url, resolved_address)
    request_headers["Host"] = host_header
    return request_url, request_headers, {"sni_hostname": sni_hostname}


async def _post_to_validated_address(
    request: _PinnedWebhookRequest,
    address: str,
    post: Callable[..., Awaitable[None]],
) -> Exception | None:
    """Try one pinned address, returning only a retryable transport failure."""
    request_url, request_headers, extensions = webhook_request_details(
        request.webhook_url, request.headers, address
    )
    try:
        await post(
            request.http,
            request_url,
            request.payload_bytes,
            request_headers,
            request.timeout,
            extensions,
        )
    except Exception as exc:
        if not is_retryable_delivery_error(exc):
            raise
        logging.getLogger("escalane").warning(
            "webhook_delivery_address_failed",
            extra={
                "alarm_id": str(request.delivery.alarm_id),
                "state": request.delivery.state,
                "url": redact_url_for_logging(request.webhook_url),
                "error": safe_delivery_error(exc),
            },
        )
        return exc
    return None


async def post_to_validated_addresses(
    http: Any,
    webhook_url: str,
    payload_bytes: bytes,
    headers: dict[str, str],
    timeout: float,
    delivery: WebhookDelivery,
    resolved_addresses: tuple[str, ...],
    *,
    post: Callable[..., Awaitable[None]] = post_webhook,
) -> None:
    """Try each prevalidated pinned address within a single timeout budget."""
    last_error: Exception = SSRFError("Webhook URL has no validated global addresses")
    request = _PinnedWebhookRequest(
        http=http,
        webhook_url=webhook_url,
        payload_bytes=payload_bytes,
        headers=headers,
        timeout=timeout,
        delivery=delivery,
    )
    async with asyncio.timeout(float(timeout)):
        for address in resolved_addresses:
            failure = await _post_to_validated_address(request, address, post)
            if failure is None:
                return
            last_error = failure
    raise last_error


async def send_state_webhook(
    http: Any,
    webhook_url: str,
    payload_bytes: bytes,
    headers: dict[str, str],
    timeout: float,
    delivery: WebhookDelivery,
    resolved_addresses: tuple[str, ...] = (),
    *,
    post_to_addresses: Callable[..., Awaitable[None]] = post_to_validated_addresses,
    log_notification: Callable[..., Awaitable[None]],
) -> None:
    """Send and audit a state callback, surfacing only retryable failures."""
    try:
        await post_to_addresses(
            http, webhook_url, payload_bytes, headers, timeout, delivery, resolved_addresses
        )
    except Exception as exc:
        safe_error = safe_delivery_error(exc)
        logging.getLogger("escalane").error(
            "webhook_delivery_failed",
            extra={
                "alarm_id": str(delivery.alarm_id),
                "state": delivery.state,
                "error": safe_error,
            },
        )
        await log_notification(
            delivery.session,
            alarm_id=delivery.alarm_id,
            channel="webhook",
            target_id=None,
            payload={"state": delivery.state},
            result="error",
            error=safe_error,
        )
        record_event("webhook_delivery_error")
        if is_retryable_delivery_error(exc):
            raise NotificationDeliveryError("State webhook delivery failed") from exc
        return
    await log_notification(
        delivery.session,
        alarm_id=delivery.alarm_id,
        channel="webhook",
        target_id=None,
        payload={"state": delivery.state},
        result="ok",
    )
    record_event("webhook_delivery_ok")
