"""Redis-backed browser session state for the operator console.

The static admin API key is used only at login.  Browser routes receive a
random session identifier and keep the operator label and CSRF secret in
separate Redis values so no privileged credential is serialized to HTML.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass

from fastapi import HTTPException, status

from alarm_broker.settings import Settings

SESSION_TTL_SECONDS = 3600
SESSION_COOKIE = "admin_session"


@dataclass(frozen=True)
class AdminSession:
    token: str
    operator_name: str
    csrf_token: str


def _key(token: str, field: str) -> str:
    return f"admin_session:{token}:{field}"


def admin_key_marker(settings: Settings) -> str:
    return hashlib.sha256(settings.admin_api_key.encode()).hexdigest()


async def create_admin_session(redis, settings: Settings, operator_name: str) -> AdminSession:
    token = secrets.token_urlsafe(32)
    csrf_token = secrets.token_hex(32)
    values = {
        "marker": admin_key_marker(settings),
        "operator": operator_name,
        "csrf": csrf_token,
    }
    for field, value in values.items():
        await redis.set(_key(token, field), value, ex=SESSION_TTL_SECONDS)
    return AdminSession(token=token, operator_name=operator_name, csrf_token=csrf_token)


async def _delete_fields(redis, token: str) -> None:
    for field in ("marker", "operator", "csrf", "flash"):
        await redis.delete(_key(token, field))


async def destroy_admin_session(redis, token: str | None) -> None:
    if token:
        await _delete_fields(redis, token)


async def _session_values(redis, token: str) -> tuple[object | None, object | None, object | None]:
    return (
        await redis.get(_key(token, "marker")),
        await redis.get(_key(token, "operator")),
        await redis.get(_key(token, "csrf")),
    )


async def _reject_session(redis, token: str, detail: str) -> None:
    await _delete_fields(redis, token)
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail)


async def _extend_session(redis, token: str) -> None:
    for field in ("marker", "operator", "csrf"):
        await redis.expire(_key(token, field), SESSION_TTL_SECONDS)


async def require_admin_session(
    redis,
    settings: Settings,
    token: str | None,
    *,
    extend: bool,
) -> AdminSession:
    if not settings.admin_api_key:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="ADMIN_API_KEY is not configured.",
        )
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="login_required")

    marker, operator, csrf_token = await _session_values(redis, token)
    if marker is None or operator is None or csrf_token is None:
        await _reject_session(redis, token, "session_expired")

    if not secrets.compare_digest(str(marker), admin_key_marker(settings)):
        await _reject_session(redis, token, "session_invalid")

    if extend:
        await _extend_session(redis, token)

    return AdminSession(token=token, operator_name=str(operator), csrf_token=str(csrf_token))


def validate_admin_csrf(session: AdminSession, submitted: str | None) -> None:
    if submitted and secrets.compare_digest(session.csrf_token, submitted):
        return
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="csrf_invalid")


async def set_flash(redis, session: AdminSession, category: str, message_key: str) -> None:
    await redis.set(
        _key(session.token, "flash"),
        f"{category}:{message_key}",
        ex=SESSION_TTL_SECONDS,
    )


async def pop_flash(redis, session: AdminSession) -> tuple[str, str] | None:
    key = _key(session.token, "flash")
    value = await redis.get(key)
    if value is None:
        return None
    await redis.delete(key)
    category, _, message_key = str(value).partition(":")
    return category, message_key
