"""Redis-backed browser session state for the operator console.

The static admin API key is used only at login.  Browser routes receive a
random session identifier and keep the operator label and CSRF secret in
separate Redis values so no privileged credential is serialized to HTML.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from typing import NoReturn

from fastapi import HTTPException, status

from escalane.config.settings import Settings

SESSION_TTL_SECONDS = 3600
SESSION_COOKIE = "admin_session"


@dataclass(frozen=True)
class AdminSession:
    """Validated browser-session data kept separately from the static admin key."""

    token: str
    operator_name: str
    csrf_token: str


def _key(token: str, field: str) -> str:
    """Namespace each session field so Redis expiry and deletion stay granular."""
    return f"admin_session:{token}:{field}"


def _redis_text(value: object) -> str | None:
    """Return a Redis string reply without turning byte values into repr text."""
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            return None
    return None


def admin_key_marker(settings: Settings) -> str:
    """Derive the digest used only to invalidate sessions after key rotation."""
    return hashlib.sha256(settings.admin_api_key.encode()).hexdigest()


async def create_admin_session(redis, settings: Settings, operator_name: str) -> AdminSession:
    """Create short-lived Redis session state without persisting the admin credential."""
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
    """Remove every Redis field associated with a browser session token."""
    for field in ("marker", "operator", "csrf", "flash"):
        await redis.delete(_key(token, field))


async def destroy_admin_session(redis, token: str | None) -> None:
    """Invalidate a supplied session token; missing cookies are harmless on logout."""
    if token:
        await _delete_fields(redis, token)


async def _session_values(redis, token: str) -> tuple[object | None, object | None, object | None]:
    """Load the marker, operator, and CSRF values needed to validate a session."""
    return (
        await redis.get(_key(token, "marker")),
        await redis.get(_key(token, "operator")),
        await redis.get(_key(token, "csrf")),
    )


async def _reject_session(redis, token: str, detail: str) -> NoReturn:
    """Delete suspect session state before returning an authentication failure."""
    await _delete_fields(redis, token)
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=detail)


async def _extend_session(redis, token: str) -> None:
    """Refresh required fields sequentially; validation rejects any partial expiry."""
    for field in ("marker", "operator", "csrf"):
        await redis.expire(_key(token, field), SESSION_TTL_SECONDS)


def _has_missing_session_value(values: tuple[object | None, object | None, object | None]) -> bool:
    """Detect partial Redis expiry, which is treated as an invalid session."""
    return any(value is None for value in values)


def _validated_session(
    values: tuple[object | None, object | None, object | None], settings: Settings, token: str
) -> AdminSession | None:
    """Return session data only when Redis values decode and match the current key marker."""
    marker, operator, csrf_token = (_redis_text(value) for value in values)
    if marker is None or operator is None or csrf_token is None:
        return None
    if not secrets.compare_digest(marker, admin_key_marker(settings)):
        return None
    return AdminSession(token=token, operator_name=operator, csrf_token=csrf_token)


async def require_admin_session(
    redis,
    settings: Settings,
    token: str | None,
    *,
    extend: bool,
) -> AdminSession:
    """Validate a browser session and optionally renew its idle timeout."""
    if not settings.admin_api_key:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="ADMIN_API_KEY is not configured.",
        )
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="login_required")

    values = await _session_values(redis, token)
    if _has_missing_session_value(values):
        await _reject_session(redis, token, "session_expired")

    session = _validated_session(values, settings, token)
    if session is None:
        await _reject_session(redis, token, "session_invalid")

    if extend:
        await _extend_session(redis, token)

    return session


def validate_admin_csrf(session: AdminSession, submitted: str | None) -> None:
    """Reject form submissions whose CSRF secret does not match the session."""
    if submitted and secrets.compare_digest(session.csrf_token, submitted):
        return
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="csrf_invalid")


async def set_flash(redis, session: AdminSession, category: str, message_key: str) -> None:
    """Store one localized post-redirect message alongside the authenticated session."""
    await redis.set(
        _key(session.token, "flash"),
        f"{category}:{message_key}",
        ex=SESSION_TTL_SECONDS,
    )


async def pop_flash(redis, session: AdminSession) -> tuple[str, str] | None:
    """Consume a one-time message so redirects do not replay stale operator feedback."""
    key = _key(session.token, "flash")
    value = _redis_text(await redis.get(key))
    if value is None:
        await redis.delete(key)
        return None
    await redis.delete(key)
    category, _, message_key = str(value).partition(":")
    return category, message_key
