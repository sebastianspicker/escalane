"""Parse and apply operator-provided seed payloads."""

from __future__ import annotations

import json
from typing import Any

import yaml
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from escalane.core.errors import ConflictError, ValidationError
from escalane.seed import apply_seed
from escalane.settings import Settings

_YAML_TYPES = {
    "application/x-yaml",
    "application/yaml",
    "application/yml",
    "text/yaml",
    "text/x-yaml",
}


_MAX_SEED_BYTES = 1_048_576  # 1 MB
_MAX_SEED_DEPTH = 64
_MAX_SEED_NODES = 10_000


def _yaml_children(node: yaml.Node, depth: int) -> list[tuple[yaml.Node, int]]:
    """Return the next traversal entries for one composed YAML node."""
    next_depth = depth + 1
    if isinstance(node, yaml.SequenceNode):
        return [(child, next_depth) for child in node.value]
    if isinstance(node, yaml.MappingNode):
        return [(child, next_depth) for key_value in node.value for child in key_value]
    return []


def _validate_yaml_nodes(node: yaml.Node | None) -> None:
    """Reject aliases and bound the composed graph before safe construction."""
    if node is None:
        return
    seen: set[int] = set()
    stack: list[tuple[yaml.Node, int]] = [(node, 1)]
    while stack:
        current, depth = stack.pop()
        node_id = id(current)
        if node_id in seen:
            raise ValidationError("YAML aliases are not allowed in seed payloads")
        seen.add(node_id)
        if len(seen) > _MAX_SEED_NODES or depth > _MAX_SEED_DEPTH:
            raise ValidationError("Seed payload exceeds structural complexity limits")
        stack.extend(_yaml_children(current, depth))


def _validate_payload_complexity(data: Any) -> None:
    """Bound parsed JSON/YAML values even when the parser accepts them."""
    nodes = 0
    stack: list[tuple[Any, int]] = [(data, 1)]
    while stack:
        current, depth = stack.pop()
        nodes += 1
        if nodes > _MAX_SEED_NODES or depth > _MAX_SEED_DEPTH:
            raise ValidationError("Seed payload exceeds structural complexity limits")
        if isinstance(current, dict):
            for key, value in current.items():
                stack.append((key, depth + 1))
                stack.append((value, depth + 1))
        elif isinstance(current, list):
            stack.extend((value, depth + 1) for value in current)


def parse_seed_payload(content_type: str, raw: bytes) -> dict[str, Any]:
    """Parse a JSON or YAML seed payload after enforcing the size limit."""
    if len(raw) > _MAX_SEED_BYTES:
        raise ValidationError(
            f"Seed payload too large ({len(raw)} bytes). Maximum allowed: {_MAX_SEED_BYTES} bytes"
        )
    if content_type in _YAML_TYPES:
        try:
            _validate_yaml_nodes(yaml.compose(raw, Loader=yaml.SafeLoader))
            data = yaml.safe_load(raw) or {}
        except (RecursionError, yaml.YAMLError) as exc:
            raise ValidationError("Invalid YAML seed payload") from exc
    else:
        try:
            data = json.loads(raw or b"{}")
        except (json.JSONDecodeError, RecursionError, UnicodeDecodeError) as exc:
            raise ValidationError("Invalid JSON seed payload") from exc

    _validate_payload_complexity(data)

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
    except IntegrityError as exc:
        await session.rollback()
        raise ConflictError("Seed data conflicts with a concurrent or referenced resource") from exc
    except (KeyError, TypeError, ValueError) as exc:
        await session.rollback()
        raise ValidationError("Invalid seed structure or values") from exc
