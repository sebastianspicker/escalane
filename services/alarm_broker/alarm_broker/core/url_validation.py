"""URL validation utilities to prevent SSRF attacks."""

from __future__ import annotations

import asyncio
import ipaddress
import os
import socket
from urllib.parse import urlparse


class SSRFError(ValueError):
    """Raised when a URL targets a private or reserved IP range."""

    pass


_BLOCKED_NETWORKS = [
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("198.18.0.0/15"),
    ipaddress.ip_network("240.0.0.0/4"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("::ffff:0:0/96"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]


async def validate_url_not_internal(url: str) -> None:
    """Validate that a URL does not resolve to a private/reserved IP range.

    Also restricts the scheme to https:// unless the ALLOW_HTTP_WEBHOOKS
    environment variable is set to a truthy value.

    Args:
        url: The URL to validate.

    Raises:
        SSRFError: If the URL targets a blocked IP range or uses a disallowed scheme.
    """
    parsed = urlparse(url)

    allow_http = os.environ.get("ALLOW_HTTP_WEBHOOKS", "").lower() in ("1", "true", "yes")
    allowed_schemes = {"https"} if not allow_http else {"https", "http"}
    if parsed.scheme not in allowed_schemes:
        raise SSRFError(
            f"URL scheme '{parsed.scheme}' is not allowed. "
            f"Allowed schemes: {sorted(allowed_schemes)}"
        )

    hostname = parsed.hostname
    if not hostname:
        raise SSRFError("URL has no hostname")

    try:
        loop = asyncio.get_running_loop()
        addr_infos = await loop.getaddrinfo(
            hostname, None, family=socket.AF_UNSPEC, type=socket.SOCK_STREAM
        )
    except socket.gaierror as exc:
        raise SSRFError(f"Cannot resolve hostname '{hostname}'") from exc

    for _family, _type, _proto, _canonname, sockaddr in addr_infos:
        ip_str = sockaddr[0]
        ip = ipaddress.ip_address(ip_str)
        for network in _BLOCKED_NETWORKS:
            if ip in network:
                raise SSRFError(f"URL resolves to blocked IP range {network} (resolved: {ip_str})")
