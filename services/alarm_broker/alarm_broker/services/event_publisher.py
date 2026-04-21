"""Event Publisher - Centralizes event enqueuing."""

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from arq.connections import ArqRedis

from alarm_broker.constants import (
    EVENT_ALARM_ACKNOWLEDGED,
    EVENT_ALARM_CANCELLED,
    EVENT_ALARM_CREATED,
    EVENT_ALARM_RESOLVED,
    EVENT_ALARM_STATE_CHANGED,
)

if TYPE_CHECKING:
    from alarm_broker.db.models import Alarm


class EventPublisher:
    """Centralized event publisher for alarm events.

    Abstracts event enqueuing into a single place and provides
    a simple API for publishing alarm lifecycle events.

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

    async def publish_alarm_resolved(
        self, alarm_id: int | str, resolved_by: str, note: str | None = None, **kwargs: Any
    ) -> None:
        """Publish an alarm.resolved event.

        Args:
            alarm_id: Alarm ID
            resolved_by: Who resolved the alarm
            note: Optional note
            **kwargs: Additional payload fields
        """
        await self._publish(
            event_type=EVENT_ALARM_RESOLVED,
            alarm_id=alarm_id,
            resolved_by=resolved_by,
            note=note,
            **kwargs,
        )

    async def publish_alarm_cancelled(
        self, alarm_id: int | str, cancelled_by: str, note: str | None = None, **kwargs: Any
    ) -> None:
        """Publish an alarm.cancelled event.

        Args:
            alarm_id: Alarm ID
            cancelled_by: Who cancelled the alarm
            note: Optional note
            **kwargs: Additional payload fields
        """
        await self._publish(
            event_type=EVENT_ALARM_CANCELLED,
            alarm_id=alarm_id,
            cancelled_by=cancelled_by,
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
        alarm_id_str = str(alarm_id)
        if event_type == EVENT_ALARM_STATE_CHANGED:
            return f"{self.JOB_NAME}:{event_type}:{alarm_id_str}:{kwargs.get('new_state', '')}"
        return f"{self.JOB_NAME}:{event_type}:{alarm_id_str}"

    @classmethod
    def from_alarm(cls, redis: ArqRedis, alarm: "Alarm") -> "EventPublisher":
        """Factory method to create a publisher with alarm context.

        Creates an EventPublisher and optionally binds additional
        fields from the alarm object.

        Args:
            redis: ArqRedis instance
            alarm: Alarm object

        Returns:
            EventPublisher instance
        """
        # Return normal instance - alarm context can be passed via kwargs
        return cls(redis)
