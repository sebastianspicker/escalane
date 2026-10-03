"""Address-pinned webhook transport for notification providers."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx

from escalane.security.url_validation import SSRFError, pin_url_to_address, redact_url_for_logging

logger = logging.getLogger("escalane")
MAX_POOLED_WEBHOOK_ORIGINS = 64


@dataclass
class _PoolEntry:
    """One client isolated to a validated original webhook origin."""

    client: httpx.AsyncClient
    active: int
    last_used: float


class WebhookClientPool:
    """Bound HTTP connection reuse by original origin, independent of pinned IPs."""

    def __init__(self, *, max_origins: int = MAX_POOLED_WEBHOOK_ORIGINS) -> None:
        if max_origins <= 0:
            raise ValueError("max_origins must be positive")
        self._max_origins = max_origins
        self._entries: dict[tuple[str, str, int], _PoolEntry] = {}
        self._lock = asyncio.Lock()
        self._temporary_clients: set[httpx.AsyncClient] = set()
        self._closed = False

    @staticmethod
    def _origin(webhook_url: str) -> tuple[str, str, int]:
        parsed = urlsplit(webhook_url)
        hostname = parsed.hostname
        if not hostname:
            raise SSRFError("Webhook URL has no valid host")
        scheme = parsed.scheme.lower()
        default_port = 443 if scheme == "https" else 80
        try:
            port = parsed.port or default_port
        except ValueError as exc:
            raise SSRFError("Webhook URL has an invalid port") from exc
        return scheme, hostname.lower(), port

    @staticmethod
    def _new_client() -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=5.0, trust_env=False, follow_redirects=False)

    @asynccontextmanager
    async def client_for(self, webhook_url: str):
        """Lease an origin-isolated client, using a temporary client at saturation."""
        origin = self._origin(webhook_url)
        temporary = False
        retired: httpx.AsyncClient | None = None
        entry: _PoolEntry
        async with self._lock:
            if self._closed:
                raise RuntimeError("Webhook client pool is closed")
            existing = self._entries.get(origin)
            if existing is not None:
                entry = existing
                entry.active += 1
            elif len(self._entries) < self._max_origins:
                entry = _PoolEntry(self._new_client(), 1, time.monotonic())
                self._entries[origin] = entry
            else:
                idle = [
                    (key, candidate)
                    for key, candidate in self._entries.items()
                    if not candidate.active
                ]
                if idle:
                    retired_origin, retired_entry = min(idle, key=lambda item: item[1].last_used)
                    del self._entries[retired_origin]
                    retired = retired_entry.client
                    entry = _PoolEntry(self._new_client(), 1, time.monotonic())
                    self._entries[origin] = entry
                else:
                    entry = _PoolEntry(self._new_client(), 1, time.monotonic())
                    temporary = True
                    self._temporary_clients.add(entry.client)
        try:
            if retired is not None:
                await retired.aclose()
            yield entry.client
        finally:
            if temporary:
                try:
                    await entry.client.aclose()
                finally:
                    async with self._lock:
                        self._temporary_clients.discard(entry.client)
            else:
                async with self._lock:
                    current = self._entries.get(origin)
                    if current is entry:
                        current.active -= 1
                        current.last_used = time.monotonic()

    async def aclose(self) -> None:
        """Close every retained origin client during worker shutdown."""
        async with self._lock:
            self._closed = True
            clients = [entry.client for entry in self._entries.values()]
            clients.extend(self._temporary_clients)
            self._entries.clear()
            self._temporary_clients.clear()
        if clients:
            await asyncio.gather(*(client.aclose() for client in clients), return_exceptions=True)


def _is_retryable_transport_error(error: Exception) -> bool:
    """Return whether a webhook transport error permits another pinned address."""
    if isinstance(error, (httpx.TransportError, OSError)):
        return True
    if isinstance(error, httpx.HTTPStatusError):
        return error.response.status_code in {408, 425, 429} or error.response.status_code >= 500
    return False


def _safe_transport_error(error: Exception) -> str:
    """Return a redacted webhook transport diagnostic suitable for logs."""
    if isinstance(error, httpx.HTTPStatusError):
        return f"Downstream provider returned HTTP {error.response.status_code}"
    if isinstance(error, httpx.TimeoutException):
        return "Downstream provider request timed out"
    if isinstance(error, (httpx.TransportError, OSError)):
        return "Downstream provider transport error"
    return f"Downstream provider error ({type(error).__name__})"


def _pinned_request(
    webhook_url: str,
    resolved_address: str,
    headers: Mapping[str, str],
    delivery_id: str,
) -> tuple[str, dict[str, str], dict[str, Any]]:
    """Pin one validated address while retaining the origin Host and SNI identity."""
    request_url, host_header, sni_hostname = pin_url_to_address(webhook_url, resolved_address)
    request_headers = dict(headers)
    request_headers["Host"] = host_header
    request_headers["X-Alarm-Delivery-ID"] = delivery_id
    return request_url, request_headers, {"sni_hostname": sni_hostname}


async def post_webhook_bytes_to_validated_addresses(
    webhook_url: str,
    payload_bytes: bytes,
    headers: Mapping[str, str],
    resolved_addresses: Sequence[str],
    *,
    delivery_id: str,
    timeout: float,
    address_failed_event: str,
    log_extra: Mapping[str, Any],
    log_permanent_failures: bool = False,
    client: httpx.AsyncClient | None = None,
    client_pool: WebhookClientPool | None = None,
) -> None:
    """Post bytes within one total budget, trying only prevalidated pinned addresses.

    Retryable transport failures move on to the next address; any other failure
    stops immediately. The client comes from the pool, else the caller's
    client, else a one-shot client bounded by ``timeout``.
    """
    last_error: Exception = SSRFError("Webhook URL has no validated global addresses")
    async with asyncio.timeout(float(timeout)):
        if client_pool is not None:
            client_context = client_pool.client_for(webhook_url)
        elif client is not None:
            client_context = _provided_webhook_client(client)
        else:
            client_context = _temporary_webhook_client(float(timeout))
        async with client_context as http:
            for address in resolved_addresses:
                request_url, request_headers, extensions = _pinned_request(
                    webhook_url, address, headers, delivery_id
                )
                try:
                    response = await http.post(
                        request_url,
                        content=payload_bytes,
                        headers=request_headers,
                        timeout=float(timeout),
                        extensions=extensions,
                        follow_redirects=False,
                    )
                    response.raise_for_status()
                except Exception as exc:
                    retryable = _is_retryable_transport_error(exc)
                    if retryable or log_permanent_failures:
                        logger.warning(
                            address_failed_event,
                            extra={
                                **log_extra,
                                "url": redact_url_for_logging(webhook_url),
                                "error": _safe_transport_error(exc),
                            },
                        )
                    if not retryable:
                        raise
                    last_error = exc
                    continue
                return
    raise last_error


@asynccontextmanager
async def _temporary_webhook_client(timeout: float):
    """Provide a one-shot transport outside worker ownership."""
    async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
        yield client


@asynccontextmanager
async def _provided_webhook_client(client: httpx.AsyncClient):
    """Adapt a caller-owned client to the pooled transport path."""
    yield client
