"""Publish durable alarm outbox rows to Redis with ordered recovery semantics."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased
from sqlalchemy.sql import Select

from escalane.config import constants
from escalane.operations.metrics import record_event
from escalane.persistence.models import AlarmEventOutbox

ARQ_JOB_NAME = "process_alarm_event"


def _worker_payload(event: AlarmEventOutbox) -> tuple[dict[str, str | None], str]:
    """Map one durable event to the stable worker payload and its success metric."""
    payload = event.payload or {}
    common: dict[str, str | None] = {
        "event_type": event.event_type,
        "alarm_id": str(event.alarm_id),
        "timestamp": datetime.now(UTC).isoformat(),
    }
    if event.event_type == constants.EVENT_ALARM_CREATED:
        return common, "alarm_created_enqueued"
    if event.event_type == constants.EVENT_ALARM_ACKNOWLEDGED:
        acknowledged_by = payload.get("acknowledged_by")
        note = payload.get("note")
        return (
            {
                **common,
                "acknowledged_by": str(acknowledged_by) if acknowledged_by else "unknown",
                "note": str(note) if note else None,
            },
            "alarm_acked_enqueued",
        )
    if event.event_type == constants.EVENT_ALARM_STATE_CHANGED:
        new_state = payload.get("new_state")
        if not new_state:
            raise ValueError("outbox state event has no new_state")
        old_state = payload.get("old_state")
        return (
            {
                **common,
                "old_state": str(old_state) if old_state else "unknown",
                "new_state": str(new_state),
            },
            "alarm_state_changed_enqueued",
        )
    raise ValueError(f"unsupported outbox event type: {event.event_type}")


def _job_id_for_payload(payload: dict[str, str | None]) -> str:
    """Build the stable ARQ ID used to collapse duplicate outbox publishes."""
    event_type = payload["event_type"]
    alarm_id = payload["alarm_id"]
    if event_type == constants.EVENT_ALARM_STATE_CHANGED:
        return f"{ARQ_JOB_NAME}:{event_type}:{alarm_id}:{payload['new_state'] or ''}"
    return f"{ARQ_JOB_NAME}:{event_type}:{alarm_id}"


async def _publish_outbox_event(
    redis: Any, event: AlarmEventOutbox, logger: logging.Logger
) -> str | None:
    """Publish one outbox row to ARQ, returning its durable failure detail if any."""
    try:
        payload, metric = _worker_payload(event)
    except ValueError as exc:
        return str(exc)

    try:
        await redis.enqueue_job(ARQ_JOB_NAME, payload, _job_id=_job_id_for_payload(payload))
    except Exception as exc:
        log_message = {
            constants.EVENT_ALARM_CREATED: "enqueue alarm_created failed",
            constants.EVENT_ALARM_ACKNOWLEDGED: "enqueue alarm_acked failed",
            constants.EVENT_ALARM_STATE_CHANGED: "enqueue alarm_state_changed failed",
        }[event.event_type]
        extra: dict[str, object] = {"alarm_id": str(event.alarm_id)}
        if event.event_type == constants.EVENT_ALARM_STATE_CHANGED:
            extra["state"] = payload["new_state"]
        logger.exception(log_message, extra=extra)
        return str(exc)

    record_event(metric)
    return None


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
            error = await _publish_outbox_event(redis, event, logger)
            attempted += 1
            event.attempts += 1
            event.last_error = error
            if error is None:
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
                    "error": error,
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
