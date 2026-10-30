"""Mock connectors for simulation/demonstration mode.

This module provides mock implementations of the external service connectors
that store all sent notifications for later retrieval and demonstration purposes.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from escalane.providers.zammad import ZammadConfig

logger = logging.getLogger("escalane")


@dataclass
class MockNotification:
    """Represents a mock notification for demonstration purposes."""

    id: str
    channel: str  # zammad, sms, signal
    timestamp: datetime
    payload: dict[str, Any]
    result: str = "ok"
    error: str | None = None


_NOTIFICATIONS_KEY = "escalane:simulation:notifications"
_SEQ_KEY = "escalane:simulation:notification_seq"
_TICKET_COUNTER_KEY = "escalane:simulation:ticket_counter"
_MAX_NOTIFICATIONS = 500


class MockNotificationStore:
    """Redis-backed storage for mock notifications.

    The API and worker run in separate processes, so records and mock ticket
    counters live in Redis. Only the newest 500 records are kept.
    """

    def __init__(self, redis: Any) -> None:
        """Bind the store to a Redis client shared by the API and worker."""
        self._redis = redis

    async def add(
        self,
        channel: str,
        payload: dict[str, Any],
        result: str = "ok",
        error: str | None = None,
    ) -> None:
        """Add a notification to the store.

        Args:
            channel: Notification channel (zammad, sms, signal)
            payload: Notification payload
            result: Result status (ok, error)
            error: Error message if result is not ok
        """
        notification_id = f"mock-{await self._redis.incr(_SEQ_KEY)}"
        record = {
            "id": notification_id,
            "channel": channel,
            "timestamp": datetime.now(UTC).isoformat(),
            "payload": payload,
            "result": result,
            "error": error,
        }
        await self._redis.rpush(_NOTIFICATIONS_KEY, json.dumps(record, default=str))
        await self._redis.ltrim(_NOTIFICATIONS_KEY, -_MAX_NOTIFICATIONS, -1)
        logger.debug(
            "mock_notification_stored",
            extra={"channel": channel, "notification_id": notification_id},
        )

    async def get_all(self) -> list[MockNotification]:
        """Get all stored notifications, oldest first.

        Returns:
            List of all mock notifications
        """
        raw_records = await self._redis.lrange(_NOTIFICATIONS_KEY, 0, -1)
        notifications: list[MockNotification] = []
        for raw in raw_records:
            data = json.loads(raw)
            notifications.append(
                MockNotification(
                    id=data["id"],
                    channel=data["channel"],
                    timestamp=datetime.fromisoformat(data["timestamp"]),
                    payload=data["payload"],
                    result=data.get("result", "ok"),
                    error=data.get("error"),
                )
            )
        return notifications

    async def get_by_channel(self, channel: str) -> list[MockNotification]:
        """Get notifications filtered by channel.

        Args:
            channel: Channel to filter by (zammad, sms, signal)

        Returns:
            List of notifications for the specified channel
        """
        return [n for n in await self.get_all() if n.channel == channel]

    async def clear(self) -> None:
        """Clear all stored notifications and reset the counters."""
        await self._redis.delete(_NOTIFICATIONS_KEY, _SEQ_KEY, _TICKET_COUNTER_KEY)
        logger.info("mock_notifications_cleared")

    async def generate_ticket_id(self) -> int:
        """Generate a mock ticket ID.

        Returns:
            A unique mock ticket ID
        """
        return 1000 + int(await self._redis.incr(_TICKET_COUNTER_KEY))


def get_mock_store(redis: Any) -> MockNotificationStore:
    """Build a mock notification store over the shared Redis client.

    Returns:
        A MockNotificationStore backed by ``redis``
    """
    return MockNotificationStore(redis)


class MockZammadClient:
    """Mock Zammad connector for simulation mode.

    This mock implements the same interface as ZammadClient but stores
    all ticket operations instead of making real API calls.
    """

    def __init__(self, *args: Any, store: MockNotificationStore, **kwargs: Any) -> None:
        """Initialize the mock Zammad client."""
        self._store = store
        self._enabled = True
        self._config = ZammadConfig(enabled=True, base_url="http://mock-zammad")

    @property
    def config(self) -> ZammadConfig:
        """Access the mock Zammad configuration."""
        return self._config

    def enabled(self) -> bool:
        """Always return True in simulation mode."""
        return self._enabled

    async def create_ticket(self, payload: dict[str, Any]) -> int:
        """Create a mock ticket.

        Args:
            payload: Ticket data including title, group, priority, etc.

        Returns:
            A mock ticket ID
        """
        ticket_id = await self._store.generate_ticket_id()
        await self._store.add(
            channel="zammad",
            payload={
                "action": "create_ticket",
                "ticket_id": ticket_id,
                "title": payload.get("title"),
                "group": payload.get("group"),
            },
            result="ok",
        )
        logger.info(
            "mock_zammad_ticket_created",
            extra={"ticket_id": ticket_id, "title": payload.get("title")},
        )
        return ticket_id

    async def add_internal_note(self, ticket_id: int, subject: str, body: str) -> None:
        """Add a mock internal note to a ticket.

        Args:
            ticket_id: ID of the ticket to update
            subject: Note subject
            body: Note body content
        """
        await self._store.add(
            channel="zammad",
            payload={
                "action": "add_internal_note",
                "ticket_id": ticket_id,
                "subject": subject,
                "body": body,
            },
            result="ok",
        )
        logger.info(
            "mock_zammad_note_added",
            extra={"ticket_id": ticket_id, "subject": subject},
        )

    async def add_email_article(self, ticket_id: int, *, to: str, subject: str, body: str) -> None:
        """Record a mock outgoing email article on a ticket.

        Args:
            ticket_id: ID of the ticket that receives the article
            to: Recipient email address
            subject: Email subject
            body: Email body content
        """
        await self._store.add(
            channel="zammad",
            payload={
                "action": "email_article",
                "ticket_id": ticket_id,
                "to": to,
                "subject": subject,
                "body": body,
            },
            result="ok",
        )
        logger.info(
            "mock_zammad_email_article_added",
            extra={"ticket_id": ticket_id, "subject": subject},
        )


class MockSendXmsClient:
    """Mock SendXMS connector for simulation mode.

    This mock implements the same interface as SendXmsClient but stores
    all SMS operations instead of making real API calls.
    """

    def __init__(self, *args: Any, store: MockNotificationStore, **kwargs: Any) -> None:
        """Initialize the mock SMS client."""
        self._store = store
        self._enabled = True

    def enabled(self) -> bool:
        """Always return True in simulation mode."""
        return self._enabled

    async def send_sms(self, to: str, message: str) -> None:
        """Record a mock SMS send.

        Args:
            to: Recipient phone number
            message: Message content
        """
        await self._store.add(
            channel="sms",
            payload={
                "action": "send_sms",
                "to": to,
                "from": "Alarm",
                "message": message,
            },
            result="ok",
        )
        logger.info("mock_sms_sent", extra={"to": to, "message_length": len(message)})


class MockSignalClient:
    """Mock Signal connector for simulation mode.

    This mock implements the same interface as SignalClient but stores
    all Signal message operations instead of making real API calls.
    """

    def __init__(self, *args: Any, store: MockNotificationStore, **kwargs: Any) -> None:
        """Initialize the mock Signal client."""
        self._store = store
        self._enabled = True

    def enabled(self) -> bool:
        """Always return True in simulation mode."""
        return self._enabled

    async def send_group_message(self, message: str, group_id: str | None = None) -> None:
        """Record a mock Signal group message.

        Args:
            message: Message content
            group_id: Target group ID
        """
        await self._store.add(
            channel="signal",
            payload={
                "action": "send_group_message",
                "group_id": group_id,
                "message": message,
            },
            result="ok",
        )
        logger.info(
            "mock_signal_sent",
            extra={"group_id": group_id, "message_length": len(message)},
        )
