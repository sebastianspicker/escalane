"""Tests for alarm_broker.worker.message."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from alarm_broker.worker.message import format_alarm_message

pytestmark = [pytest.mark.unit]


# ── format_alarm_message: all fields ─────────────────────────────────


def test_format_alarm_message_all_fields():
    """Message includes all fields when site is provided."""
    created = datetime(2025, 6, 15, 14, 30, 0, tzinfo=UTC)
    result = format_alarm_message(
        alarm_id="abc-123",
        person="Person X",
        room="Raum 1.23",
        site="Standort BG",
        created_at=created,
        ack_url="http://localhost:8080/a/token123",
        step_no=2,
    )

    assert "NOTFALLALARM (silent)" in result
    assert "Alarm-ID: abc-123" in result
    assert "Person: Person X" in result
    assert "Ort: Raum 1.23 / Standort BG" in result
    assert "Zeit: 2025-06-15T14:30:00+00:00" in result
    assert "Stufe: 2" in result
    assert "Quittieren: http://localhost:8080/a/token123" in result


def test_format_alarm_message_all_fields_line_order():
    """Lines appear in the expected order."""
    created = datetime(2025, 1, 1, 0, 0, 0, tzinfo=UTC)
    result = format_alarm_message(
        alarm_id="id-1",
        person="Nurse A",
        room="R101",
        site="Site Alpha",
        created_at=created,
        ack_url="http://example.com/ack",
        step_no=1,
    )

    lines = result.strip().split("\n")
    assert len(lines) == 7
    assert lines[0] == "NOTFALLALARM (silent)"
    assert lines[1].startswith("Alarm-ID:")
    assert lines[2].startswith("Person:")
    assert lines[3].startswith("Ort:")
    assert lines[4].startswith("Zeit:")
    assert lines[5].startswith("Stufe:")
    assert lines[6].startswith("Quittieren:")


# ── format_alarm_message: optional site missing ─────────────────────


def test_format_alarm_message_site_none():
    """When site is None, Ort line shows only the room without a slash separator."""
    created = datetime(2025, 3, 20, 10, 0, 0, tzinfo=UTC)
    result = format_alarm_message(
        alarm_id="id-no-site",
        person="Doctor B",
        room="Raum 2.05",
        site=None,
        created_at=created,
        ack_url="http://localhost/ack",
        step_no=1,
    )

    assert "Ort: Raum 2.05" in result
    assert " / " not in result


def test_format_alarm_message_site_empty_string_treated_as_truthy():
    """An empty string site is technically truthy-check — verifying behavior."""
    created = datetime(2025, 3, 20, 10, 0, 0, tzinfo=UTC)
    # Empty string is falsy in Python, so it should behave like None
    result = format_alarm_message(
        alarm_id="id-empty-site",
        person="Nurse C",
        room="R300",
        site="",
        created_at=created,
        ack_url="http://localhost/ack",
        step_no=3,
    )

    # Empty string is falsy, so the " / " separator should not appear
    assert "Ort: R300" in result
    assert " / " not in result


def test_format_alarm_message_step_zero():
    """Step 0 is rendered correctly."""
    created = datetime(2025, 6, 1, 8, 0, 0, tzinfo=UTC)
    result = format_alarm_message(
        alarm_id="id-step0",
        person="Person Y",
        room="Room A",
        site="Site B",
        created_at=created,
        ack_url="http://localhost/ack",
        step_no=0,
    )

    assert "Stufe: 0" in result


def test_format_alarm_message_returns_string():
    """format_alarm_message returns a plain string."""
    created = datetime(2025, 1, 1, 0, 0, 0, tzinfo=UTC)
    result = format_alarm_message(
        alarm_id="type-check",
        person="P",
        room="R",
        site=None,
        created_at=created,
        ack_url="http://x",
        step_no=1,
    )

    assert isinstance(result, str)


def test_format_alarm_message_omits_ack_line_when_missing():
    """When no ACK URL exists, the message omits the quittieren line."""
    created = datetime(2025, 1, 1, 0, 0, 0, tzinfo=UTC)
    result = format_alarm_message(
        alarm_id="no-ack-url",
        person="P",
        room="R",
        site=None,
        created_at=created,
        ack_url=None,
        step_no=1,
    )

    assert "Quittieren:" not in result
