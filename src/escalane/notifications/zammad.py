"""Zammad workflows with durable notification-audit outcomes."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from escalane.notifications import delivery as notification_delivery
from escalane.persistence.models import Alarm
from escalane.providers.base import ZammadTicketProvider

logger = logging.getLogger("escalane")


async def create_ticket(
    session: AsyncSession,
    alarm: Alarm,
    zammad: ZammadTicketProvider,
    ticket_payload: dict[str, Any],
) -> int | None:
    """Create a ticket, retaining the existing retry and audit contract."""
    try:
        ticket_id = await zammad.create_ticket(ticket_payload)
    except Exception as error:
        safe_error = notification_delivery.safe_delivery_error(error)
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
        if notification_delivery.is_retryable_delivery_error(error):
            raise notification_delivery.NotificationDeliveryError(
                "Zammad ticket creation failed"
            ) from error
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


async def add_ack_note(
    session: AsyncSession,
    alarm_id: uuid.UUID,
    ticket_id: int,
    acked_by: str | None,
    acked_at: Any,
    note: str | None,
    zammad: ZammadTicketProvider,
) -> bool:
    """Add an acknowledgment note, retaining the existing retry contract."""
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
        await zammad.add_internal_note(ticket_id, subject=subject, body=body)
    except Exception as error:
        retryable = notification_delivery.is_retryable_delivery_error(error)
        safe_error = notification_delivery.safe_delivery_error(error)
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
