"""arq worker tasks: alarm fan-out, escalation, ACK handling, webhooks."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any, TypedDict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from tenacity import retry, stop_after_attempt, wait_exponential

from alarm_broker import constants
from alarm_broker.connectors.sendxms import SendXmsClient
from alarm_broker.connectors.signal import SignalClient
from alarm_broker.connectors.zammad import ZammadClient
from alarm_broker.core.metrics import record_event
from alarm_broker.core.url_validation import SSRFError, validate_url_not_internal
from alarm_broker.db.models import Alarm, AlarmStatus
from alarm_broker.services.enrichment_service import enrich_alarm_context
from alarm_broker.services.notification_service import NotificationService, log_notification
from alarm_broker.services.trigger_service import TriggerService
from alarm_broker.settings import Settings

logger = logging.getLogger("alarm_broker")


class WorkerContext(TypedDict, total=False):
    """Typed dictionary for the arq worker context."""

    sessionmaker: async_sessionmaker[AsyncSession]
    settings: Settings
    redis: Any  # arq.connections.ArqRedis
    http: Any  # httpx.AsyncClient
    zammad: ZammadClient
    sendxms: SendXmsClient
    signal: SignalClient


async def process_alarm_event(ctx: dict, payload: dict[str, Any]) -> None:
    """Process a generic alarm event from EventPublisher.

    This is a unified entry point for all alarm events. It dispatches
    to the appropriate existing tasks based on the event type.

    Args:
        ctx: Worker context dictionary
        payload: Event payload containing event_type and event data
    """
    event_type = payload.get("event_type")
    alarm_id = payload.get("alarm_id")

    if not event_type or not alarm_id:
        logger.warning("process_alarm_event_invalid_payload", extra={"payload": payload})
        return

    # Dispatch to appropriate handler based on event type
    if event_type == constants.EVENT_ALARM_CREATED:
        await alarm_created(ctx, str(alarm_id))
    elif event_type == constants.EVENT_ALARM_ACKNOWLEDGED:
        acked_by = payload.get("acknowledged_by")
        note = payload.get("note")
        await alarm_acked(
            ctx, str(alarm_id), str(acked_by) if acked_by else None, str(note) if note else None
        )
    elif event_type == constants.EVENT_ALARM_STATE_CHANGED:
        state = payload.get("new_state", "")
        await alarm_state_changed(ctx, str(alarm_id), str(state))
    elif event_type == constants.EVENT_ALARM_RESOLVED:
        # For resolved events, we could add additional handling
        logger.info("alarm_resolved_event_received", extra={"alarm_id": str(alarm_id)})
    elif event_type == constants.EVENT_ALARM_CANCELLED:
        # For cancelled events, we could add additional handling
        logger.info("alarm_cancelled_event_received", extra={"alarm_id": str(alarm_id)})
    else:
        logger.warning(
            "process_alarm_event_unknown_type",
            extra={"event_type": event_type, "alarm_id": str(alarm_id)},
        )


def _get_notification_service(ctx: dict) -> NotificationService:
    """Get notification service from worker context.

    Args:
        ctx: Worker context dictionary

    Returns:
        NotificationService instance
    """
    return NotificationService(
        zammad=ctx["zammad"],
        sendxms=ctx["sendxms"],
        signal=ctx["signal"],
    )


def _has_incomplete_event_delivery(alarm: Alarm) -> bool:
    meta = alarm.meta if isinstance(alarm.meta, dict) else {}
    state = meta.get("event_delivery")
    if not isinstance(state, dict):
        return False
    return not state.get("alarm_created_enqueued") or not state.get("alarm_state_changed_enqueued")


async def alarm_created(ctx: dict, alarm_id: str) -> None:
    """Enrich context, open Zammad ticket, send stage 0 notifications, schedule escalations."""
    alarm_uuid = uuid.UUID(alarm_id)
    sessionmaker = ctx["sessionmaker"]
    settings = ctx["settings"]
    notification = _get_notification_service(ctx)

    async with sessionmaker() as session:
        alarm = await session.get(Alarm, alarm_uuid)
        if not alarm:
            logger.warning("alarm_not_found", extra={"alarm_id": alarm_id})
            return

        enriched = await enrich_alarm_context(session, alarm)

        if not alarm.ack_token:
            logger.warning("alarm_missing_ack_token", extra={"alarm_id": alarm_id})
            ack_url = None
        else:
            ack_url = f"{settings.base_url}/a/{alarm.ack_token}"

        ticket_id = await notification.handle_zammad_ticket(
            session, alarm, enriched, ack_url, settings
        )
        if ticket_id:
            alarm.zammad_ticket_id = ticket_id
            await session.commit()

        # Send stage 0 notifications
        await notification.send(
            session=session, alarm=alarm, enriched=enriched, step_no=0, ack_url=ack_url
        )

        schedule = await notification.get_escalation_schedule(session)
        for step_no, after_seconds in schedule:
            await ctx["redis"].enqueue_job(
                "escalate",
                alarm_id,
                step_no,
                _defer_by=int(after_seconds),
                _job_id=f"escalate:{alarm_id}:{step_no}",
            )
            logger.info(
                "escalation_scheduled",
                extra={
                    "alarm_id": alarm_id,
                    "step_no": step_no,
                    "after_seconds": after_seconds,
                },
            )


async def escalate(ctx: dict, alarm_id: str, step_no: int) -> None:
    """Execute an escalation step.

    This task is scheduled by alarm_created for future execution.
    It only sends notifications if the alarm is still in triggered state.

    Args:
        ctx: Worker context with sessionmaker, settings, and connectors
        alarm_id: UUID string of the alarm
        step_no: Escalation step number to execute
    """
    alarm_uuid = uuid.UUID(alarm_id)
    sessionmaker = ctx["sessionmaker"]
    settings = ctx["settings"]
    notification = _get_notification_service(ctx)

    async with sessionmaker() as session:
        alarm = await session.get(Alarm, alarm_uuid)
        if not alarm:
            logger.warning("alarm_not_found", extra={"alarm_id": alarm_id, "step_no": step_no})
            return

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

        if not alarm.ack_token:
            logger.warning(
                "alarm_missing_ack_token",
                extra={"alarm_id": alarm_id, "step_no": step_no},
            )
            ack_url = None
        else:
            ack_url = f"{settings.base_url}/a/{alarm.ack_token}"

        await notification.send(
            session=session, alarm=alarm, enriched=enriched, step_no=step_no, ack_url=ack_url
        )

        logger.info(
            "escalation_completed",
            extra={"alarm_id": alarm_id, "step_no": step_no},
        )


async def alarm_acked(
    ctx: dict, alarm_id: str, acked_by: str | None = None, note: str | None = None
) -> None:
    """Handle alarm acknowledgment.

    This task is enqueued when an alarm is acknowledged. It updates
    the Zammad ticket with an internal note.

    Args:
        ctx: Worker context with sessionmaker, settings, and connectors
        alarm_id: UUID string of the alarm
        acked_by: Name of the person who acknowledged
        note: Optional note from the acknowledger
    """
    alarm_uuid = uuid.UUID(alarm_id)
    sessionmaker = ctx["sessionmaker"]
    zammad: ZammadClient = ctx["zammad"]

    async with sessionmaker() as session:
        alarm = await session.get(Alarm, alarm_uuid)
        if not alarm:
            logger.warning("alarm_not_found", extra={"alarm_id": alarm_id})
            return

        if not alarm.zammad_ticket_id:
            logger.warning(
                "ack_no_zammad_ticket",
                extra={
                    "alarm_id": alarm_id,
                    "detail": "Zammad ticket ID is None; ACK note will not be sent. "
                    "This may indicate a prior Zammad ticket creation failure.",
                },
            )
            return

        if not zammad.enabled():
            logger.debug("zammad_disabled", extra={"alarm_id": alarm_id})
            return

        acked_at = alarm.acked_at or datetime.now(UTC)

        notification = _get_notification_service(ctx)
        success = await notification.add_zammad_ack_note(
            session,
            alarm_id=alarm.id,
            ticket_id=alarm.zammad_ticket_id,
            acked_by=acked_by,
            acked_at=acked_at,
            note=note,
        )

        if success:
            logger.info(
                "ack_note_added",
                extra={"alarm_id": alarm_id, "ticket_id": alarm.zammad_ticket_id},
            )
        else:
            logger.warning(
                "ack_note_failed",
                extra={"alarm_id": alarm_id, "ticket_id": alarm.zammad_ticket_id},
            )


async def alarm_state_changed(ctx: dict, alarm_id: str, state: str) -> None:
    """Send state-change webhook callbacks with retry and audit logging.

    Args:
        ctx: Worker context with settings, sessionmaker and HTTP client
        alarm_id: UUID string of the alarm
        state: New alarm state value
    """
    settings = ctx["settings"]
    if not settings.is_webhook_enabled():
        return

    alarm_uuid = uuid.UUID(alarm_id)
    sessionmaker = ctx["sessionmaker"]
    http = ctx["http"]

    async with sessionmaker() as session:
        alarm = await session.get(Alarm, alarm_uuid)
        if not alarm:
            logger.warning(
                "alarm_not_found",
                extra={"alarm_id": alarm_id, "state": state, "channel": "webhook"},
            )
            return

        try:
            await validate_url_not_internal(settings.webhook_url)
        except SSRFError as exc:
            logger.warning(
                "webhook_url_rejected",
                extra={
                    "alarm_id": alarm_id,
                    "webhook_url": settings.webhook_url,
                    "error": str(exc),
                },
            )
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
            return

        payload = _build_webhook_payload(alarm, state)
        payload_bytes = json.dumps(payload, separators=(",", ":")).encode()
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if settings.webhook_secret:
            sig = hmac.new(
                settings.webhook_secret.encode(),
                payload_bytes,
                hashlib.sha256,
            ).hexdigest()
            headers["X-Hub-Signature-256"] = f"sha256={sig}"

        await _send_webhook_with_retry(
            http=http,
            webhook_url=settings.webhook_url,
            payload_bytes=payload_bytes,
            headers=headers,
            timeout=settings.webhook_timeout_seconds,
            alarm_id=alarm.id,
            session=session,
            state=state,
        )


async def recover_incomplete_alarm_events(ctx: dict) -> None:
    """Periodically retry incomplete trigger-side event publication."""
    sessionmaker = ctx["sessionmaker"]
    settings = ctx["settings"]
    redis = ctx["redis"]
    batch_size = 500

    async with sessionmaker() as session:
        trigger = TriggerService(session, redis, settings)
        offset = 0

        while True:
            alarms = (
                await session.scalars(
                    select(Alarm)
                    .where(Alarm.deleted_at.is_(None))
                    .order_by(Alarm.created_at.desc(), Alarm.id.desc())
                    .offset(offset)
                    .limit(batch_size)
                )
            ).all()
            if not alarms:
                break

            for alarm in alarms:
                if not _has_incomplete_event_delivery(alarm):
                    continue
                recovered = await trigger.recover_alarm_events(alarm)
                logger.info(
                    "alarm_event_recovery_attempted",
                    extra={"alarm_id": str(alarm.id), "recovered": recovered},
                )

            if len(alarms) < batch_size:
                break
            offset += batch_size


def _build_webhook_payload(alarm: Alarm, state: str) -> dict[str, Any]:
    """Build the webhook payload from an alarm.

    Includes a timestamp field for replay protection. Receivers should
    reject payloads with a timestamp older than 5 minutes.

    Args:
        alarm: Alarm instance
        state: New alarm state value

    Returns:
        Dictionary payload for webhook
    """
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


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=0.2, max=2), reraise=True)
async def _post_webhook(
    http: Any, url: str, payload_bytes: bytes, headers: dict, timeout: float
) -> None:
    """POST webhook with tenacity retry (3 attempts, exponential backoff)."""
    response = await http.post(url, content=payload_bytes, headers=headers, timeout=float(timeout))
    response.raise_for_status()


async def _send_webhook_with_retry(
    http: Any,
    webhook_url: str,
    payload_bytes: bytes,
    headers: dict[str, str],
    timeout: float,
    alarm_id: uuid.UUID,
    session: AsyncSession,
    state: str,
) -> None:
    """Send webhook with retry and audit logging."""
    try:
        await _post_webhook(http, webhook_url, payload_bytes, headers, timeout)
        await log_notification(
            session,
            alarm_id=alarm_id,
            channel="webhook",
            target_id=None,
            payload={"state": state},
            result="ok",
        )
        record_event("webhook_delivery_ok")
    except Exception as exc:
        logger.exception(
            "webhook_delivery_failed",
            extra={"alarm_id": str(alarm_id), "state": state, "error": str(exc)},
        )
        await log_notification(
            session,
            alarm_id=alarm_id,
            channel="webhook",
            target_id=None,
            payload={"state": state},
            result="error",
            error=str(exc),
        )
        record_event("webhook_delivery_error")
