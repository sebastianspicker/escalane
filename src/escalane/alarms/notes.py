"""Append and read attributed operator notes on alarms."""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from escalane.persistence.models import Alarm, AlarmNote


async def add_alarm_note(
    session: AsyncSession,
    alarm: Alarm,
    *,
    note: str,
    created_by: str | None,
    note_type: str = "manual",
) -> AlarmNote:
    """Persist one note without changing lifecycle state and return the stored row."""
    record = AlarmNote(
        alarm_id=alarm.id,
        note=note,
        created_by=created_by,
        note_type=note_type,
    )
    session.add(record)
    await session.commit()
    await session.refresh(record)
    return record


async def list_alarm_notes(session: AsyncSession, alarm_id: uuid.UUID) -> Sequence[AlarmNote]:
    """Return all notes for one alarm, oldest first."""
    return (
        await session.scalars(
            select(AlarmNote)
            .where(AlarmNote.alarm_id == alarm_id)
            .order_by(AlarmNote.created_at.asc())
        )
    ).all()
