"""Parse and apply operator-provided seed payloads."""

from __future__ import annotations

import json
from typing import Any

import yaml
from sqlalchemy.ext.asyncio import AsyncSession

from alarm_broker.core.errors import ValidationError
from alarm_broker.seed import apply_seed
from alarm_broker.settings import Settings

_YAML_TYPES = {
    "application/x-yaml",
    "application/yaml",
    "application/yml",
    "text/yaml",
    "text/x-yaml",
}


_MAX_SEED_BYTES = 1_048_576  # 1 MB


def parse_seed_payload(content_type: str, raw: bytes) -> dict[str, Any]:
    """Parse a JSON or YAML seed payload after enforcing the size limit."""
    if len(raw) > _MAX_SEED_BYTES:
        raise ValidationError(
            f"Seed payload too large ({len(raw)} bytes). Maximum allowed: {_MAX_SEED_BYTES} bytes"
        )
    if content_type in _YAML_TYPES:
        try:
            data = yaml.safe_load(raw) or {}
        except yaml.YAMLError as exc:
            raise ValidationError("Invalid YAML seed payload") from exc
    else:
        try:
            data = json.loads(raw or b"{}")
        except json.JSONDecodeError as exc:
            raise ValidationError("Invalid JSON seed payload") from exc

    if not isinstance(data, dict):
        raise ValidationError("Seed payload must be a JSON/YAML object")

    return data


async def apply_seed_payload(
    session: AsyncSession,
    *,
    data: dict[str, Any],
    settings: Settings,
) -> None:
    """Apply parsed seed data and translate structure errors to domain validation."""
    try:
        await apply_seed(session, data, settings)
    except (KeyError, TypeError, ValueError) as exc:
        await session.rollback()
        raise ValidationError("Invalid seed structure or values") from exc
