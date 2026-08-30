"""Resolve display context for alarm notifications and dashboards."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from escalane.contracts.notifications import EnrichedAlarmContext
from escalane.persistence.models import Alarm, Person, Room, Site


async def _person_display_name(session: AsyncSession, person_id: str | None) -> str | None:
    """Resolve a person label while retaining the stored ID if master data was removed."""
    if not person_id:
        return None
    person = await session.get(Person, person_id)
    return person.display_name if person else person_id


async def _site_name(session: AsyncSession, site_id: str | None) -> str | None:
    """Resolve a site label while retaining the stored ID if master data was removed."""
    if not site_id:
        return None
    site = await session.get(Site, site_id)
    return site.name if site else site_id


async def _room_and_site_labels(
    session: AsyncSession,
    *,
    room_id: str | None,
    fallback_site_id: str | None,
) -> tuple[str | None, str | None]:
    """Resolve room and site labels with fallback IDs for predictable delivery output."""
    if not room_id:
        return None, await _site_name(session, fallback_site_id)

    room = await session.get(Room, room_id)
    if not room:
        return room_id, await _site_name(session, fallback_site_id)
    site_name = await _site_name(session, room.site_id) if room.site_id else fallback_site_id
    return room.label, site_name


async def enrich_alarm_context(session: AsyncSession, alarm: Alarm) -> EnrichedAlarmContext:
    """Load human-readable person, room, and site labels for an alarm.

    Missing master-data rows fall back to the stored IDs so notification
    delivery can continue even if a room/person record was deleted or not yet
    seeded.
    """
    person_name = await _person_display_name(session, alarm.person_id)
    room_label, site_name = await _room_and_site_labels(
        session,
        room_id=alarm.room_id,
        fallback_site_id=alarm.site_id,
    )

    return EnrichedAlarmContext(
        person_name=person_name,
        room_label=room_label,
        site_name=site_name,
        severity=alarm.severity,
    )
