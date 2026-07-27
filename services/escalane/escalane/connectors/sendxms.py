"""SMS connector for SendXMS provider."""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from escalane.connectors.base import BaseConnector, BaseConnectorConfig


@dataclass(frozen=True)
class SendXmsConfig(BaseConnectorConfig):
    """Configuration for SendXMS connector.

    Attributes:
        enabled: Whether SMS sending is enabled
        base_url: SendXMS API base URL
        api_key: API key for authentication
        from_name: Sender name/number for SMS
        send_path: API endpoint path for sending messages
    """

    enabled: bool = False
    base_url: str = ""
    api_key: str = ""
    from_name: str = "Notfall"
    send_path: str = "/send"


class SendXmsClient(BaseConnector):
    """Connector for SendXMS SMS provider integration.

    Provides methods for sending SMS messages for alarm notifications.
    """

    def __init__(self, http: httpx.AsyncClient, config: SendXmsConfig) -> None:
        """Initialize the SendXMS connector.

        Args:
            http: Async HTTP client instance
            config: SendXMS configuration
        """
        super().__init__(http, config)
        self._sms_cfg = config

    async def send_sms(self, to: str, message: str) -> None:
        """Send an SMS message.

        Args:
            to: Recipient phone number
            message: Message content

        Raises:
            httpx.HTTPStatusError: If the API request fails
        """
        if not self._sms_cfg.enabled:
            return

        payload = {"to": to, "message": message, "from": self._sms_cfg.from_name}
        await self._post(
            self._sms_cfg.send_path,
            json=payload,
            headers={"Authorization": f"Bearer {self._sms_cfg.api_key}"},
        )
