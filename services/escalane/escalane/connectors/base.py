"""Base connector class for external service integrations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx


@dataclass(frozen=True)
class BaseConnectorConfig:
    """Base configuration for connectors.

    Attributes:
        enabled: Whether the connector is active
        base_url: Base URL for the external service
        api_key: API key for authentication (if applicable)
    """

    enabled: bool = False
    base_url: str = ""
    api_key: str = ""


class BaseConnector:
    """Abstract base class for external service connectors.

    Provides common functionality:
    - HTTP client management
    - Header construction
    - Enabled state checking

    Retry ownership belongs to the worker.  A connector call deliberately
    performs one provider request so a retrying worker cannot multiply
    externally visible deliveries.
    """

    def __init__(self, http: httpx.AsyncClient, config: BaseConnectorConfig) -> None:
        """Initialize the connector.

        Args:
            http: Async HTTP client instance
            config: Connector configuration
        """
        self._http = http
        self._cfg = config

    @property
    def config(self) -> BaseConnectorConfig:
        """Access the connector configuration.

        Returns:
            The connector's configuration object
        """
        return self._cfg

    def enabled(self) -> bool:
        """Check if the connector is enabled and properly configured.

        Returns:
            True if the connector can make requests
        """
        return self._cfg.enabled and bool(self._cfg.base_url)

    def _headers(self) -> dict[str, str]:
        """Build default headers for requests.

        Override this method to add custom headers.

        Returns:
            Dictionary of HTTP headers
        """
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._cfg.api_key:
            headers["Authorization"] = f"Bearer {self._cfg.api_key}"
        return headers

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        """Make one HTTP request.

        Args:
            method: HTTP method (GET, POST, PUT, etc.)
            path: URL path (appended to base_url)
            json: JSON body for the request
            headers: Additional headers (merged with default headers)

        Returns:
            HTTP response

        Raises:
            httpx.HTTPStatusError: If the response status is not successful
            httpx.RequestError: If the request fails
        """
        if not self.enabled():
            raise RuntimeError("Connector is not enabled")

        url = f"{self._cfg.base_url}{path}"
        merged_headers = {**self._headers(), **(headers or {})}

        response = await self._http.request(
            method,
            url,
            json=json,
            headers=merged_headers,
        )
        response.raise_for_status()
        return response

    async def _post(
        self,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        """Make one POST request.

        Args:
            path: URL path (appended to base_url)
            json: JSON body for the request
            headers: Additional headers (merged with default headers)

        Returns:
            HTTP response
        """
        return await self._request("POST", path, json=json, headers=headers)

    async def _put(
        self,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        """Make one PUT request.

        Args:
            path: URL path (appended to base_url)
            json: JSON body for the request
            headers: Additional headers (merged with default headers)

        Returns:
            HTTP response
        """
        return await self._request("PUT", path, json=json, headers=headers)
