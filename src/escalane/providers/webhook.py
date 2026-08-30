"""Address-pinned webhook transport for notification providers."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Mapping, Sequence
from typing import Any

import httpx

from escalane.security.url_validation import SSRFError, pin_url_to_address, redact_url_for_logging

logger = logging.getLogger("escalane")


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
) -> None:
    """Post JSON within the caller's total budget to prevalidated pinned addresses."""
    last_error: Exception = SSRFError("Webhook URL has no validated global addresses")
    retryable_error: Exception | None = None
    async with asyncio.timeout(float(timeout)):
        async with httpx.AsyncClient(timeout=float(timeout), trust_env=False) as client:
            for address in resolved_addresses:
                retryable_error = await _post_webhook_to_validated_address(
                    client, webhook_url, payload, address, target_id, delivery_id
                )
                if retryable_error is None:
                    return
                last_error = retryable_error
    raise retryable_error or last_error


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
) -> None:
    """Post bytes within one total budget, trying only prevalidated pinned addresses."""
    last_error: Exception = SSRFError("Webhook URL has no validated global addresses")
    async with asyncio.timeout(float(timeout)):
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
