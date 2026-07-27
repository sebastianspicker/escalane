"""Publish alarm service events onto the ARQ worker queue.

This module is the producer side of the worker wire contract. Keep the
`JOB_NAME` and payload keys aligned with `worker.tasks.process_alarm_event`.
"""

from datetime import UTC, datetime
from typing import Any

from arq.connections import ArqRedis

from escalane.constants import (
    EVENT_ALARM_ACKNOWLEDGED,
    EVENT_ALARM_CREATED,
    EVENT_ALARM_STATE_CHANGED,
)


class EventPublisher:
    """Centralized event publisher for alarm events.

    Provides a small API for the event types that currently need worker-side
    follow-up. Resolve/cancel are represented as `alarm.state_changed` events
    with the new state in the payload, not as separate ARQ job types.

    Usage:
        publisher = EventPublisher(redis)
        await publisher.publish_alarm_created(alarm_id=123)
        await publisher.publish_alarm_acknowledged(alarm_id=123, acknowledged_by="user@example.com")
    """

    JOB_NAME = "process_alarm_event"

    def __init__(self, redis: ArqRedis):
        """Initialize the EventPublisher.

        Args:
            redis: ArqRedis instance for enqueuing jobs
        """
        self._redis = redis

    async def publish_alarm_created(self, alarm_id: int | str, **kwargs: Any) -> None:
        """Publish an alarm.created event.

        Args:
            alarm_id: Alarm ID
            **kwargs: Additional payload fields
        """
        await self._publish(event_type=EVENT_ALARM_CREATED, alarm_id=alarm_id, **kwargs)

    async def publish_alarm_acknowledged(
        self, alarm_id: int | str, acknowledged_by: str, note: str | None = None, **kwargs: Any
    ) -> None:
        """Publish an alarm.acknowledged event.

        Args:
            alarm_id: Alarm ID
            acknowledged_by: Who acknowledged the alarm
            note: Optional note
            **kwargs: Additional payload fields
        """
        await self._publish(
            event_type=EVENT_ALARM_ACKNOWLEDGED,
            alarm_id=alarm_id,
            acknowledged_by=acknowledged_by,
            note=note,
            **kwargs,
        )

    async def publish_alarm_state_changed(
        self, alarm_id: int | str, old_state: str, new_state: str, **kwargs: Any
    ) -> None:
        """Publish an alarm.state_changed event.

        Args:
            alarm_id: Alarm ID
            old_state: Previous status
            new_state: New status
            **kwargs: Additional payload fields
        """
        await self._publish(
            event_type=EVENT_ALARM_STATE_CHANGED,
            alarm_id=alarm_id,
            old_state=old_state,
            new_state=new_state,
            **kwargs,
        )

    async def _publish(self, event_type: str, alarm_id: int | str, **kwargs: Any) -> None:
        """Internal method for actual job enqueuing.

        Args:
            event_type: Event type
            alarm_id: Alarm ID
            **kwargs: Additional payload fields
        """
        payload = {
            "event_type": event_type,
            "alarm_id": alarm_id,
            "timestamp": datetime.now(UTC).isoformat(),
            **kwargs,
        }
        await self._redis.enqueue_job(
            self.JOB_NAME,
            payload,
            _job_id=self._job_id_for_event(event_type, alarm_id, **kwargs),
        )

    def _job_id_for_event(self, event_type: str, alarm_id: int | str, **kwargs: Any) -> str:
        """Build deterministic ARQ job IDs so repeated publishes collapse."""
        alarm_id_str = str(alarm_id)
        if event_type == EVENT_ALARM_STATE_CHANGED:
            return f"{self.JOB_NAME}:{event_type}:{alarm_id_str}:{kwargs.get('new_state', '')}"
        return f"{self.JOB_NAME}:{event_type}:{alarm_id_str}"
