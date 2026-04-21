"""Tests for alarm_broker.services.seed_service.parse_seed_payload."""

from __future__ import annotations

import pytest

from alarm_broker.core.errors import ValidationError
from alarm_broker.services.seed_service import _MAX_SEED_BYTES, parse_seed_payload

pytestmark = [pytest.mark.unit]


def test_parse_json_payload():
    result = parse_seed_payload("application/json", b'{"sites": []}')
    assert result == {"sites": []}


def test_parse_yaml_payload():
    result = parse_seed_payload("application/x-yaml", b"sites:\n  - id: bg\n")
    assert result == {"sites": [{"id": "bg"}]}


def test_parse_empty_json_returns_empty_dict():
    result = parse_seed_payload("application/json", b"")
    assert result == {}


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
