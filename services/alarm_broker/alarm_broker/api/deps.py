from __future__ import annotations

import ipaddress
import secrets
from collections.abc import AsyncIterator
from functools import lru_cache

from arq.connections import ArqRedis
from fastapi import Depends, Header, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from alarm_broker.settings import Settings, get_settings


def get_app_settings(request: Request) -> Settings:
    settings = getattr(request.app.state, "settings", None)
    if settings is None:
        settings = get_settings()
    return settings


def require_admin(
    request: Request,
    x_admin_key: str | None = Header(default=None, alias="X-Admin-Key"),
    settings: Settings = Depends(get_app_settings),
) -> None:
    # Return 403 if admin key is not configured instead of 500
    if not settings.admin_api_key:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin key not configured on server",
        )
    if not secrets.compare_digest(x_admin_key or "", settings.admin_api_key):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid admin key")


def get_sessionmaker(request: Request) -> async_sessionmaker[AsyncSession]:
    sessionmaker: async_sessionmaker[AsyncSession] | None = getattr(
        request.app.state, "sessionmaker", None
    )
    if sessionmaker is None:
        raise RuntimeError("DB not initialized")
    return sessionmaker


async def get_session(
    sessionmaker: async_sessionmaker[AsyncSession] = Depends(get_sessionmaker),
) -> AsyncIterator[AsyncSession]:
    async with sessionmaker() as session:
        yield session


def get_redis(request: Request) -> ArqRedis:
    redis: ArqRedis | None = getattr(request.app.state, "redis", None)
    if redis is None:
        raise RuntimeError("Redis not initialized")
    return redis


@lru_cache(maxsize=16)
def _parse_trusted_proxy_cidrs(
    raw: str,
) -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    cidrs = [item.strip() for item in raw.split(",") if item.strip()]
    networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    for cidr in cidrs:
        try:
            networks.append(ipaddress.ip_network(cidr, strict=False))
        except ValueError:
            continue
    return tuple(networks)


def _is_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return False
    return True


def _is_trusted_proxy(peer_ip: str, trusted_proxy_cidrs: str) -> bool:
    if not trusted_proxy_cidrs.strip() or not _is_ip(peer_ip):
        return False
    peer = ipaddress.ip_address(peer_ip)
    return any(peer in net for net in _parse_trusted_proxy_cidrs(trusted_proxy_cidrs))


def get_client_ip(request: Request, settings: Settings | None = None) -> str:
    peer_ip = request.client.host if request.client else ""
    forwarded = request.headers.get("x-forwarded-for")
    trusted_proxy_cidrs = settings.trusted_proxy_cidrs if settings else ""

    if forwarded and _is_trusted_proxy(peer_ip, trusted_proxy_cidrs):
        forwarded_ip = forwarded.split(",")[0].strip()
        if _is_ip(forwarded_ip):
            return forwarded_ip

    if _is_ip(peer_ip):
        return peer_ip
    return "127.0.0.1"


def is_secure_request(request: Request, settings: Settings | None = None) -> bool:
    if request.url.scheme == "https":
        return True

    peer_ip = request.client.host if request.client else ""
    trusted_proxy_cidrs = settings.trusted_proxy_cidrs if settings else ""
    forwarded_proto = request.headers.get("x-forwarded-proto", "")

    if forwarded_proto and _is_trusted_proxy(peer_ip, trusted_proxy_cidrs):
        first_proto = forwarded_proto.split(",")[0].strip().lower()
        return first_proto == "https"

    return False
