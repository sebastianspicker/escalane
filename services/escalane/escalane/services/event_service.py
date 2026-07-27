"""Publish durable alarm outbox rows to Redis with ordered recovery semantics."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased
from sqlalchemy.sql import Select

from escalane import constants
from escalane.core.metrics import record_event
from escalane.db.models import Alarm, AlarmEventOutbox
from escalane.services.event_publisher import EventPublisher
from escalane.services.notification_delivery import completed_notification

ACK_EVENT_REPLAY_STALE_SECONDS = 600


@dataclass(frozen=True)
class EventResult:
    """Result of an event enqueue operation."""

    success: bool
    error: str | None = None


async def enqueue_alarm_acked_event(
    redis: Any,
    *,
    alarm_id: uuid.UUID,
    acked_by: str | None,
    note: str | None,
    logger: logging.Logger,
) -> EventResult:
    """Enqueue an alarm acknowledged event.

    Convenience wrapper around EventPublisher with error handling and metrics.

    Args:
        redis: Redis connection
        alarm_id: UUID of the alarm
        acked_by: Who acknowledged the alarm
        note: Optional note
        logger: Logger instance

    Returns:
        EventResult indicating success or failure with error detail
    """
    try:
        publisher = EventPublisher(redis)
        await publisher.publish_alarm_acknowledged(
            alarm_id=str(alarm_id),
            acknowledged_by=acked_by or "unknown",
            note=note,
        )
        record_event("alarm_acked_enqueued")
        return EventResult(success=True)
    except Exception as exc:
        logger.exception("enqueue alarm_acked failed", extra={"alarm_id": str(alarm_id)})
        return EventResult(success=False, error=str(exc))


async def enqueue_alarm_created_event(
    redis: Any,
    *,
    alarm_id: uuid.UUID,
    logger: logging.Logger,
) -> EventResult:
    """Enqueue an alarm created event.

    Convenience wrapper around EventPublisher with error handling and metrics.
    """
    try:
        publisher = EventPublisher(redis)
        await publisher.publish_alarm_created(alarm_id=str(alarm_id))
        record_event("alarm_created_enqueued")
        return EventResult(success=True)
    except Exception as exc:
        logger.exception("enqueue alarm_created failed", extra={"alarm_id": str(alarm_id)})
        return EventResult(success=False, error=str(exc))


async def enqueue_alarm_state_changed_event(
    redis: Any,
    *,
    alarm_id: uuid.UUID,
    state: str,
    logger: logging.Logger,
    old_state: str = "unknown",
) -> EventResult:
    """Enqueue an alarm state changed event.

    Convenience wrapper around EventPublisher with error handling and metrics.

    Args:
        redis: Redis connection
        alarm_id: UUID of the alarm
        state: New state
        logger: Logger instance

    Returns:
        EventResult indicating success or failure with error detail
    """
    try:
        publisher = EventPublisher(redis)
        await publisher.publish_alarm_state_changed(
            alarm_id=str(alarm_id),
            old_state=old_state,
            new_state=state,
        )
        record_event("alarm_state_changed_enqueued")
        return EventResult(success=True)
    except Exception as exc:
        logger.exception(
            "enqueue alarm_state_changed failed",
            extra={"alarm_id": str(alarm_id), "state": state},
        )
        return EventResult(success=False, error=str(exc))


async def _enqueue_state_changed_outbox_event(
    redis: Any,
    event: AlarmEventOutbox,
    payload: dict,
    logger: logging.Logger,
) -> EventResult:
    """Validate and publish one state-change outbox row."""
    new_state = payload.get("new_state")
    old_state = payload.get("old_state")
    if not new_state:
        return EventResult(success=False, error="outbox state event has no new_state")
    return await enqueue_alarm_state_changed_event(
        redis,
        alarm_id=event.alarm_id,
        state=str(new_state),
        old_state=str(old_state) if old_state else "unknown",
        logger=logger,
    )


async def _enqueue_outbox_event(
    redis: Any, event: AlarmEventOutbox, logger: logging.Logger
) -> EventResult:
    """Validate and map one durable outbox row to its stable queue event."""
    payload = event.payload or {}
    if event.event_type == constants.EVENT_ALARM_CREATED:
        return await enqueue_alarm_created_event(
            redis,
            alarm_id=event.alarm_id,
            logger=logger,
        )
    if event.event_type == constants.EVENT_ALARM_ACKNOWLEDGED:
        acknowledged_by = payload.get("acknowledged_by")
        note = payload.get("note")
        return await enqueue_alarm_acked_event(
            redis,
            alarm_id=event.alarm_id,
            acked_by=str(acknowledged_by) if acknowledged_by else None,
            note=str(note) if note else None,
            logger=logger,
        )
    if event.event_type == constants.EVENT_ALARM_STATE_CHANGED:
        return await _enqueue_state_changed_outbox_event(redis, event, payload, logger)
    return EventResult(success=False, error=f"unsupported outbox event type: {event.event_type}")


def _pending_outbox_statement(
    alarm_id: uuid.UUID | None,
    blocked_alarms: set[uuid.UUID],
    remaining_limit: int,
) -> Select[tuple[AlarmEventOutbox]]:
    """Build the ordered query for the next publishable outbox batch."""
    earlier = aliased(AlarmEventOutbox)
    has_earlier_pending_event = exists(
        select(1).where(
            earlier.alarm_id == AlarmEventOutbox.alarm_id,
            earlier.published_at.is_(None),
            or_(
                earlier.created_at < AlarmEventOutbox.created_at,
                and_(
                    earlier.created_at == AlarmEventOutbox.created_at,
                    earlier.sequence < AlarmEventOutbox.sequence,
                ),
                and_(
                    earlier.created_at == AlarmEventOutbox.created_at,
                    earlier.sequence == AlarmEventOutbox.sequence,
                    earlier.id < AlarmEventOutbox.id,
                ),
            ),
        )
    )
    statement = (
        select(AlarmEventOutbox)
        .where(
            AlarmEventOutbox.published_at.is_(None),
            ~has_earlier_pending_event,
        )
        .order_by(AlarmEventOutbox.created_at, AlarmEventOutbox.sequence, AlarmEventOutbox.id)
        .limit(remaining_limit)
        .with_for_update(skip_locked=True)
    )
    if alarm_id is not None:
        statement = statement.where(AlarmEventOutbox.alarm_id == alarm_id)
    if blocked_alarms:
        statement = statement.where(AlarmEventOutbox.alarm_id.not_in(blocked_alarms))
    return statement


async def dispatch_pending_alarm_events(
    session: AsyncSession,
    redis: Any,
    *,
    logger: logging.Logger,
    alarm_id: uuid.UUID | None = None,
    limit: int = 500,
) -> int:
    """Publish pending lifecycle events and durably record accepted queue writes."""
    published = 0
    blocked_alarms: set[uuid.UUID] = set()
    attempted = 0
    while attempted < limit:
        stmt = _pending_outbox_statement(alarm_id, blocked_alarms, limit - attempted)
        events = list((await session.scalars(stmt)).all())
        if not events:
            break

        for event in events:
            result = await _enqueue_outbox_event(redis, event, logger)
            attempted += 1
            event.attempts += 1
            event.last_error = result.error
            if result.success:
                event.published_at = datetime.now(UTC)
                published += 1
                continue
            blocked_alarms.add(event.alarm_id)
            logger.warning(
                "alarm_event_outbox_publish_failed",
                extra={
                    "alarm_id": str(event.alarm_id),
                    "outbox_id": str(event.id),
                    "event_type": event.event_type,
                    "error": result.error,
                },
            )
    await session.commit()
    return published


async def has_pending_alarm_events(session: AsyncSession, alarm_id: uuid.UUID) -> bool:
    """Return whether durable lifecycle events still await queue acceptance."""
    pending_id = await session.scalar(
        select(AlarmEventOutbox.id)
        .where(
            AlarmEventOutbox.alarm_id == alarm_id,
            AlarmEventOutbox.published_at.is_(None),
        )
        .limit(1)
    )
    return pending_id is not None


async def record_published_alarm_event_failure(
    session: AsyncSession,
    *,
    alarm_id: uuid.UUID,
    event_type: str,
    error: str,
) -> int:
    """Record a terminal worker failure without losing its durable event marker."""
    events = list(
        (
            await session.scalars(
                select(AlarmEventOutbox).where(
                    AlarmEventOutbox.alarm_id == alarm_id,
                    AlarmEventOutbox.event_type == event_type,
                    AlarmEventOutbox.published_at.is_not(None),
                )
            )
        ).all()
    )
    for event in events:
        event.last_error = error
    await session.commit()
    return len(events)


async def rearm_stale_acknowledgement_events(
    session: AsyncSession,
    *,
    limit: int = 500,
) -> int:
    """Reopen stale ACK events after a ticket exists and no terminal audit does."""
    cutoff = datetime.now(UTC) - timedelta(seconds=ACK_EVENT_REPLAY_STALE_SECONDS)
    rows = (
        await session.execute(
            select(AlarmEventOutbox, Alarm.zammad_ticket_id)
            .join(Alarm, Alarm.id == AlarmEventOutbox.alarm_id)
            .where(
                AlarmEventOutbox.event_type == constants.EVENT_ALARM_ACKNOWLEDGED,
                AlarmEventOutbox.published_at <= cutoff,
                Alarm.zammad_ticket_id.is_not(None),
                Alarm.deleted_at.is_(None),
            )
            .order_by(AlarmEventOutbox.published_at, AlarmEventOutbox.id)
            .limit(limit)
        )
    ).all()
    rearmed = 0
    for event, ticket_id in rows:
        if await completed_notification(
            session,
            alarm_id=event.alarm_id,
            channel="zammad",
            target_id=None,
            payload_matches={"action": "ack_update", "ticket_id": ticket_id},
        ):
            continue
        event.published_at = None
        rearmed += 1
    await session.commit()
    return rearmed
