"""Notification dispatch to Zammad, SMS, and Signal with audit logging."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from escalane import constants
from escalane.connectors.sendxms import SendXmsClient
from escalane.connectors.signal import SignalClient
from escalane.connectors.zammad import ZammadClient
from escalane.core.errors import ConfigurationError
from escalane.core.metrics import record_event
from escalane.core.url_validation import (
    RetryableSSRFError,
    SSRFError,
    redact_url_for_logging,
    validate_url_not_internal,
    validate_webhook_host_allowed,
)
from escalane.db.models import Alarm, EscalationStep, EscalationTarget
from escalane.services import notification_delivery
from escalane.services.message_formatter import format_alarm_message
from escalane.services.webhook_delivery import (
    post_webhook_to_validated_addresses as _post_webhook_to_validated_addresses,
)
from escalane.settings import Settings
from escalane.types import EnrichedAlarmContext, NotificationPayload

logger = logging.getLogger("escalane")


class NotificationService:
    """Service for sending alarm notifications through various channels.

    This class encapsulates all notification logic, making it easier to
    test, extend, and maintain the notification flow.
    """

    def __init__(
        self,
        zammad: ZammadClient,
        sendxms: SendXmsClient,
        signal: SignalClient,
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

    async def send(
        self,
        session: AsyncSession,
        alarm: Alarm,
        enriched: EnrichedAlarmContext,
        *,
        step_no: int,
        ack_url: str | None,
        policy_id: str = "default",
        settings: Settings | None = None,
    ) -> None:
        """Build payload, fetch escalation targets, dispatch to each enabled channel."""
        payload = self._build_notification_payload(
            alarm=alarm,
            enriched=enriched,
            step_no=step_no,
            ack_url=ack_url,
        )

        targets = await self._get_escalation_targets(session, policy_id, step_no)

        failed_targets: list[str] = []
        for target in targets:
            if not target.enabled:
                continue
            if await notification_delivery.successful_notification(
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

        severity = enriched.get("severity", constants.PRIORITY_CRITICAL)
        priority = self._get_priority_for_severity(severity)
        title = self._build_title(enriched, step_no)
        tags = self._build_tags(step_no, severity)

        return {
            "title": title,
            "body": body,
            "tags": tags,
            "priority": priority,
            "step_no": step_no,
            "alarm_id": str(alarm.id),
        }

    def _get_priority_for_severity(self, severity: str) -> int:
        """Map severity to priority ID for external systems.

        Args:
            severity: Alarm severity (P0, P1, P2, P3)

        Returns:
            Priority ID for systems like Zammad
        """
        priority_map = {
            constants.PRIORITY_CRITICAL: 3,  # P0
            constants.PRIORITY_HIGH: 2,  # P1
            constants.PRIORITY_MEDIUM: 2,  # P2
            constants.PRIORITY_LOW: 1,  # P3
        }
        return priority_map.get(severity, 3)

    def _build_title(self, enriched: EnrichedAlarmContext, step_no: int) -> str:
        """Build notification title based on step and context.

        Args:
            enriched: Enriched alarm context
            step_no: Escalation step number

        Returns:
            Formatted title string
        """
        person = enriched.get("person_name", "Unknown")
        room = enriched.get("room_label", "Unknown")

        if step_no == 0:
            return f"NOTFALLALARM - {person} - {room}"
        return f"ESKALATION Stufe {step_no} - {person} - {room}"

    def _build_tags(self, step_no: int, severity: str) -> list[str]:
        """Build tags for notification based on step and severity.

        Args:
            step_no: Escalation step number
            severity: Alarm severity

        Returns:
            List of tag strings
        """
        tags = []
        if step_no == 0:
            tags.append(constants.TAG_EMERGENCY)
        if severity == constants.PRIORITY_CRITICAL:
            tags.append(constants.TAG_SILENT)
        return tags

    def _build_zammad_ticket_payload(self, payload: NotificationPayload) -> dict[str, Any]:
        """Build the Zammad ticket dict from a notification payload.

        Args:
            payload: Notification payload containing title, priority, tags, body.

        Returns:
            Zammad API ticket dict ready for `create_ticket`.
        """
        zcfg = self._zammad.config  # ZammadConfig (has group, state_id_new, customer)
        return {
            "title": payload["title"],
            "group": zcfg.group,
            "priority_id": payload["priority"],
            "state_id": zcfg.state_id_new,
            "customer_id": zcfg.customer,
            "tags": payload["tags"],
            "article": {
                "subject": "Alarm ausgelöst (silent)",
                "body": payload["body"],
                "type": "note",
                "internal": True,
            },
        }

    async def _get_escalation_targets(
        self,
        session: AsyncSession,
        policy_id: str,
        step_no: int,
    ) -> list[EscalationTarget]:
        """Fetch escalation targets for a given policy and step.

        Args:
            session: Database session
            policy_id: Escalation policy ID
            step_no: Escalation step number

        Returns:
            List of enabled EscalationTarget objects
        """
        steps = (
            await session.scalars(
                select(EscalationStep)
                .options(selectinload(EscalationStep.target))
                .where(EscalationStep.policy_id == policy_id)
                .where(EscalationStep.step_no == step_no)
            )
        ).all()
        return [step.target for step in steps if step.target is not None and step.target.enabled]

    async def _send_to_channel(
        self,
        session: AsyncSession,
        target: EscalationTarget,
        payload: NotificationPayload,
        settings: Settings | None = None,
    ) -> bool:
        """Dispatch notification to the appropriate channel-specific method.

        Routes the notification based on the target's channel preference.
        Errors in one channel do not affect other channels.

        Args:
            session: Database session
            target: Target configuration with channel preference
            payload: Notification payload to send
        """
        if target.channel == "email":
            return await self._send_email_notifications(session, target, payload)
        if target.channel == "sms":
            return await self._send_sms_notifications(session, target, payload)
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
        if not self._zammad.enabled():
            await self._log_notification_result(
                session, target, payload, "skipped", "Zammad not enabled"
            )
            return True

        try:
            await self._zammad.create_ticket(self._build_zammad_ticket_payload(payload))
        except Exception as e:
            safe_error = notification_delivery.safe_delivery_error(e)
            logger.error(
                "email_notification_failed",
                extra={"target_id": target.id, "error": safe_error},
            )
            return await self._record_channel_failure(session, target, payload, e, error=safe_error)
        await self._log_notification_result(session, target, payload, "ok")
        return True

    async def _send_sms_notifications(
        self,
        session: AsyncSession,
        target: EscalationTarget,
        payload: NotificationPayload,
    ) -> bool:
        """Send SMS notification via SendXMS.

        Args:
            session: Database session
            target: Target with SMS configuration
            payload: Notification payload
        """
        return await self._send_via_sendxms(session, target, payload["body"], payload)

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
        send: Callable[[], Awaitable[None]],
    ) -> bool:
        """Send through an enabled channel and retain one consistent audit outcome."""
        if not enabled:
            await self._log_notification_result(
                session, target, payload, "skipped", disabled_reason
            )
            return True
        try:
            await send()
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
        settings: Settings | None = None,
    ) -> bool:
        """Send webhook notification via HTTP POST.

        Sends the notification payload to a configured webhook URL.

        Args:
            session: Database session
            target: Target with webhook configuration
            payload: Notification payload to send
        """
        webhook_url = target.address
        if not webhook_url:
            await self._log_notification_result(
                session, target, payload, "skipped", "No webhook URL configured"
            )
            return True

        resolved_addresses = await self._resolve_webhook_addresses(
            session, target, payload, webhook_url, settings
        )
        if resolved_addresses is None:
            return True
        if not resolved_addresses:
            return False

        delivery_id = notification_delivery.notification_delivery_id(
            alarm_id=uuid.UUID(payload["alarm_id"]),
            channel=target.channel,
            target_id=str(target.id),
            payload=payload,
        )
        try:
            await _post_webhook_to_validated_addresses(
                webhook_url, payload, resolved_addresses, target.id, delivery_id
            )
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
        settings: Settings | None,
    ) -> tuple[str, ...] | list[str] | None:
        """Resolve safe addresses, distinguishing permanent blocks from retryable DNS.

        ``None`` means a permanent SSRF/configuration rejection was recorded and
        delivery should stop. An empty sequence means DNS failed transiently and
        the worker should retry. A populated sequence is pinned for this attempt.
        """
        try:
            allowed_hosts = settings.webhook_allowed_hosts if settings else ""
            validate_webhook_host_allowed(webhook_url, allowed_hosts)
            return await validate_url_not_internal(webhook_url)
        except RetryableSSRFError as e:
            logger.warning(
                "webhook_dns_unavailable",
                extra={
                    "target_id": target.id,
                    "url": redact_url_for_logging(webhook_url),
                    "error": str(e),
                },
            )
            await self._log_notification_result(
                session, target, payload, "error", f"DNS resolution failed: {e}"
            )
            return ()
        except SSRFError as e:
            logger.warning(
                "webhook_ssrf_blocked",
                extra={
                    "target_id": target.id,
                    "url": redact_url_for_logging(webhook_url),
                    "error": str(e),
                },
            )
            await self._log_notification_result(
                session, target, payload, "skipped", f"SSRF blocked: {e}"
            )
            return None

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
        await self._log_notification_result(session, target, payload, "error", safe_error)
        return not notification_delivery.is_retryable_delivery_error(exception)

    async def handle_zammad_ticket(
        self,
        session: AsyncSession,
        alarm: Alarm,
        enriched: EnrichedAlarmContext,
        ack_url: str | None,
    ) -> int | None:
        """Create a Zammad ticket for the alarm.

        Args:
            session: Database session
            alarm: Alarm instance
            enriched: Enriched alarm context
            ack_url: ACK URL for responders

        Returns:
            Ticket ID if created, None otherwise
        """
        if not self._zammad.enabled():
            return None

        base = self._build_notification_payload(alarm, enriched, step_no=0, ack_url=ack_url)

        try:
            ticket_id = await self._zammad.create_ticket(self._build_zammad_ticket_payload(base))
        except Exception as e:
            safe_error = notification_delivery.safe_delivery_error(e)
            logger.error(
                "zammad_create_ticket_failed",
                extra={"alarm_id": str(alarm.id), "error": safe_error},
            )
            await notification_delivery.log_notification(
                session,
                alarm_id=alarm.id,
                channel="zammad",
                target_id=None,
                payload={"action": "create_ticket"},
                result="error",
                error=safe_error,
            )
            if notification_delivery.is_retryable_delivery_error(e):
                raise notification_delivery.NotificationDeliveryError(
                    "Zammad ticket creation failed"
                ) from e
            return None
        await notification_delivery.log_notification(
            session,
            alarm_id=alarm.id,
            channel="zammad",
            target_id=None,
            payload={"action": "create_ticket", "ticket_id": ticket_id},
            result="ok",
        )
        return ticket_id

    async def add_zammad_ack_note(
        self,
        session: AsyncSession,
        alarm_id: uuid.UUID,
        ticket_id: int,
        acked_by: str | None,
        acked_at: Any,
        note: str | None,
    ) -> bool:
        """Add an acknowledgment note to a Zammad ticket.

        Args:
            session: Database session
            alarm_id: Alarm ID for audit logging
            ticket_id: Zammad ticket ID
            acked_by: Person who acknowledged
            acked_at: Timestamp of acknowledgment
            note: Optional note from acknowledger

        Returns:
            True if successful, False otherwise
        """
        if not self._zammad.enabled():
            return False

        if await notification_delivery.successful_notification(
            session,
            alarm_id=alarm_id,
            channel="zammad",
            target_id=None,
            payload_matches={"action": "ack_update", "ticket_id": ticket_id},
        ):
            return True

        subject, body = notification_delivery.zammad_ack_note(acked_by, acked_at, note)

        try:
            await self._zammad.add_internal_note(ticket_id, subject=subject, body=body)
        except Exception as e:
            retryable = notification_delivery.is_retryable_delivery_error(e)
            safe_error = notification_delivery.safe_delivery_error(e)
            logger.error(
                "zammad_ack_note_failed",
                extra={"ticket_id": ticket_id, "error": safe_error},
            )
            await notification_delivery.log_notification(
                session,
                alarm_id=alarm_id,
                channel="zammad",
                target_id=None,
                payload={"action": "ack_update", "ticket_id": ticket_id},
                result="error" if retryable else "permanent_error",
                error=safe_error,
            )
            return not retryable
        await notification_delivery.log_notification(
            session,
            alarm_id=alarm_id,
            channel="zammad",
            target_id=None,
            payload={"action": "ack_update", "ticket_id": ticket_id},
            result="ok",
        )
        return True

    async def get_escalation_schedule(
        self,
        session: AsyncSession,
        policy_id: str = "default",
    ) -> list[tuple[int, int]]:
        """Get escalation steps that need to be scheduled.

        Args:
            session: Database session
            policy_id: Escalation policy ID

        Returns:
            List of (step_no, after_seconds) tuples
        """
        rows = (
            await session.execute(
                select(EscalationStep.step_no, EscalationStep.after_seconds)
                .where(EscalationStep.policy_id == policy_id)
                .where(EscalationStep.step_no > 0)
                .distinct()
                .order_by(EscalationStep.step_no, EscalationStep.after_seconds)
            )
        ).all()
        schedule: dict[int, int] = {}
        for step_no, after_seconds in rows:
            previous_delay = schedule.setdefault(step_no, after_seconds)
            if previous_delay != after_seconds:
                raise ConfigurationError(
                    f"Escalation policy {policy_id!r} has conflicting delays for step {step_no}"
                )
        return list(schedule.items())
