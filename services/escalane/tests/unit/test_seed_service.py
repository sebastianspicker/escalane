"""Tests for escalane.services.seed_service.parse_seed_payload."""

from __future__ import annotations

try:
    from tests.assertions import expect
except ModuleNotFoundError:
    from assertions import expect

import pytest

from escalane.core.errors import ValidationError
from escalane.seed import _expand_env
from escalane.services.seed_service import (
    _MAX_SEED_BYTES,
    _MAX_SEED_DEPTH,
    _MAX_SEED_NODES,
    parse_seed_payload,
)
from escalane.settings import Settings

pytestmark = [pytest.mark.unit]


def test_parse_json_payload():
    result = parse_seed_payload("application/json", b'{"sites": []}')
    expect(result == {"sites": []})


def test_parse_yaml_payload():
    result = parse_seed_payload("application/x-yaml", b"sites:\n  - id: bg\n")
    expect(result == {"sites": [{"id": "bg"}]})


def test_parse_empty_json_returns_empty_dict():
    result = parse_seed_payload("application/json", b"")
    expect(result == {})


def test_payload_too_large_raises_validation_error():
    oversized = b"x" * (_MAX_SEED_BYTES + 1)
    with pytest.raises(ValidationError, match="too large"):
        parse_seed_payload("application/json", oversized)


def test_invalid_json_raises_validation_error():
    with pytest.raises(ValidationError, match="Invalid JSON"):
        parse_seed_payload("application/json", b"{not json")


def test_invalid_yaml_raises_validation_error():
    with pytest.raises(ValidationError, match="Invalid YAML"):
        parse_seed_payload("application/x-yaml", b"key: [unclosed")


def test_json_non_dict_raises_validation_error():
    with pytest.raises(ValidationError, match="must be a JSON/YAML object"):
        parse_seed_payload("application/json", b"[1, 2, 3]")


def test_yaml_non_dict_raises_validation_error():
    with pytest.raises(ValidationError, match="must be a JSON/YAML object"):
        parse_seed_payload("application/x-yaml", b"- item1\n- item2\n")


def test_expand_env_resolves_only_documented_field_bound_placeholders() -> None:
    settings = Settings(
        yealink_device_token="device-token",
        signal_target_group_id="signal-group",
        escalate_t1=60,
        escalate_t2=180,
        escalate_t3=300,
    )

    result = _expand_env(
        {
            "devices": [{"device_token": "${YEALINK_DEVICE_TOKEN}"}],
            "escalation_targets": [{"channel": "signal", "address": "${SIGNAL_TARGET_GROUP_ID}"}],
            "escalation_steps": [
                {"after_seconds": "${ESCALATE_T1}"},
                {"after_seconds": "${ESCALATE_T2}"},
                {"after_seconds": "${ESCALATE_T3}"},
            ],
        },
        settings,
    )

    expect(result["devices"][0]["device_token"] == "device-token")
    expect(result["escalation_targets"][0]["address"] == "signal-group")
    expect([step["after_seconds"] for step in result["escalation_steps"]] == [60, 180, 300])


@pytest.mark.parametrize("name", ["ADMIN_API_KEY", "DATABASE_URL", "UNKNOWN_VALUE"])
def test_expand_env_rejects_arbitrary_placeholder_names(name: str) -> None:
    with pytest.raises(ValidationError, match="unknown|Unknown|not allowed"):
        _expand_env({"sites": [{"name": f"${{{name}}}"}]}, Settings())


@pytest.mark.parametrize(
    "payload",
    [
        {"sites": [{"name": "${YEALINK_DEVICE_TOKEN}"}]},
        {"devices": [{"device_token": "${SIGNAL_TARGET_GROUP_ID}"}]},
        {"escalation_targets": [{"channel": "sms", "address": "${SIGNAL_TARGET_GROUP_ID}"}]},
        {"escalation_steps": [{"after_seconds": "${YEALINK_DEVICE_TOKEN}"}]},
    ],
)
def test_expand_env_rejects_misplaced_allowed_placeholders(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError, match="placeholder|Signal"):
        _expand_env(payload, Settings(yealink_device_token="device-token"))


@pytest.mark.parametrize(
    "payload",
    [
        {"devices": [{"device_token": "${YEALINK_DEVICE_TOKEN}"}]},
        {"escalation_targets": [{"channel": "signal", "address": "${SIGNAL_TARGET_GROUP_ID}"}]},
    ],
)
def test_expand_env_rejects_unresolved_allowed_placeholders(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError, match="unresolved"):
        _expand_env(payload, Settings())


def test_expand_env_preserves_plain_values() -> None:
    raw = {"devices": [{"device_token": "literal-token"}], "sites": [{"name": "literal"}]}

    expect(_expand_env(raw, Settings()) == raw)


@pytest.mark.parametrize(
    "payload",
    [
        b"base: &base {id: one}\nsites: [*base]\n",
        b"loop: &loop [*loop]\n",
    ],
)
def test_parse_yaml_rejects_aliases_and_cycles(payload: bytes) -> None:
    with pytest.raises(ValidationError, match="aliases"):
        parse_seed_payload("application/x-yaml", payload)


@pytest.mark.parametrize(
    ("content_type", "payload"),
    [
        (
            "application/json",
            ("[" * (_MAX_SEED_DEPTH + 1) + "0" + "]" * (_MAX_SEED_DEPTH + 1)).encode(),
        ),
        ("application/x-yaml", ("- " * (_MAX_SEED_DEPTH + 1) + "value\n").encode()),
        ("application/json", ("[0," * _MAX_SEED_NODES + "0]" * _MAX_SEED_NODES).encode()),
    ],
)
def test_parse_payload_rejects_excessive_depth_or_nodes(content_type: str, payload: bytes) -> None:
    with pytest.raises(ValidationError, match="complexity|Invalid"):
        parse_seed_payload(content_type, payload)
