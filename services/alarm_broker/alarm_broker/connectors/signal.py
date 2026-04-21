"""Signal connector for group messaging.

This module provides integration with signal-cli-rest-api for sending
alarm notifications to Signal groups.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from alarm_broker.connectors.base import BaseConnector, BaseConnectorConfig


@dataclass(frozen=True)
class SignalConfig(BaseConnectorConfig):
    """Configuration for Signal connector.

    Attributes:
        enabled: Whether Signal sending is enabled
        endpoint: signal-cli-rest-api endpoint URL
        target_group_id: Default target group ID for messages
        send_path: API endpoint path for sending messages
    """

    enabled: bool = False
    base_url: str = ""  # Maps to endpoint for consistency
    endpoint: str = ""
    target_group_id: str = ""
    send_path: str = "/v2/send"

    def __post_init__(self) -> None:
        # Ensure base_url is set from endpoint for BaseConnector compatibility
        if self.endpoint and not self.base_url:
            object.__setattr__(self, "base_url", self.endpoint)


class SignalClient(BaseConnector):
    """Connector for Signal messaging integration.

    Provides methods for sending messages to Signal groups for alarm notifications.
    """

    def __init__(self, http: httpx.AsyncClient, config: SignalConfig) -> None:
        """Initialize the Signal connector.

        Args:
            http: Async HTTP client instance
            config: Signal configuration
        """
        super().__init__(http, config)
        self._signal_cfg = config

    async def send_group_message(self, message: str, group_id: str | None = None) -> None:
        """Send a message to a Signal group.

        Args:
            message: Message content
            group_id: Target group ID (uses default if not specified)

        Raises:
            httpx.HTTPStatusError: If the API request fails
        """
        if not self._signal_cfg.enabled:
            return

        gid = group_id or self._signal_cfg.target_group_id
        payload = {"message": message, "groupId": gid}
        await self._post_with_retry(self._signal_cfg.send_path, json=payload)
