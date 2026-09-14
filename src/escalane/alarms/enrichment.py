"""Resolve display context for alarm notifications and dashboards."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from escalane.contracts.notifications import EnrichedAlarmContext
from escalane.persistence.models import Alarm, Person, Room, Site


async def enrich_alarm_context(session: AsyncSession, alarm: Alarm) -> EnrichedAlarmContext:
    """Load human-readable person, room, and site labels for an alarm.

    Missing master-data rows fall back to the stored IDs so notification
    delivery can continue even if a room/person record was deleted or not yet
    seeded.
    """
    row = (
        await session.execute(
            select(Person.display_name, Room.label, Room.site_id, Site.name)
            .select_from(Alarm)
            .outerjoin(Person, Person.id == Alarm.person_id)
            .outerjoin(Room, Room.id == Alarm.room_id)
            .outerjoin(Site, Site.id == func.coalesce(Room.site_id, Alarm.site_id))
            .where(Alarm.id == alarm.id)
        )
    ).one_or_none()
    person_display, room_display, room_site_id, site_display = (
        row if row is not None else (None, None, None, None)
    )
    person_name = person_display if person_display is not None else alarm.person_id
    room_label = room_display if room_display is not None else alarm.room_id
    site_id = room_site_id if room_site_id is not None else alarm.site_id
    site_name = site_display if site_display is not None else site_id

    return EnrichedAlarmContext(
        person_name=person_name,
        room_label=room_label,
        site_name=site_name,
        severity=alarm.severity,
    )
