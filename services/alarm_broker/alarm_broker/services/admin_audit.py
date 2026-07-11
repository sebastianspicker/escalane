"""Redacted admin audit-event construction and persistence helpers."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from alarm_broker.db.models import AdminAuditEvent

REDACTED_VALUE = "[REDACTED]"
_SENSITIVE_KEY_PARTS = frozenset(
    {
        "address",
        "authorization",
        "cookie",
        "credential",
        "key",
        "password",
        "phone",
        "secret",
        "token",
    }
)


def _is_sensitive_key(key: object) -> bool:
    if not isinstance(key, str):
        return False
    words = re.findall(r"[a-z]+|[A-Z][a-z]*|\d+", key.replace("-", "_").replace(".", "_"))
    return any(word.casefold() in _SENSITIVE_KEY_PARTS for word in words)


def redact_sensitive_fields(value: Any) -> Any:
    """Return a copy of nested data with values under sensitive keys redacted."""
    if isinstance(value, Mapping):
        return {
            key: REDACTED_VALUE if _is_sensitive_key(key) else redact_sensitive_fields(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_sensitive_fields(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_sensitive_fields(item) for item in value)
    return value


def add_admin_audit_event(
    session: AsyncSession,
    *,
    operator_name: str,
    action: str,
    resource_type: str,
    resource_id: str,
    changed_fields: Mapping[str, Any] | None = None,
    request_id: str | None = None,
) -> AdminAuditEvent:
    """Add a redacted event to the current transaction without committing it."""
    event = AdminAuditEvent(
        operator_name=operator_name,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        changed_fields=redact_sensitive_fields(dict(changed_fields or {})),
        request_id=request_id,
    )
    session.add(event)
    return event
