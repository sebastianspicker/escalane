"""Zammad connector for ticket management."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from escalane.providers.base import BaseConnector, BaseConnectorConfig


@dataclass(frozen=True)
class ZammadConfig(BaseConnectorConfig):
    """Configuration for Zammad connector.

    Attributes:
        base_url: Zammad instance URL
        api_token: API token for authentication
        group: Default ticket group
        priority_id_p0: Priority ID for P0 alarms
        state_id_new: State ID for new tickets
        customer: Default customer identifier
    """

    base_url: str = ""
    api_token: str = ""
    group: str = "Notfallstelle"
    priority_id_p0: int = 3
    state_id_new: int = 1
    customer: str = "guess:alarm-system@example.org"


class ZammadClient(BaseConnector):
    """Connector for Zammad helpdesk integration.

    Provides methods for creating tickets and adding internal notes
    for alarm events.
    """

    def __init__(self, http: httpx.AsyncClient, config: ZammadConfig) -> None:
        """Initialize the Zammad connector.

        Args:
            http: Async HTTP client instance
            config: Zammad configuration
        """
        super().__init__(http, config)
        self._zammad_cfg = config

    @property
    def config(self) -> ZammadConfig:
        """Access the Zammad-specific configuration.

        Returns:
            The connector's ZammadConfig object
        """
        return self._zammad_cfg

    def enabled(self) -> bool:
        """Check if Zammad integration is enabled.

        Returns:
            True if API token is configured
        """
        return bool(self._zammad_cfg.api_token and self._zammad_cfg.base_url)

    def _headers(self) -> dict[str, str]:
        """Build headers for Zammad API requests.

        Returns:
            Dictionary with Authorization header
        """
        return {"Authorization": f"Bearer {self._zammad_cfg.api_token}"}

    async def create_ticket(self, payload: dict[str, Any]) -> int:
        """Create a new ticket in Zammad.

        Args:
            payload: Ticket data including title, group, priority, etc.

        Returns:
            The created ticket ID

        Raises:
            RuntimeError: If response doesn't contain a valid ticket ID
            httpx.HTTPStatusError: If the API request fails
        """
        resp = await self._post("/api/v1/tickets", json=payload)
        data = resp.json()
        ticket_id = data.get("id")
        if not isinstance(ticket_id, int):
            raise RuntimeError("Zammad response missing ticket id")
        return ticket_id

    async def add_internal_note(self, ticket_id: int, subject: str, body: str) -> None:
        """Add an internal note to an existing ticket.

        Args:
            ticket_id: ID of the ticket to update
            subject: Note subject
            body: Note body content

        Raises:
            httpx.HTTPStatusError: If the API request fails
        """
        payload = {
            "article": {
                "subject": subject,
                "body": body,
                "type": "note",
                "internal": True,
            }
        }
        await self._put(f"/api/v1/tickets/{ticket_id}", json=payload)
