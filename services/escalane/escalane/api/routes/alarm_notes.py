"""Expose authenticated creation and retrieval of operator alarm notes."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Header, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from escalane.api.deps import get_session, require_admin
from escalane.api.schemas import AlarmNoteIn, AlarmNoteOut
from escalane.db.models import AlarmNote
from escalane.services.alarm_service import get_alarm_or_404

router = APIRouter(prefix="/v1/alarms", dependencies=[Depends(require_admin)])


@router.get("/{alarm_id}/notes", response_model=list[AlarmNoteOut])
async def list_alarm_notes(
    alarm_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
) -> list[AlarmNoteOut]:
    """List all notes for an alarm."""
    await get_alarm_or_404(session, alarm_id)

    notes = (
        await session.scalars(
            select(AlarmNote)
            .where(AlarmNote.alarm_id == alarm_id)
            .order_by(AlarmNote.created_at.asc())
        )
    ).all()

    return [AlarmNoteOut.model_validate(note, from_attributes=True) for note in notes]


@router.post("/{alarm_id}/notes", response_model=AlarmNoteOut, status_code=status.HTTP_201_CREATED)
async def create_alarm_note(
    alarm_id: uuid.UUID,
    body: AlarmNoteIn,
    request: Request,
    x_admin_email: str | None = Header(default=None, alias="X-Admin-Email"),
    session: AsyncSession = Depends(get_session),
) -> AlarmNoteOut:
    """Create a note for an alarm."""
    alarm = await get_alarm_or_404(session, alarm_id)
    request.state.alarm_id = str(alarm.id)
    created_by = body.created_by or x_admin_email or "admin"

    note = AlarmNote(
        alarm_id=alarm.id,
        note=body.note,
        created_by=created_by,
        note_type="manual",
    )
    session.add(note)
    await session.commit()
    await session.refresh(note)

    return AlarmNoteOut.model_validate(note, from_attributes=True)
