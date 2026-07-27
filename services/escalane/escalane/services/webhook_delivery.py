"""Validated, address-pinned target webhook transport."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import httpx

from escalane.core.url_validation import (
    SSRFError,
    pin_url_to_address,
    redact_url_for_logging,
)
from escalane.services.notification_delivery import (
    is_retryable_delivery_error,
    safe_delivery_error,
)
from escalane.types import NotificationPayload

logger = logging.getLogger("escalane")


def _webhook_request_options(
    webhook_url: str, resolved_address: str, delivery_id: str
) -> tuple[str, dict[str, str], dict[str, Any]]:
    """Pin one validated address without losing the original Host or SNI identity."""
    request_url, host_header, sni_hostname = pin_url_to_address(webhook_url, resolved_address)
    return (
        request_url,
        {
            "Content-Type": "application/json",
            "Host": host_header,
            "X-Alarm-Delivery-ID": delivery_id,
        },
        {"sni_hostname": sni_hostname},
    )


async def post_webhook_to_validated_addresses(
    webhook_url: str,
    payload: NotificationPayload,
    resolved_addresses: tuple[str, ...] | list[str],
    target_id: str,
    delivery_id: str,
) -> None:
    """Post within one timeout budget, trying only prevalidated pinned addresses."""
    last_error: Exception = SSRFError("Webhook URL has no validated global addresses")
    retryable_error: Exception | None = None
    async with asyncio.timeout(30.0):
        async with httpx.AsyncClient(timeout=30.0, trust_env=False) as client:
            for address in resolved_addresses:
                request_url, request_headers, request_extensions = _webhook_request_options(
                    webhook_url, address, delivery_id
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
                    last_error = exc
                    logger.warning(
                        "webhook_notification_address_failed",
                        extra={
                            "target_id": target_id,
                            "url": redact_url_for_logging(webhook_url),
                            "error": safe_delivery_error(exc),
                        },
                    )
                    if not is_retryable_delivery_error(exc):
                        raise
                    retryable_error = exc
                    continue
                return
    raise retryable_error or last_error
