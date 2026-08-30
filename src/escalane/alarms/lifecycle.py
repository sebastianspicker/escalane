"""Apply concurrency-safe alarm lifecycle mutations and durable event writes."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

from sqlalchemy import delete, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from escalane.alarms.outbox import (
    dispatch_pending_alarm_events,
    has_pending_alarm_events,
)
from escalane.config import constants
from escalane.config.errors import ConflictError, NotFoundError
from escalane.contracts.alarms import AlarmStatus
from escalane.persistence.json_merge import merge_json_object
from escalane.persistence.models import Alarm, AlarmEventOutbox, AlarmNote

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


@dataclass(frozen=True)
class AlarmStateOutcome:
    """Result of one state mutation plus durable event dispatch."""

    changed: bool
    published: int
    pending: bool


@dataclass(frozen=True, slots=True)
class AlarmPatchCommand:
    """Describe mutable alarm fields supplied by an inbound adapter."""

    title: str | None = None
    description: str | None = None
    severity: str | None = None
    tags: tuple[str, ...] | None = None


def _meta_note_value(
    session: AsyncSession,
    key: str,
    note: str | None,
) -> object | None:
    if not note:
        return None
    return merge_json_object(
        Alarm.meta,
        {key: note},
        dialect_name=session.get_bind().dialect.name,
    )


async def _resolve_compare_and_set_loss(
    session: AsyncSession,
    alarm: Alarm,
    *,
    target_status: AlarmStatus,
) -> bool:
    """Reload a lost CAS and distinguish idempotency from a conflicting winner."""
    await session.commit()
    await session.refresh(alarm)
    if alarm.deleted_at is not None:
        raise NotFoundError("alarm")
    if alarm.status == target_status:
        return False
    raise ConflictError(
        f"Alarm state changed concurrently to {alarm.status.value}",
        details={
            "current_status": alarm.status.value,
            "requested_status": target_status.value,
        },
    )


async def _set_alarm_state(
    session: AsyncSession,
    alarm: Alarm,
    *,
    current_status: AlarmStatus,
    target_status: AlarmStatus,
    values: dict[str, object],
) -> bool:
    result = await session.execute(
        update(Alarm)
        .where(
            Alarm.id == alarm.id,
            Alarm.status == current_status,
            Alarm.deleted_at.is_(None),
        )
        .values(values)
        .execution_options(synchronize_session=False)
    )
    if cast(CursorResult[Any], result).rowcount == 1:
        return True
    return await _resolve_compare_and_set_loss(session, alarm, target_status=target_status)


def _outbox_event(
    alarm: Alarm, event_type: str, *, sequence: int = 0, **payload: object
) -> AlarmEventOutbox:
    return AlarmEventOutbox(
        alarm_id=alarm.id,
        event_type=event_type,
        payload=dict(payload),
        sequence=sequence,
    )


def _acknowledgement_events(
    alarm: Alarm, acked_by: str | None, note: str | None
) -> list[AlarmEventOutbox]:
    return [
        _outbox_event(
            alarm,
            constants.EVENT_ALARM_ACKNOWLEDGED,
            acknowledged_by=acked_by or "unknown",
            note=note,
        ),
        _outbox_event(
            alarm,
            constants.EVENT_ALARM_STATE_CHANGED,
            sequence=1,
            old_state=AlarmStatus.TRIGGERED.value,
            new_state=AlarmStatus.ACKNOWLEDGED.value,
        ),
    ]


def _transition_values(
    session: AsyncSession,
    target_status: AlarmStatus,
    actor: str | None,
    note: str | None,
) -> dict[str, object]:
    values: dict[str, object] = {"status": target_status}
    note_keys = {
        AlarmStatus.RESOLVED: ("resolved_at", "resolved_by", "resolve_note"),
        AlarmStatus.CANCELLED: ("cancelled_at", "cancelled_by", "cancel_note"),
    }
    fields = note_keys.get(target_status)
    if fields is None:
        return values
    timestamp_key, actor_key, note_key = fields
    values.update({timestamp_key: datetime.now(UTC), actor_key: actor})
    meta_value = _meta_note_value(session, note_key, note)
    if meta_value is not None:
        values["meta"] = meta_value
    return values


async def get_alarm_by_ack_token(session: AsyncSession, ack_token: str) -> Alarm | None:
    """Resolve a capability token while hiding soft-deleted alarms."""
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
    """Acknowledge a triggered alarm exactly once and persist its outbox events."""
    if alarm.status == AlarmStatus.ACKNOWLEDGED:
        return await _resolve_compare_and_set_loss(
            session,
            alarm,
            target_status=AlarmStatus.ACKNOWLEDGED,
        )
    if alarm.status != AlarmStatus.TRIGGERED:
        raise ConflictError(
            f"Cannot acknowledge alarm in {alarm.status.value} status",
            details={
                "current_status": alarm.status.value,
                "expected_status": AlarmStatus.TRIGGERED.value,
            },
        )

    values: dict[str, object] = {
        "status": AlarmStatus.ACKNOWLEDGED,
        "acked_at": datetime.now(UTC),
        "acked_by": acked_by,
    }
    meta_value = _meta_note_value(session, "ack_note", note)
    if meta_value is not None:
        values["meta"] = meta_value

    if not await _set_alarm_state(
        session,
        alarm,
        current_status=AlarmStatus.TRIGGERED,
        target_status=AlarmStatus.ACKNOWLEDGED,
        values=values,
    ):
        return False

    session.add_all(_acknowledgement_events(alarm, acked_by, note))
    await session.commit()
    await session.refresh(alarm)
    return True


async def transition_alarm(
    session: AsyncSession,
    alarm: Alarm,
    *,
    target_status: AlarmStatus,
    actor: str | None = None,
    note: str | None = None,
) -> bool:
    """Apply an allowed terminal transition with compare-and-set semantics."""
    current = alarm.status
    if current == target_status:
        return await _resolve_compare_and_set_loss(
            session,
            alarm,
            target_status=target_status,
        )

    allowed = _ALLOWED_TRANSITIONS.get(current, set())
    if target_status not in allowed:
        raise ConflictError(
            f"Invalid status transition: {current.value} -> {target_status.value}",
        )

    values = _transition_values(session, target_status, actor, note)

    if not await _set_alarm_state(
        session,
        alarm,
        current_status=current,
        target_status=target_status,
        values=values,
    ):
        return False

    session.add(
        _outbox_event(
            alarm,
            constants.EVENT_ALARM_STATE_CHANGED,
            old_state=current.value,
            new_state=target_status.value,
        )
    )
    await session.commit()
    await session.refresh(alarm)
    return True


async def apply_alarm_state_change(
    session: AsyncSession,
    redis: Any,
    alarm: Alarm,
    *,
    target_status: AlarmStatus,
    actor: str | None = None,
    note: str | None = None,
    logger: logging.Logger,
) -> AlarmStateOutcome:
    """Apply an alarm state command and dispatch its durable lifecycle events."""
    if target_status == AlarmStatus.ACKNOWLEDGED:
        changed = await acknowledge_alarm(
            session,
            alarm,
            acked_by=actor,
            note=note,
        )
    else:
        changed = await transition_alarm(
            session,
            alarm,
            target_status=target_status,
            actor=actor,
            note=note,
        )

    published = await dispatch_pending_alarm_events(
        session,
        redis,
        logger=logger,
        alarm_id=alarm.id,
    )
    pending = await has_pending_alarm_events(session, alarm.id)
    return AlarmStateOutcome(changed=changed, published=published, pending=pending)


async def apply_alarm_patch(
    session: AsyncSession,
    alarm: Alarm,
    command: AlarmPatchCommand,
) -> None:
    """Apply supported metadata and severity changes to one active alarm."""
    values: dict[str, object] = {}
    meta_patch: dict[str, object] = {}
    if command.title is not None:
        meta_patch["title"] = command.title
    if command.description is not None:
        meta_patch["description"] = command.description
    if command.tags is not None:
        meta_patch["tags"] = list(command.tags)
    if meta_patch:
        values["meta"] = merge_json_object(
            Alarm.meta,
            meta_patch,
            dialect_name=session.get_bind().dialect.name,
        )
    if command.severity is not None:
        values["severity"] = command.severity

    if values:
        await session.execute(
            update(Alarm)
            .where(Alarm.id == alarm.id, Alarm.deleted_at.is_(None))
            .values(values)
            .execution_options(synchronize_session=False)
        )

    await session.commit()
    await session.refresh(alarm)


async def soft_delete_alarm(
    session: AsyncSession,
    alarm: Alarm,
    *,
    deleted_by: str | None = None,
    note: str | None = None,
) -> None:
    """Soft-delete an alarm exactly once and discard undelivered lifecycle events.

    The conditional update makes a stale delete lose cleanly to another delete.
    The browser delete note and outbox cleanup share the same commit with the
    winning delete; lifecycle writes that start after deletion fail their CAS.
    """
    result = await session.execute(
        update(Alarm)
        .where(Alarm.id == alarm.id, Alarm.deleted_at.is_(None))
        .values(deleted_at=datetime.now(UTC), deleted_by=deleted_by)
        .execution_options(synchronize_session=False)
    )
    if cast(CursorResult[Any], result).rowcount != 1:
        current = await session.execute(
            select(Alarm.id, Alarm.deleted_at).where(Alarm.id == alarm.id)
        )
        if current.one_or_none() is None:
            raise NotFoundError("Alarm", str(alarm.id))
        raise ConflictError("Alarm has already been deleted. No action taken.")

    if note:
        session.add(
            AlarmNote(
                alarm_id=alarm.id,
                note=note,
                created_by=deleted_by,
                note_type="delete",
            )
        )

    await session.execute(
        delete(AlarmEventOutbox).where(
            AlarmEventOutbox.alarm_id == alarm.id,
            AlarmEventOutbox.published_at.is_(None),
        )
    )
    await session.commit()
    await session.refresh(alarm)


async def get_alarm_or_404(session: AsyncSession, alarm_id: uuid.UUID | str) -> Alarm:
    """Load an active alarm or raise the domain not-found error used by APIs."""
    alarm = await session.get(Alarm, alarm_id)
    if not alarm:
        raise NotFoundError("Alarm", str(alarm_id))
    if alarm and alarm.deleted_at is not None:
        raise NotFoundError("alarm")
    return alarm
