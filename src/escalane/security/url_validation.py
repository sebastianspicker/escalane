"""URL validation utilities to prevent SSRF attacks."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import urlparse, urlsplit, urlunsplit


class SSRFError(ValueError):
    """Raised when a URL targets a private or reserved IP range."""

    pass


class RetryableSSRFError(SSRFError):
    """Raised when URL policy validation cannot finish due to transient DNS failure."""


def _parse_allowed_hosts(raw_hosts: str) -> frozenset[str]:
    """Normalize the explicit hostname allowlist required for generic webhooks."""
    return frozenset(host.strip().lower() for host in raw_hosts.split(",") if host.strip())


def validate_webhook_host_allowed(url: str, allowed_hosts: str) -> None:
    """Validate that a generic webhook URL hostname is explicitly allowlisted."""
    try:
        parsed = urlparse(url)
        hostname = parsed.hostname
    except ValueError as exc:
        raise SSRFError("URL has an invalid host or port") from exc
    if not hostname:
        raise SSRFError("URL has no hostname")

    allowed = _parse_allowed_hosts(allowed_hosts)
    if not allowed:
        raise SSRFError("WEBHOOK_ALLOWED_HOSTS is empty; generic webhooks are disabled")

    if hostname.lower() not in allowed:
        raise SSRFError(f"Webhook host '{hostname}' is not in WEBHOOK_ALLOWED_HOSTS")


def redact_url_for_logging(url: str) -> str:
    """Return only a URL's scheme and authority for safe logging.

    Webhook providers frequently encode credentials in path segments, so the
    path is sensitive alongside userinfo, query parameters, and fragments.
    """
    try:
        parsed = urlsplit(url)
        hostname = parsed.hostname
        if not hostname:
            return "<invalid-url>"
        display_host = f"[{hostname}]" if ":" in hostname else hostname
        if parsed.port is not None:
            display_host = f"{display_host}:{parsed.port}"
        return urlunsplit((parsed.scheme, display_host, "", "", ""))
    except ValueError:
        return "<invalid-url>"


def _pin_parts(url: str, address: str):
    """Parse a validated URL and resolved address before constructing a pinned request."""
    try:
        parsed = urlsplit(url)
        hostname = parsed.hostname
        port = parsed.port
        ip = ipaddress.ip_address(address)
    except ValueError as exc:
        raise SSRFError("URL has an invalid host, port, or address") from exc
    if not hostname:
        raise SSRFError("URL has no hostname")
    return parsed, hostname, port, ip


def _host_with_port(host: str, port: int | None, default_port: int) -> str:
    """Avoid adding a default port to Host headers or pinned URL authorities."""
    if port is None or port == default_port:
        return host
    return f"{host}:{port}"


def pin_url_to_address(url: str, address: str) -> tuple[str, str, str]:
    """Build a request URL pinned to one validated address while retaining TLS/Host identity."""
    parsed, hostname, port, ip = _pin_parts(url, address)
    pinned_host = _host_with_port(f"[{ip}]" if ip.version == 6 else str(ip), port, -1)

    default_port = 443 if parsed.scheme == "https" else 80
    host_header = _host_with_port(
        f"[{hostname}]" if ":" in hostname else hostname, port, default_port
    )

    return (
        urlunsplit((parsed.scheme, pinned_host, parsed.path, parsed.query, "")),
        host_header,
        hostname,
    )


def _allowed_schemes(*, allow_http: bool) -> set[str]:
    """Keep plain HTTP opt-in explicit so production defaults remain TLS-only."""
    return {"https", "http"} if allow_http else {"https"}


def _validated_hostname(url: str, *, allow_http: bool) -> str:
    """Reject malformed URLs, credentials, and schemes before DNS resolution."""
    try:
        parsed = urlparse(url)
        _ = parsed.port
    except ValueError as exc:
        raise SSRFError("URL has an invalid host or port") from exc
    allowed_schemes = _allowed_schemes(allow_http=allow_http)
    if parsed.scheme not in allowed_schemes:
        raise SSRFError(
            f"URL scheme '{parsed.scheme}' is not allowed. "
            f"Allowed schemes: {sorted(allowed_schemes)}"
        )
    if parsed.username is not None or parsed.password is not None:
        raise SSRFError("Embedded URL credentials are not allowed")

    hostname = parsed.hostname
    if not hostname:
        raise SSRFError("URL has no hostname")
    return hostname


def _global_addresses(addr_infos) -> tuple[str, ...]:
    """Accept only globally routable DNS answers to prevent SSRF via private ranges."""
    resolved: list[str] = []
    for _family, _type, _proto, _canonname, sockaddr in addr_infos:
        ip_str = str(sockaddr[0])
        if not ipaddress.ip_address(ip_str).is_global:
            raise SSRFError(
                f"URL resolves to a blocked IP range or non-global address (resolved: {ip_str})"
            )
        if ip_str not in resolved:
            resolved.append(ip_str)
    return tuple(resolved)


async def validate_url_not_internal(url: str, *, allow_http: bool = False) -> tuple[str, ...]:
    """Validate that a URL resolves only to public addresses and return them."""
    hostname = _validated_hostname(url, allow_http=allow_http)

    try:
        loop = asyncio.get_running_loop()
        addr_infos = await loop.getaddrinfo(
            hostname, None, family=socket.AF_UNSPEC, type=socket.SOCK_STREAM
        )
    except socket.gaierror as exc:
        raise RetryableSSRFError(f"Cannot resolve hostname '{hostname}'") from exc

    resolved = _global_addresses(addr_infos)
    if not resolved:
        raise RetryableSSRFError(f"Cannot resolve hostname '{hostname}'")
    return resolved
