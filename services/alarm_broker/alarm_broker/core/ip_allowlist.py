from __future__ import annotations

import ipaddress
from functools import lru_cache


@lru_cache(maxsize=32)
def _parse_allowlist(allowlist: str) -> list[ipaddress.IPv4Network | ipaddress.IPv6Network]:
    items = [s.strip() for s in allowlist.split(",") if s.strip()]
    networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    for item in items:
        if "/" in item:
            networks.append(ipaddress.ip_network(item, strict=False))
        else:
            addr = ipaddress.ip_address(item)
            suffix = 32 if addr.version == 4 else 128
            networks.append(ipaddress.ip_network(f"{item}/{suffix}", strict=False))
    return networks


def ip_allowed(ip: str, allowlist: str) -> bool:
    if not allowlist.strip():
        return True
    try:
        addr = ipaddress.ip_address(ip)
        networks = _parse_allowlist(allowlist)
    except ValueError:
        return False

    return any(addr in net for net in networks)
