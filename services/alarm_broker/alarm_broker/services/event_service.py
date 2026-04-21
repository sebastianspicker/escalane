from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Any

from alarm_broker.core.metrics import record_event
from alarm_broker.services.event_publisher import EventPublisher


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
