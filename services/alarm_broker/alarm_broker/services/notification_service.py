"""Notification dispatch to Zammad, SMS, and Signal with audit logging."""

from __future__ import annotations

import logging
import uuid
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from alarm_broker import constants
from alarm_broker.connectors.sendxms import SendXmsClient
from alarm_broker.connectors.signal import SignalClient
from alarm_broker.connectors.zammad import ZammadClient
from alarm_broker.core.url_validation import (
    SSRFError,
    validate_url_not_internal,
    validate_webhook_host_allowed,
)
from alarm_broker.db.models import Alarm, AlarmNotification, EscalationStep, EscalationTarget
from alarm_broker.services.message_formatter import format_alarm_message
from alarm_broker.settings import Settings
from alarm_broker.types import EnrichedAlarmContext, NotificationPayload

logger = logging.getLogger("alarm_broker")


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
    """Log a notification attempt to the database.

    Args:
        session: Database session
        alarm_id: ID of the alarm
        channel: Notification channel (zammad, sms, signal)
        target_id: ID of the escalation target (if applicable)
        payload: Payload sent to the channel
        result: Result of the notification (ok, error)
        error: Error message if result is error
    """
    session.add(
        AlarmNotification(
            alarm_id=alarm_id,
            channel=channel,
            target_id=target_id,
            payload=payload,
            result=result,
            error=error,
        )
    )
    await session.commit()


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

        for target in targets:
            if not target.enabled:
                continue
            await self._send_to_channel(session, target, payload, settings)

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
            return f"NOTFALLALARM – {person} – {room}"
        return f"ESKALATION Stufe {step_no} – {person} – {room}"

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
    ) -> None:
        """Dispatch notification to the appropriate channel-specific method.

        Routes the notification based on the target's channel preference.
        Errors in one channel do not affect other channels.

        Args:
            session: Database session
            target: Target configuration with channel preference
            payload: Notification payload to send
        """
        try:
            if target.channel == "email":
                await self._send_email_notifications(session, target, payload)
            elif target.channel == "sms":
                await self._send_sms_notifications(session, target, payload)
            elif target.channel == "signal":
                await self._send_via_signal(session, target, payload["body"], payload)
            elif target.channel == "webhook":
                await self._send_webhook_notifications(session, target, payload, settings)
            else:
                logger.warning(
                    "unknown_channel",
                    extra={"channel": target.channel, "target_id": target.id},
                )
        except Exception as e:
            logger.exception(
                "channel_dispatch_failed",
                extra={"channel": target.channel, "target_id": target.id, "error": str(e)},
            )

    async def _send_email_notifications(
        self,
        session: AsyncSession,
        target: EscalationTarget,
        payload: NotificationPayload,
    ) -> None:
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
                session, target, payload, "error", "Zammad not enabled"
            )
            return

        try:
            await self._zammad.create_ticket(self._build_zammad_ticket_payload(payload))
            await self._log_notification_result(session, target, payload, "ok")
        except Exception as e:
            logger.exception(
                "email_notification_failed",
                extra={"target_id": target.id, "error": str(e)},
            )
            await self._log_notification_result(session, target, payload, "error", str(e))

    async def _send_sms_notifications(
        self,
        session: AsyncSession,
        target: EscalationTarget,
        payload: NotificationPayload,
    ) -> None:
        """Send SMS notification via SendXMS.

        Args:
            session: Database session
            target: Target with SMS configuration
            payload: Notification payload
        """
        await self._send_via_sendxms(session, target, payload["body"], payload)

    async def _send_via_signal(
        self,
        session: AsyncSession,
        target: EscalationTarget,
        message: str,
        payload: NotificationPayload,
    ) -> None:
        """Send message via Signal client.

        Args:
            session: Database session
            target: Target with Signal configuration
            message: Message to send
            payload: Full notification payload for logging
        """
        try:
            await self._signal.send_group_message(message, group_id=target.address)
            await self._log_notification_result(session, target, payload, "ok")
        except Exception as e:
            logger.exception(
                "signal_notification_failed",
                extra={"target_id": target.id, "error": str(e)},
            )
            await self._log_notification_result(session, target, payload, "error", str(e))

    async def _send_via_sendxms(
        self,
        session: AsyncSession,
        target: EscalationTarget,
        message: str,
        payload: NotificationPayload,
    ) -> None:
        """Send message via SendXMS client.

        Args:
            session: Database session
            target: Target with SendXMS configuration
            message: Message to send
            payload: Full notification payload for logging
        """
        try:
            await self._sendxms.send_sms(target.address, message)
            await self._log_notification_result(session, target, payload, "ok")
        except Exception as e:
            logger.exception(
                "sendxms_notification_failed",
                extra={"target_id": target.id, "error": str(e)},
            )
            await self._log_notification_result(session, target, payload, "error", str(e))

    async def _send_webhook_notifications(
        self,
        session: AsyncSession,
        target: EscalationTarget,
        payload: NotificationPayload,
        settings: Settings | None = None,
    ) -> None:
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
                session, target, payload, "error", "No webhook URL configured"
            )
            return

        try:
            allowed_hosts = settings.webhook_allowed_hosts if settings else ""
            validate_webhook_host_allowed(webhook_url, allowed_hosts)
            await validate_url_not_internal(webhook_url)
        except SSRFError as e:
            logger.warning(
                "webhook_ssrf_blocked",
                extra={"target_id": target.id, "url": webhook_url, "error": str(e)},
            )
            await self._log_notification_result(
                session, target, payload, "error", f"SSRF blocked: {e}"
            )
            return

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    webhook_url,
                    json=payload,
                    headers={"Content-Type": "application/json"},
                )
                response.raise_for_status()
            await self._log_notification_result(session, target, payload, "ok")
        except Exception as e:
            logger.exception(
                "webhook_notification_failed",
                extra={"target_id": target.id, "error": str(e)},
            )
            await self._log_notification_result(session, target, payload, "error", str(e))

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
            result: Result status ("ok" or "error")
            error: Error message if result is error
        """
        await log_notification(
            session,
            alarm_id=uuid.UUID(payload["alarm_id"]),
            channel=target.channel,
            target_id=str(target.id),
            payload=payload,
            result=result,
            error=error,
        )

    async def handle_zammad_ticket(
        self,
        session: AsyncSession,
        alarm: Alarm,
        enriched: EnrichedAlarmContext,
        ack_url: str | None,
        settings: Any,
    ) -> int | None:
        """Create a Zammad ticket for the alarm.

        Args:
            session: Database session
            alarm: Alarm instance
            enriched: Enriched alarm context
            ack_url: ACK URL for responders
            settings: Application settings (unused, kept for API compat)

        Returns:
            Ticket ID if created, None otherwise
        """
        if not self._zammad.enabled():
            return None

        base = self._build_notification_payload(alarm, enriched, step_no=0, ack_url=ack_url)

        try:
            ticket_id = await self._zammad.create_ticket(self._build_zammad_ticket_payload(base))
            await log_notification(
                session,
                alarm_id=alarm.id,
                channel="zammad",
                target_id=None,
                payload={"action": "create_ticket", "ticket_id": ticket_id},
                result="ok",
            )
            return ticket_id
        except Exception as e:
            logger.exception(
                "zammad_create_ticket_failed",
                extra={"alarm_id": str(alarm.id), "error": str(e)},
            )
            await log_notification(
                session,
                alarm_id=alarm.id,
                channel="zammad",
                target_id=None,
                payload={"action": "create_ticket"},
                result="error",
                error=str(e),
            )
            return None

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

        subject = "Alarm quittiert"
        body_parts = [
            f"ACK durch: {acked_by or '-'}",
            f"Zeit: {acked_at.isoformat()}",
        ]
        if note:
            body_parts.append(f"Notiz: {note}")
        body = "\n".join(body_parts)

        try:
            await self._zammad.add_internal_note(ticket_id, subject=subject, body=body)
            await log_notification(
                session,
                alarm_id=alarm_id,
                channel="zammad",
                target_id=None,
                payload={"action": "ack_update", "ticket_id": ticket_id},
                result="ok",
            )
            return True
        except Exception as e:
            logger.exception(
                "zammad_ack_note_failed",
                extra={"ticket_id": ticket_id, "error": str(e)},
            )
            return False

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
            )
        ).all()
        return [(row[0], row[1]) for row in rows]
