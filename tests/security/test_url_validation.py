"""Outbound URL validation and SSRF classification tests."""

from __future__ import annotations

import socket
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from escalane.security.url_validation import (
    RetryableSSRFError,
    SSRFError,
    pin_url_to_address,
    redact_url_for_logging,
    validate_url_not_internal,
    validate_webhook_host_allowed,
)

pytestmark = [pytest.mark.security]


async def test_dns_resolution_failure_is_classified_as_retryable() -> None:
    loop = MagicMock()
    loop.getaddrinfo = AsyncMock(side_effect=socket.gaierror("temporary failure"))

    with (
        patch("escalane.security.url_validation.asyncio.get_running_loop", return_value=loop),
        pytest.raises(RetryableSSRFError, match="Cannot resolve hostname"),
    ):
        await validate_url_not_internal("https://hooks.example.test/path")


def test_url_validation_rejects_untrusted_forms_and_preserves_ipv6_authority() -> None:
    """Webhook validation rejects ambiguous targets while preserving valid IPv6 host headers."""
    with pytest.raises(SSRFError, match="empty"):
        validate_webhook_host_allowed("https://hooks.example.test/path", "")
    with pytest.raises(SSRFError, match="not in"):
        validate_webhook_host_allowed("https://hooks.example.test/path", "other.example.test")
    with pytest.raises(SSRFError, match="invalid host"):
        pin_url_to_address("https://hooks.example.test:bad/path", "8.8.8.8")

    pinned, host_header, hostname = pin_url_to_address(
        "https://[2001:db8::1]/hook", "2001:4860:4860::8888"
    )
    assert pinned == "https://[2001:4860:4860::8888]/hook"
    assert host_header == "[2001:db8::1]"
    assert hostname == "2001:db8::1"
    assert redact_url_for_logging("https://hooks.example.test:bad/path") == "<invalid-url>"


async def test_url_validation_rejects_private_and_empty_dns_answers() -> None:
    """DNS policy fails closed for private addresses and transient empty resolver answers."""
    private_loop = MagicMock()
    private_loop.getaddrinfo = AsyncMock(
        return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 0))]
    )
    with (
        patch(
            "escalane.security.url_validation.asyncio.get_running_loop", return_value=private_loop
        ),
        pytest.raises(SSRFError, match="blocked IP"),
    ):
        await validate_url_not_internal("https://hooks.example.test/path")

    empty_loop = MagicMock()
    empty_loop.getaddrinfo = AsyncMock(return_value=[])
    with (
        patch("escalane.security.url_validation.asyncio.get_running_loop", return_value=empty_loop),
        pytest.raises(RetryableSSRFError, match="Cannot resolve hostname"),
    ):
        await validate_url_not_internal("https://hooks.example.test/path")
