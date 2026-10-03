"""Notification dispatch to Zammad, SMS, and Signal with audit logging."""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from escalane.alarms.enrichment import EnrichedAlarmContext
from escalane.alarms.severity import PRIORITY_CRITICAL
from escalane.config.settings import Settings
from escalane.notifications import delivery as notification_delivery
from escalane.notifications import (
    payloads as notification_payloads,
)
from escalane.notifications import (
    targets as notification_targets,
)
from escalane.notifications import (
    zammad as notification_zammad,
)
from escalane.notifications.formatting import format_alarm_message
from escalane.notifications.payloads import NotificationPayload
from escalane.notifications.webhooks import (
    WebhookAddresses,
    WebhookDnsFailure,
    resolve_webhook_addresses,
)
from escalane.persistence.models import Alarm, EscalationTarget
from escalane.providers.base import SignalGroupProvider, SmsProvider, ZammadTicketProvider
from escalane.providers.webhook import WebhookClientPool, post_webhook_bytes_to_validated_addresses
from escalane.security.url_validation import redact_url_for_logging
from escalane.telemetry.metrics import observe_latency, record_event

logger = logging.getLogger("escalane")


def _target_webhook_body(payload: NotificationPayload) -> bytes:
    """Serialize a target webhook body exactly as httpx's ``json=`` encoder does."""
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode(
        "utf-8"
    )


class NotificationService:
    """Service for sending alarm notifications through various channels.

    This class encapsulates all notification logic, making it easier to
    test, extend, and maintain the notification flow.
    """

    def __init__(
        self,
        zammad: ZammadTicketProvider,
        sendxms: SmsProvider,
        signal: SignalGroupProvider,
        webhook_pool: WebhookClientPool | None = None,
    ) -> None:
        """Initialize the notification service.

        Args:
            zammad: Zammad client for ticket management
            sendxms: SMS client for text messages
            signal: Signal client for group messages
        """
        self._zammad = zammad
        self._sendxms = sendxms
        self._signal = signal
        self._webhook_pool = webhook_pool

    async def send(
        self,
        session: AsyncSession,
        alarm: Alarm,
        enriched: EnrichedAlarmContext,
        *,
        step_no: int,
        ack_url: str | None,
        settings: Settings,
        policy_id: str = "default",
    ) -> None:
        """Build payload, fetch escalation targets, dispatch to each enabled channel."""
        payload = self._build_notification_payload(
            alarm=alarm,
            enriched=enriched,
            step_no=step_no,
            ack_url=ack_url,
        )

        targets = await notification_targets.get_escalation_targets(session, policy_id, step_no)

        failed_targets: list[str] = []
        for target in targets:
            if not target.enabled:
                continue
            if await notification_delivery.completed_notification(
                session,
                alarm_id=alarm.id,
                channel=target.channel,
                target_id=str(target.id),
                payload_matches={"step_no": step_no},
            ):
                logger.info(
                    "notification_delivery_already_complete",
                    extra={
                        "alarm_id": str(alarm.id),
                        "target_id": str(target.id),
                        "step_no": step_no,
                    },
                )
                continue
            delivered = await self._send_to_channel(session, target, payload, settings)
            if not delivered:
                failed_targets.append(str(target.id))

        if failed_targets:
            record_event("notification_delivery_error")
            raise notification_delivery.NotificationDeliveryError(
                "Notification delivery failed for targets: " + ", ".join(failed_targets)
            )

    def _build_notification_payload(
        self,
        alarm: Alarm,
        enriched: EnrichedAlarmContext,
        step_no: int,
        ack_url: str | None,
    ) -> NotificationPayload:
        """Build the notification payload with message content and metadata.

        Creates the message title, body, tags, and applies severity-based
        prioritization for the notification.

        Args:
            alarm: Alarm instance
            enriched: Enriched alarm context
            step_no: Escalation step number
            ack_url: ACK URL for responders

        Returns:
            Dictionary containing title, body, tags, and priority
        """
        body = format_alarm_message(
            alarm_id=str(alarm.id),
            person=str(enriched["person_name"]),
            room=str(enriched["room_label"]),
            site=str(enriched.get("site_name")) if enriched.get("site_name") else None,
            created_at=alarm.created_at,
            ack_url=ack_url,
            step_no=step_no,
        )

        severity = enriched.get("severity", PRIORITY_CRITICAL)
        priority = notification_payloads.priority_for_severity(severity)
        title = notification_payloads.build_title(enriched, step_no)
        tags = notification_payloads.build_tags(step_no, severity)

        return {
            "title": title,
            "body": body,
            "tags": tags,
            "priority": priority,
            "step_no": step_no,
            "alarm_id": str(alarm.id),
        }

    async def _send_to_channel(
        self,
        session: AsyncSession,
        target: EscalationTarget,
        payload: NotificationPayload,
        settings: Settings,
    ) -> bool:
        """Dispatch notification to the appropriate channel-specific method.

        Routes the notification based on the target's channel preference.
        Errors in one channel do not affect other channels.

        Args:
            session: Database session
            target: Target configuration with channel preference
            payload: Notification payload to send
            settings: Runtime settings used by webhook delivery
        """
        if target.channel == "email":
            return await self._send_email_notifications(session, target, payload)
        if target.channel == "sms":
            return await self._send_via_sendxms(session, target, payload["body"], payload)
        if target.channel == "signal":
            return await self._send_via_signal(session, target, payload["body"], payload)
        if target.channel == "webhook":
            return await self._send_webhook_notifications(session, target, payload, settings)

        logger.warning(
            "unknown_channel",
            extra={"channel": target.channel, "target_id": target.id},
        )
        await self._log_notification_result(
            session, target, payload, "skipped", "Unknown notification channel"
        )
        return True

    async def _send_email_notifications(
        self,
        session: AsyncSession,
        target: EscalationTarget,
        payload: NotificationPayload,
    ) -> bool:
        """Send email notification via Zammad.

        Formats the email payload and creates a ticket in Zammad.
        Failure does not affect other channels.

        Args:
            session: Database session
            target: Target with email configuration
            payload: Notification payload
        """
        return await self._send_with_client(
            session,
            target,
            payload,
            enabled=self._zammad.enabled(),
            disabled_reason="Zammad not enabled",
            failure_event="email_notification_failed",
            send=lambda: self._zammad.create_ticket(
                notification_payloads.build_zammad_ticket_payload(payload, self._zammad.config)
            ),
        )

    async def _send_via_signal(
        self,
        session: AsyncSession,
        target: EscalationTarget,
        message: str,
        payload: NotificationPayload,
    ) -> bool:
        """Send message via Signal client.

        Args:
            session: Database session
            target: Target with Signal configuration
            message: Message to send
            payload: Full notification payload for logging
        """
        return await self._send_with_client(
            session,
            target,
            payload,
            enabled=self._signal.enabled(),
            disabled_reason="Signal not enabled",
            failure_event="signal_notification_failed",
            send=lambda: self._signal.send_group_message(message, group_id=target.address),
        )

    async def _send_via_sendxms(
        self,
        session: AsyncSession,
        target: EscalationTarget,
        message: str,
        payload: NotificationPayload,
    ) -> bool:
        """Send message via SendXMS client.

        Args:
            session: Database session
            target: Target with SendXMS configuration
            message: Message to send
            payload: Full notification payload for logging
        """
        return await self._send_with_client(
            session,
            target,
            payload,
            enabled=self._sendxms.enabled(),
            disabled_reason="SendXMS not enabled",
            failure_event="sendxms_notification_failed",
            send=lambda: self._sendxms.send_sms(target.address, message),
        )

    async def _send_with_client(
        self,
        session: AsyncSession,
        target: EscalationTarget,
        payload: NotificationPayload,
        *,
        enabled: bool,
        disabled_reason: str,
        failure_event: str,
        send: Callable[[], Awaitable[Any]],
    ) -> bool:
        """Send through an enabled channel and retain one consistent audit outcome."""
        if not enabled:
            await self._log_notification_result(
                session, target, payload, "skipped", disabled_reason
            )
            return True
        try:
            started_at = time.monotonic()
            try:
                await send()
            finally:
                observe_latency("provider_delivery", time.monotonic() - started_at)
        except Exception as exc:
            safe_error = notification_delivery.safe_delivery_error(exc)
            logger.error(failure_event, extra={"target_id": target.id, "error": safe_error})
            return await self._record_channel_failure(
                session, target, payload, exc, error=safe_error
            )
        await self._log_notification_result(session, target, payload, "ok")
        return True

    async def _send_webhook_notifications(
        self,
        session: AsyncSession,
        target: EscalationTarget,
        payload: NotificationPayload,
        settings: Settings,
    ) -> bool:
        """Send webhook notification via HTTP POST.

        Sends the notification payload to a configured webhook URL.

        Args:
            session: Database session
            target: Target with webhook configuration
            payload: Notification payload to send
            settings: Webhook allowlist, scheme policy, and timeout
        """
        webhook_url = target.address
        if not webhook_url:
            await self._log_notification_result(
                session, target, payload, "skipped", "No webhook URL configured"
            )
            return True

        resolved = await self._resolve_webhook_addresses(
            session, target, payload, webhook_url, settings
        )
        if isinstance(resolved, bool):
            return resolved

        delivery_id = notification_delivery.notification_delivery_id(
            alarm_id=uuid.UUID(payload["alarm_id"]),
            channel=target.channel,
            target_id=str(target.id),
            payload=payload,
        )
        try:
            started_at = time.monotonic()
            try:
                await post_webhook_bytes_to_validated_addresses(
                    webhook_url,
                    _target_webhook_body(payload),
                    {"Content-Type": "application/json"},
                    resolved,
                    delivery_id=delivery_id,
                    timeout=settings.webhook_timeout_seconds,
                    address_failed_event="webhook_notification_address_failed",
                    log_extra={"target_id": target.id},
                    log_permanent_failures=True,
                    client_pool=self._webhook_pool,
                )
            finally:
                observe_latency("provider_delivery", time.monotonic() - started_at)
        except Exception as e:
            safe_error = notification_delivery.safe_delivery_error(e)
            logger.error(
                "webhook_notification_failed",
                extra={"target_id": target.id, "error": safe_error},
            )
            return await self._record_channel_failure(session, target, payload, e, error=safe_error)
        await self._log_notification_result(session, target, payload, "ok")
        return True

    async def _resolve_webhook_addresses(
        self,
        session: AsyncSession,
        target: EscalationTarget,
        payload: NotificationPayload,
        webhook_url: str,
        settings: Settings,
    ) -> tuple[str, ...] | bool:
        """Resolve safe addresses, distinguishing permanent blocks from retryable DNS.

        A tuple is pinned for this attempt. Otherwise the outcome was audited
        and the returned flag is the channel result: ``True`` for a permanent
        SSRF/configuration rejection, ``False`` for a DNS failure to retry.
        """
        resolution = await resolve_webhook_addresses(webhook_url, settings)
        if isinstance(resolution, WebhookAddresses):
            return resolution.addresses
        extra = {
            "target_id": target.id,
            "url": redact_url_for_logging(webhook_url),
            "error": str(resolution.error),
        }
        if isinstance(resolution, WebhookDnsFailure):
            logger.warning("webhook_dns_unavailable", extra=extra)
            await self._log_notification_result(
                session, target, payload, "error", f"DNS resolution failed: {resolution.error}"
            )
            return False
        logger.warning("webhook_ssrf_blocked", extra=extra)
        await self._log_notification_result(
            session, target, payload, "skipped", f"SSRF blocked: {resolution.error}"
        )
        return True

    async def _log_notification_result(
        self,
        session: AsyncSession,
        target: EscalationTarget,
        payload: NotificationPayload,
        result: str,
        error: str | None = None,
    ) -> None:
        """Log notification result to database and track metrics.

        Records successful and failed notification attempts for auditing
        and metrics purposes.

        Args:
            session: Database session
            target: Target that was notified
            payload: Payload that was sent
            result: Result status ("ok", "error", or "skipped")
            error: Detail for an error or skipped result
        """
        await notification_delivery.log_notification(
            session,
            alarm_id=uuid.UUID(payload["alarm_id"]),
            channel=target.channel,
            target_id=str(target.id),
            payload=payload,
            result=result,
            error=error,
        )

    async def _record_channel_failure(
        self,
        session: AsyncSession,
        target: EscalationTarget,
        payload: NotificationPayload,
        exception: Exception,
        *,
        error: str | None = None,
    ) -> bool:
        """Audit a failed channel attempt and decide whether ARQ must retry it.

        ``True`` means the delivery is complete from the worker's perspective:
        a permanent provider/configuration failure was recorded and later
        targets should continue. ``False`` preserves the existing worker retry
        contract for ambiguous/transient failures.
        """
        safe_error = error or notification_delivery.safe_delivery_error(exception)
        retryable = notification_delivery.is_retryable_delivery_error(exception)
        result = "error" if retryable else "permanent_error"
        await self._log_notification_result(session, target, payload, result, safe_error)
        return not retryable

    async def handle_zammad_ticket(
        self,
        session: AsyncSession,
        alarm: Alarm,
        enriched: EnrichedAlarmContext,
        ack_url: str | None,
    ) -> int | None:
        """Create a Zammad ticket for the alarm."""
        if not self._zammad.enabled():
            return None
        payload = self._build_notification_payload(alarm, enriched, step_no=0, ack_url=ack_url)
        return await notification_zammad.create_ticket(
            session,
            alarm,
            self._zammad,
            notification_payloads.build_zammad_ticket_payload(payload, self._zammad.config),
        )

    def zammad_enabled(self) -> bool:
        """Return whether ticket notes can be delivered to Zammad."""
        return self._zammad.enabled()

    async def add_zammad_ack_note(
        self,
        session: AsyncSession,
        alarm_id: uuid.UUID,
        ticket_id: int,
        acked_by: str | None,
        acked_at: Any,
        note: str | None,
    ) -> bool:
        """Add an acknowledgment note to a Zammad ticket."""
        if not self._zammad.enabled():
            return False
        return await notification_zammad.add_ack_note(
            session, alarm_id, ticket_id, acked_by, acked_at, note, self._zammad
        )
