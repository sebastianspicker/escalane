from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from alarm_broker.core.errors import ConflictError, NotFoundError
from alarm_broker.db.models import Alarm, AlarmStatus

_ALLOWED_TRANSITIONS: dict[AlarmStatus, set[AlarmStatus]] = {
    AlarmStatus.TRIGGERED: {
        AlarmStatus.ACKNOWLEDGED,
        AlarmStatus.RESOLVED,
        AlarmStatus.CANCELLED,
    },
    AlarmStatus.ACKNOWLEDGED: {
        AlarmStatus.RESOLVED,
        AlarmStatus.CANCELLED,
    },
    AlarmStatus.RESOLVED: set(),
    AlarmStatus.CANCELLED: set(),
}


def _merge_meta_note(alarm: Alarm, key: str, note: str | None) -> None:
    # LIMITATION: This overwrites any previous value for the same key in alarm.meta,
    # so only the latest note per key is retained. For full note history, migrate
    # to the AlarmNote table which preserves all entries as separate rows.
    if note:
        alarm.meta = {**(alarm.meta or {}), key: note}


async def get_alarm_by_ack_token(session: AsyncSession, ack_token: str) -> Alarm | None:
    result: Alarm | None = await session.scalar(select(Alarm).where(Alarm.ack_token == ack_token))
    if result and result.deleted_at is not None:
        raise NotFoundError("alarm")
    return result


async def acknowledge_alarm(
    session: AsyncSession,
    alarm: Alarm,
    *,
    acked_by: str | None = None,
    note: str | None = None,
) -> bool:
    if alarm.status != AlarmStatus.TRIGGERED:
        raise ConflictError(
            f"Cannot acknowledge alarm in {alarm.status.value} status",
            details={
                "current_status": alarm.status.value,
                "expected_status": AlarmStatus.TRIGGERED.value,
            },
        )

    alarm.status = AlarmStatus.ACKNOWLEDGED
    alarm.acked_at = datetime.now(UTC)
    alarm.acked_by = acked_by
    _merge_meta_note(alarm, "ack_note", note)
    await session.commit()
    return True


async def transition_alarm(
    session: AsyncSession,
    alarm: Alarm,
    *,
    target_status: AlarmStatus,
    actor: str | None = None,
    note: str | None = None,
) -> bool:
    current = alarm.status
    if current == target_status:
        return False

    allowed = _ALLOWED_TRANSITIONS.get(current, set())
    if target_status not in allowed:
        raise ConflictError(
            f"Invalid status transition: {current.value} -> {target_status.value}",
        )

    now = datetime.now(UTC)
    alarm.status = target_status

    if target_status == AlarmStatus.RESOLVED:
        alarm.resolved_at = now
        alarm.resolved_by = actor
        _merge_meta_note(alarm, "resolve_note", note)
    elif target_status == AlarmStatus.CANCELLED:
        alarm.cancelled_at = now
        alarm.cancelled_by = actor
        _merge_meta_note(alarm, "cancel_note", note)

    await session.commit()
    return True


async def get_alarm_or_404(session: AsyncSession, alarm_id: uuid.UUID | str) -> Alarm:
    alarm = await session.get(Alarm, alarm_id)
    if not alarm:
        raise NotFoundError(f"Alarm {alarm_id} not found")
    if alarm and alarm.deleted_at is not None:
        raise NotFoundError("alarm")
    return alarm
