"""Mock connectors for simulation/demonstration mode.

This module provides mock implementations of the external service connectors
that store all sent notifications for later retrieval and demonstration purposes.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from escalane.connectors.zammad import ZammadConfig

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


class MockNotificationStore:
    """Thread-safe storage for mock notifications.

    This store holds all notifications sent through mock connectors
    during simulation mode, allowing retrieval for demonstration.
    """

    _instance: MockNotificationStore | None = None
    _lock = threading.Lock()
    _notifications: list[MockNotification]
    _ticket_counter: int

    def __new__(cls) -> MockNotificationStore:
        """Singleton pattern to ensure one store across the application."""
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._notifications = []
                cls._instance._ticket_counter = 1000
            return cls._instance

    def add(
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
        notification = MockNotification(
            id=f"mock-{len(self._notifications) + 1}",
            channel=channel,
            timestamp=datetime.now(),
            payload=payload,
            result=result,
            error=error,
        )
        self._notifications.append(notification)
        logger.debug(
            "mock_notification_stored",
            extra={"channel": channel, "notification_id": notification.id},
        )

    def get_all(self) -> list[MockNotification]:
        """Get all stored notifications.

        Returns:
            List of all mock notifications
        """
        return list(self._notifications)

    def get_by_channel(self, channel: str) -> list[MockNotification]:
        """Get notifications filtered by channel.

        Args:
            channel: Channel to filter by (zammad, sms, signal)

        Returns:
            List of notifications for the specified channel
        """
        return [n for n in self._notifications if n.channel == channel]

    def clear(self) -> None:
        """Clear all stored notifications."""
        self._notifications.clear()
        self._ticket_counter = 1000
        logger.info("mock_notifications_cleared")

    def generate_ticket_id(self) -> int:
        """Generate a mock ticket ID.

        Returns:
            A unique mock ticket ID
        """
        self._ticket_counter += 1
        return self._ticket_counter


# Global store instance
_mock_store = MockNotificationStore()


def get_mock_store() -> MockNotificationStore:
    """Get the global mock notification store.

    Returns:
        The singleton MockNotificationStore instance
    """
    return _mock_store


# =============================================================================
# Mock Zammad Connector
# =============================================================================


class MockZammadClient:
    """Mock Zammad connector for simulation mode.

    This mock implements the same interface as ZammadClient but stores
    all ticket operations instead of making real API calls.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize the mock Zammad client."""
        self._store = get_mock_store()
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
        ticket_id = self._store.generate_ticket_id()
        self._store.add(
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
        self._store.add(
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


# =============================================================================
# Mock SMS Connector
# =============================================================================


class MockSendXmsClient:
    """Mock SendXMS connector for simulation mode.

    This mock implements the same interface as SendXmsClient but stores
    all SMS operations instead of making real API calls.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize the mock SMS client."""
        self._store = get_mock_store()
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
        self._store.add(
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


# =============================================================================
# Mock Signal Connector
# =============================================================================


class MockSignalClient:
    """Mock Signal connector for simulation mode.

    This mock implements the same interface as SignalClient but stores
    all Signal message operations instead of making real API calls.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        """Initialize the mock Signal client."""
        self._store = get_mock_store()
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
        self._store.add(
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
