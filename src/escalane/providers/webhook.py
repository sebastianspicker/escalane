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


async def _post_webhook_to_validated_address(
    client: httpx.AsyncClient,
    webhook_url: str,
    payload: Any,
    resolved_address: str,
    target_id: str,
    delivery_id: str,
    timeout: float,
) -> Exception | None:
    """Post a JSON payload to one pinned address and return a retryable failure."""
    request_url, request_headers, request_extensions = _pinned_request(
        webhook_url,
        resolved_address,
        {"Content-Type": "application/json"},
        delivery_id,
    )
    try:
        response = await client.post(
            request_url,
            json=payload,
            headers=request_headers,
            extensions=request_extensions,
            timeout=timeout,
            follow_redirects=False,
        )
        response.raise_for_status()
    except Exception as exc:
        logger.warning(
            "webhook_notification_address_failed",
            extra={
                "target_id": target_id,
                "url": redact_url_for_logging(webhook_url),
                "error": _safe_transport_error(exc),
            },
        )
        if not _is_retryable_transport_error(exc):
            raise
        return exc
    return None


async def post_webhook_to_validated_addresses(
    webhook_url: str,
    payload: Any,
    resolved_addresses: Sequence[str],
    target_id: str,
    delivery_id: str,
    timeout: float,
    *,
    client_pool: WebhookClientPool | None = None,
) -> None:
    """Post JSON within the caller's total budget to prevalidated pinned addresses."""
    last_error: Exception = SSRFError("Webhook URL has no validated global addresses")
    retryable_error: Exception | None = None
    async with asyncio.timeout(float(timeout)):
        if client_pool is None:
            client_context = _temporary_webhook_client(float(timeout))
        else:
            client_context = client_pool.client_for(webhook_url)
        async with client_context as client:
            for address in resolved_addresses:
                retryable_error = await _post_webhook_to_validated_address(
                    client, webhook_url, payload, address, target_id, delivery_id, float(timeout)
                )
                if retryable_error is None:
                    return
                last_error = retryable_error
    raise retryable_error or last_error


@asynccontextmanager
async def _temporary_webhook_client(timeout: float):
    """Provide backward-compatible one-shot transport outside worker ownership."""
    async with httpx.AsyncClient(timeout=timeout, trust_env=False) as client:
        yield client


async def post_webhook_bytes_to_validated_addresses(
    http: Any,
    webhook_url: str,
    payload_bytes: bytes,
    headers: Mapping[str, str],
    timeout: float,
    delivery_id: str,
    resolved_addresses: Sequence[str],
    *,
    log_extra: Mapping[str, Any],
    client_pool: WebhookClientPool | None = None,
) -> None:
    """Post bytes within one total budget, trying only prevalidated pinned addresses."""
    last_error: Exception = SSRFError("Webhook URL has no validated global addresses")
    async with asyncio.timeout(float(timeout)):
        client_context = (
            client_pool.client_for(webhook_url)
            if client_pool is not None
            else _provided_webhook_client(http)
        )
        async with client_context as client:
            for address in resolved_addresses:
                request_url, request_headers, extensions = _pinned_request(
                    webhook_url, address, headers, delivery_id
                )
                try:
                    response = await client.post(
                        request_url,
                        content=payload_bytes,
                        headers=request_headers,
                        timeout=float(timeout),
                        extensions=extensions,
                        follow_redirects=False,
                    )
                    response.raise_for_status()
                except Exception as exc:
                    if not _is_retryable_transport_error(exc):
                        raise
                    logger.warning(
                        "webhook_delivery_address_failed",
                        extra={
                            **log_extra,
                            "url": redact_url_for_logging(webhook_url),
                            "error": _safe_transport_error(exc),
                        },
                    )
                    last_error = exc
                    continue
                return
    raise last_error


@asynccontextmanager
async def _provided_webhook_client(client: Any):
    """Adapt the existing injected-client API to the pooled transport path."""
    yield client
