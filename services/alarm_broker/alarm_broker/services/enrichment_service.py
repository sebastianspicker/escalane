from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from alarm_broker.db.models import Alarm, Person, Room, Site
from alarm_broker.types import EnrichedAlarmContext


async def enrich_alarm_context(session: AsyncSession, alarm: Alarm) -> EnrichedAlarmContext:
    person_name: str | None = alarm.person_id
    room_label: str | None = alarm.room_id
    site_name: str | None = alarm.site_id

    if alarm.person_id:
        person = await session.get(Person, alarm.person_id)
        if person:
            person_name = person.display_name
    if alarm.room_id:
        room = await session.get(Room, alarm.room_id)
        if room:
            room_label = room.label
            if room.site_id:
                site = await session.get(Site, room.site_id)
                if site:
                    site_name = site.name
                else:
                    site_name = room.site_id
        elif alarm.site_id:
            site = await session.get(Site, alarm.site_id)
            if site:
                site_name = site.name
    elif alarm.site_id:
        site = await session.get(Site, alarm.site_id)
        if site:
            site_name = site.name

    return EnrichedAlarmContext(
        person_name=person_name,
        room_label=room_label,
        site_name=site_name,
        severity=alarm.severity,
    )
