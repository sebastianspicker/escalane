"""Shared FastAPI dependencies with fail-closed proxy and resource handling."""

from __future__ import annotations

import ipaddress
import secrets
from collections.abc import AsyncIterator
from functools import lru_cache

from arq.connections import ArqRedis
from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from escalane.config.settings import Settings, get_settings


def get_app_settings(request: Request) -> Settings:
    """Return lifespan settings so request handling shares one validated configuration."""
    settings = getattr(request.app.state, "settings", None)
    if settings is None:
        settings = get_settings()
    return settings


def require_admin(
    request: Request,
    x_admin_key: str | None = Header(default=None, alias="X-Admin-Key"),
    settings: Settings = Depends(get_app_settings),
) -> None:
    """Require the static admin API key for privileged API endpoints.

    Missing server-side configuration fails closed with 403. A bad client key is
    401 so operators can distinguish "server not configured" from "wrong key".
    """
    if not settings.admin_api_key:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin key not configured on server",
        )
    if not secrets.compare_digest(x_admin_key or "", settings.admin_api_key):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid admin key")


def get_sessionmaker(request: Request) -> async_sessionmaker[AsyncSession]:
    """Require the lifespan-created session factory instead of creating per-request engines."""
    sessionmaker: async_sessionmaker[AsyncSession] | None = getattr(
        request.app.state, "sessionmaker", None
    )
    if sessionmaker is None:
        raise RuntimeError("DB not initialized")
    return sessionmaker


async def get_session(
    sessionmaker: async_sessionmaker[AsyncSession] = Depends(get_sessionmaker),
) -> AsyncIterator[AsyncSession]:
    """Yield a scoped async database session and always close it after the response."""
    async with sessionmaker() as session:
        yield session


def get_redis(request: Request) -> ArqRedis:
    """Require the lifespan-created Redis pool for request and queue coordination."""
    redis: ArqRedis | None = getattr(request.app.state, "redis", None)
    if redis is None:
        raise RuntimeError("Redis not initialized")
    return redis


@lru_cache(maxsize=16)
def _parse_trusted_proxy_cidrs(
    raw: str,
) -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    """Parse configured proxy networks once, silently ignoring malformed optional entries."""
    cidrs = [item.strip() for item in raw.split(",") if item.strip()]
    networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    for cidr in cidrs:
        try:
            networks.append(ipaddress.ip_network(cidr, strict=False))
        except ValueError:
            continue
    return tuple(networks)


def _is_ip(value: str) -> bool:
    """Return whether a header or peer value is a literal IP address."""
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return False
    return True


def _is_trusted_proxy(peer_ip: str, trusted_proxy_cidrs: str) -> bool:
    """Allow forwarding headers only when the immediate peer is explicitly trusted."""
    if not trusted_proxy_cidrs.strip() or not _is_ip(peer_ip):
        return False
    peer = ipaddress.ip_address(peer_ip)
    return any(peer in net for net in _parse_trusted_proxy_cidrs(trusted_proxy_cidrs))


def _forwarded_client_ip(forwarded: str, trusted_proxy_cidrs: str, peer_ip: str) -> str:
    """Walk a proxy chain from the trusted peer toward the first untrusted client."""
    for candidate in reversed(forwarded.split(",")):
        candidate = candidate.strip()
        if not _is_ip(candidate):
            return peer_ip
        if not _is_trusted_proxy(candidate, trusted_proxy_cidrs):
            return candidate
    return peer_ip


def get_client_ip(request: Request, settings: Settings | None = None) -> str | None:
    """Return the caller IP, honoring X-Forwarded-For only from trusted proxies.

    ``None`` means the ASGI server did not provide a syntactically valid peer
    address. Callers must choose a fail-closed or shared-bucket policy rather
    than treating that request as loopback traffic.
    """
    peer_ip = request.client.host if request.client else ""
    forwarded = request.headers.get("x-forwarded-for")
    trusted_proxy_cidrs = settings.trusted_proxy_cidrs if settings else ""

    if forwarded and _is_trusted_proxy(peer_ip, trusted_proxy_cidrs):
        return _forwarded_client_ip(forwarded, trusted_proxy_cidrs, peer_ip)

    if _is_ip(peer_ip):
        return peer_ip
    return None


def is_secure_request(request: Request, settings: Settings | None = None) -> bool:
    """Return whether the original client request was HTTPS.

    `X-Forwarded-Proto` is intentionally ignored unless the immediate peer is
    in `TRUSTED_PROXY_CIDRS`; otherwise any client could force Secure-cookie
    behavior over plain HTTP.
    """
    if request.url.scheme == "https":
        return True

    peer_ip = request.client.host if request.client else ""
    trusted_proxy_cidrs = settings.trusted_proxy_cidrs if settings else ""
    forwarded_proto = request.headers.get("x-forwarded-proto", "")

    if forwarded_proto and _is_trusted_proxy(peer_ip, trusted_proxy_cidrs):
        first_proto = forwarded_proto.split(",")[0].strip().lower()
        return first_proto == "https"

    return False
