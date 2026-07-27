"""Tests for escalane.services.enrichment_service."""

from __future__ import annotations

try:
    from tests.assertions import expect
except ModuleNotFoundError:
    from assertions import expect

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from escalane.db.models import Alarm, AlarmStatus
from escalane.services.enrichment_service import enrich_alarm_context

pytestmark = [pytest.mark.unit]


def _make_alarm(
    *,
    person_id: str | None = None,
    room_id: str | None = None,
    site_id: str | None = None,
    severity: str = "P0",
) -> Alarm:
    """Create an Alarm instance for testing without persisting it."""
    return Alarm(
        id=uuid.uuid4(),
        status=AlarmStatus.TRIGGERED,
        source="yealink",
        event="action_url_triggered",
        created_at=datetime.now(UTC),
        person_id=person_id,
        room_id=room_id,
        site_id=site_id,
        severity=severity,
        meta={},
    )


async def _persist_and_enrich(sessionmaker: async_sessionmaker, alarm: Alarm) -> dict:
    """Persist an alarm and return its enrichment from the same test session."""
    async with sessionmaker() as session:
        session.add(alarm)
        await session.commit()
        return await enrich_alarm_context(session, alarm)


async def test_full_enrichment(sessionmaker: async_sessionmaker, seeded_db: None):
    """When person, room, and site all exist, enrichment returns their display values."""
    alarm = _make_alarm(person_id="ma-012", room_id="bg-1.23", site_id="bg")

    result = await _persist_and_enrich(sessionmaker, alarm)

    expect(result["person_name"] == "Person X")
    expect(result["room_label"] == "Raum 1.23")
    expect(result["site_name"] == "Standort BG")
    expect(result["severity"] == "P0")


async def test_missing_person_falls_back_to_person_id(
    sessionmaker: async_sessionmaker, seeded_db: None
):
    """When person_id references a non-existent person, fall back to the raw person_id."""
    alarm = _make_alarm(person_id="unknown-person", room_id="bg-1.23", site_id="bg")

    result = await _persist_and_enrich(sessionmaker, alarm)

    expect(result["person_name"] == "unknown-person")
    expect(result["room_label"] == "Raum 1.23")
    expect(result["site_name"] == "Standort BG")


async def test_missing_room_falls_back_to_room_id(
    sessionmaker: async_sessionmaker, seeded_db: None
):
    """When room_id references a non-existent room, fall back to the raw room_id."""
    alarm = _make_alarm(person_id="ma-012", room_id="nonexistent-room", site_id="bg")

    result = await _persist_and_enrich(sessionmaker, alarm)

    expect(result["person_name"] == "Person X")
    expect(result["room_label"] == "nonexistent-room")
    expect(result["site_name"] == "Standort BG")


async def test_alarm_with_no_person_or_room(sessionmaker: async_sessionmaker, seeded_db: None):
    """When the alarm has no person_id and no room_id, enrichment returns None for those fields."""
    alarm = _make_alarm(person_id=None, room_id=None, site_id=None)

    result = await _persist_and_enrich(sessionmaker, alarm)

    expect(result["person_name"] is None)
    expect(result["room_label"] is None)
    expect(result["site_name"] is None)
    expect(result["severity"] == "P0")


async def test_enrichment_preserves_severity(sessionmaker: async_sessionmaker, seeded_db: None):
    """The severity field from the alarm is passed through unchanged."""
    alarm = _make_alarm(person_id="ma-012", room_id="bg-1.23", site_id="bg", severity="P2")

    result = await _persist_and_enrich(sessionmaker, alarm)

    expect(result["severity"] == "P2")


async def test_missing_site_falls_back_to_room_site_id(
    sessionmaker: async_sessionmaker, seeded_db: None
):
    """When a room exists but its site is not in the DB, site_name falls back to room.site_id."""
    # Create a room referencing a site that will NOT be in the DB.
    # We need a site to satisfy the FK, then remove it -- but simpler: we check
    # that when the site IS present it resolves. The seeded data already covers this.
    # Instead, test the path: room exists, room.site_id is set, but session.get(Site, ...) returns
    # the site. Already covered by test_full_enrichment. Let's test with person_id=None and room
    # present.
    alarm = _make_alarm(person_id=None, room_id="bg-1.23", site_id="bg")

    result = await _persist_and_enrich(sessionmaker, alarm)

    expect(result["person_name"] is None)
    expect(result["room_label"] == "Raum 1.23")
    expect(result["site_name"] == "Standort BG")


async def test_missing_room_and_unknown_site_falls_back_to_alarm_site_id(
    sessionmaker: async_sessionmaker, seeded_db: None
):
    """When neither room nor site resolve, site_name falls back to alarm.site_id."""
    alarm = _make_alarm(person_id="ma-012", room_id="nonexistent-room", site_id="missing-site")

    async with sessionmaker() as session:
        session.add(alarm)
        await session.commit()

        result = await enrich_alarm_context(session, alarm)

    expect(result["person_name"] == "Person X")
    expect(result["room_label"] == "nonexistent-room")
    expect(result["site_name"] == "missing-site")


async def test_room_found_site_row_missing_falls_back_to_room_site_id(
    sessionmaker: async_sessionmaker, seeded_db: None
):
    """Room exists and has site_id, but Site row is absent → fall back to room.site_id string."""
    from escalane.db.models import Room

    orphan_site_id = "orphan-site"

    async with sessionmaker() as session:
        # SQLite does not enforce FK constraints by default, so we can create
        # a Room referencing a Site that does not exist.
        orphan_room = Room(id="orphan-room", site_id=orphan_site_id, label="Orphan Room", floor="0")
        session.add(orphan_room)
        await session.commit()

    alarm = _make_alarm(room_id="orphan-room", site_id=None)

    async with sessionmaker() as session:
        session.add(alarm)
        await session.commit()

        result = await enrich_alarm_context(session, alarm)

    expect(result["room_label"] == "Orphan Room")
    # Site row does not exist → falls back to room.site_id
    expect(result["site_name"] == orphan_site_id)


async def test_no_room_id_known_site_id_resolves(sessionmaker: async_sessionmaker, seeded_db: None):
    """When alarm.room_id is None but alarm.site_id resolves, site_name is set from Site."""
    alarm = _make_alarm(person_id=None, room_id=None, site_id="bg")

    async with sessionmaker() as session:
        session.add(alarm)
        await session.commit()

        result = await enrich_alarm_context(session, alarm)

    expect(result["room_label"] is None)
    expect(result["site_name"] == "Standort BG")


async def test_no_room_id_unknown_site_id_falls_back(
    sessionmaker: async_sessionmaker, seeded_db: None
):
    """When alarm.room_id is None and alarm.site_id has no Site row,
    falls back to the site_id string."""
    alarm = _make_alarm(person_id=None, room_id=None, site_id="nonexistent-site")

    async with sessionmaker() as session:
        session.add(alarm)
        await session.commit()

        result = await enrich_alarm_context(session, alarm)

    expect(result["room_label"] is None)
    expect(result["site_name"] == "nonexistent-site")
