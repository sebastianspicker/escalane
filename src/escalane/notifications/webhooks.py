"""Webhook address resolution shared by target and state-change deliveries."""

from __future__ import annotations

from dataclasses import dataclass

from escalane.config.settings import Settings
from escalane.security.url_validation import (
    RetryableSSRFError,
    SSRFError,
    validate_url_not_internal,
    validate_webhook_host_allowed,
)


@dataclass(frozen=True)
class WebhookAddresses:
    """Validated global addresses to pin for this delivery attempt."""

    addresses: tuple[str, ...]


@dataclass(frozen=True)
class WebhookDnsFailure:
    """Transient resolver failure; the delivery should be retried later."""

    error: RetryableSSRFError


@dataclass(frozen=True)
class WebhookRejected:
    """Permanent allowlist or SSRF-policy rejection; the delivery must not be retried."""

    error: SSRFError


type WebhookResolution = WebhookAddresses | WebhookDnsFailure | WebhookRejected


async def resolve_webhook_addresses(webhook_url: str, settings: Settings) -> WebhookResolution:
    """Apply the host allowlist and SSRF policy, classifying the outcome for the caller."""
    try:
        validate_webhook_host_allowed(webhook_url, settings.webhook_allowed_hosts)
        addresses = await validate_url_not_internal(
            webhook_url, allow_http=settings.simulation_enabled
        )
    except RetryableSSRFError as exc:
        return WebhookDnsFailure(exc)
    except SSRFError as exc:
        return WebhookRejected(exc)
    return WebhookAddresses(tuple(addresses))
