"""IP allowlist parsing for inbound Yealink trigger requests."""

from __future__ import annotations

import ipaddress
from functools import lru_cache


@lru_cache(maxsize=32)
def _parse_allowlist(allowlist: str) -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    """Expand host or CIDR entries into networks once per configured allowlist string."""
    # ip_network also accepts a single address and turns it into a /32 or /128 network.
    return [
        ipaddress.ip_network(s.strip(), strict=False) for s in allowlist.split(",") if s.strip()
    ]


def ip_allowed(ip: str, allowlist: str) -> bool:
    """Return whether an IP is allowed by a comma-separated IP/CIDR list.

    An empty allowlist is intentionally permissive so local development can
    run without network-specific configuration; outside simulation mode,
    startup rejects an empty list, and in every mode it rejects invalid entries.
    """
    if not allowlist.strip():
        return True
    try:
        addr = ipaddress.ip_address(ip)
        networks = _parse_allowlist(allowlist)
    except ValueError:
        return False

    return any(addr in net for net in networks)
