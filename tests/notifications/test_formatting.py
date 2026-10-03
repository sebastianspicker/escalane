"""Tests for escalane.notifications.formatting."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from escalane.notifications.formatting import format_alarm_message
from tests.support.assertions import expect

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

    expect("NOTFALLALARM (silent)" in result)
    expect("Alarm-ID: abc-123" in result)
    expect("Person: Person X" in result)
    expect("Ort: Raum 1.23 / Standort BG" in result)
    expect("Zeit: 2025-06-15T14:30:00+00:00" in result)
    expect("Stufe: 2" in result)
    expect("Quittieren: http://localhost:8080/a/token123" in result)


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
    expect(len(lines) == 7)
    expect(lines[0] == "NOTFALLALARM (silent)")
    expect(lines[1].startswith("Alarm-ID:"))
    expect(lines[2].startswith("Person:"))
    expect(lines[3].startswith("Ort:"))
    expect(lines[4].startswith("Zeit:"))
    expect(lines[5].startswith("Stufe:"))
    expect(lines[6].startswith("Quittieren:"))


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

    expect("Ort: Raum 2.05" in result)
    expect(" / " not in result)


def test_format_alarm_message_site_empty_string_treated_as_truthy():
    """An empty string site is technically a truthy-check: verifying behavior."""
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
    expect("Ort: R300" in result)
    expect(" / " not in result)


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

    expect("Stufe: 0" in result)


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

    expect(isinstance(result, str))


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

    expect("Quittieren:" not in result)
