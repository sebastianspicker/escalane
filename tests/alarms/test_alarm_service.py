"""Tests for escalane.alarms.lifecycle."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from escalane.alarms.lifecycle import (
    acknowledge_alarm,
    get_alarm_by_ack_token,
    get_alarm_or_404,
    soft_delete_alarm,
    transition_alarm,
)
from escalane.config.errors import ConflictError, NotFoundError
from escalane.contracts.alarms import AlarmStatus
from escalane.persistence.models import Alarm, AlarmEventOutbox
from tests.support.assertions import expect
from tests.support.constants import ACK_FOUND_TOKEN, ACK_SOFT_DELETED_TOKEN
from tests.support.worker_task_helpers import load_alarm_notes, persist_alarm

pytestmark = [pytest.mark.unit]


_ACK_TOKEN_UNSET = object()


def _make_alarm(
    *,
    status: AlarmStatus = AlarmStatus.TRIGGERED,
    ack_token: str | None | object = _ACK_TOKEN_UNSET,
    severity: str = "P0",
) -> Alarm:
    """Create an Alarm instance for testing."""
    resolved_ack_token = ACK_FOUND_TOKEN if ack_token is _ACK_TOKEN_UNSET else ack_token
    return Alarm(
        id=uuid.uuid4(),
        status=status,
        source="yealink",
        event="action_url_triggered",
        created_at=datetime.now(UTC),
        severity=severity,
        ack_token=resolved_ack_token,
        meta={},
    )


async def _persist_alarm(sessionmaker: async_sessionmaker, alarm: Alarm) -> None:
    """Persist a test alarm before exercising a lifecycle operation."""
    await persist_alarm(sessionmaker, alarm)


# ── get_alarm_by_ack_token ─────────────────────────────────────────────


async def test_get_alarm_by_ack_token_found(sessionmaker: async_sessionmaker, engine):
    """Returns the alarm when the ack_token matches."""
    alarm = _make_alarm(ack_token=ACK_FOUND_TOKEN)

    await _persist_alarm(sessionmaker, alarm)

    async with sessionmaker() as session:
        result = await get_alarm_by_ack_token(session, ACK_FOUND_TOKEN)

    expect(result is not None)
    expect(result.id == alarm.id)
    expect(result.ack_token == ACK_FOUND_TOKEN)


async def test_get_alarm_by_ack_token_not_found(sessionmaker: async_sessionmaker, engine):
    """Returns None when no alarm matches the ack_token."""
    async with sessionmaker() as session:
        result = await get_alarm_by_ack_token(session, "does-not-exist")

    expect(result is None)


async def test_get_alarm_by_ack_token_soft_deleted_raises_not_found(
    sessionmaker: async_sessionmaker, engine
):
    """Raises NotFoundError when the alarm exists but has been soft-deleted."""
    alarm = _make_alarm(ack_token=ACK_SOFT_DELETED_TOKEN)
    alarm.deleted_at = datetime.now(UTC)

    await _persist_alarm(sessionmaker, alarm)

    async with sessionmaker() as session:
        with pytest.raises(NotFoundError):
            await get_alarm_by_ack_token(session, ACK_SOFT_DELETED_TOKEN)


# ── acknowledge_alarm ──────────────────────────────────────────────────


async def test_acknowledge_alarm_success(sessionmaker: async_sessionmaker, engine):
    """A TRIGGERED alarm can be acknowledged, setting status and timestamps."""
    alarm = _make_alarm(status=AlarmStatus.TRIGGERED)

    await _persist_alarm(sessionmaker, alarm)

    async with sessionmaker() as session:
        persisted = await session.get(Alarm, alarm.id)
        result = await acknowledge_alarm(session, persisted, acked_by="Tester", note="On my way")

    expect(result is True)

    async with sessionmaker() as session:
        updated = await session.get(Alarm, alarm.id)

    expect(updated.status == AlarmStatus.ACKNOWLEDGED)
    expect(updated.acked_at is not None)
    expect(updated.acked_by == "Tester")
    expect(updated.meta.get("ack_note") == "On my way")


async def test_acknowledge_alarm_already_acknowledged(sessionmaker: async_sessionmaker, engine):
    """Acknowledging an already-acknowledged alarm is an idempotent no-op."""
    alarm = _make_alarm(status=AlarmStatus.ACKNOWLEDGED)

    await _persist_alarm(sessionmaker, alarm)

    async with sessionmaker() as session:
        persisted = await session.get(Alarm, alarm.id)
        result = await acknowledge_alarm(session, persisted)

    expect(result is False)


async def test_stale_repeated_acknowledgement_reports_a_conflicting_winner(
    sessionmaker: async_sessionmaker, engine
):
    alarm = _make_alarm(status=AlarmStatus.ACKNOWLEDGED)
    await _persist_alarm(sessionmaker, alarm)

    async with sessionmaker() as stale_session, sessionmaker() as winning_session:
        stale_alarm = await stale_session.get(Alarm, alarm.id)
        winning_alarm = await winning_session.get(Alarm, alarm.id)
        assert await transition_alarm(
            winning_session, winning_alarm, target_status=AlarmStatus.RESOLVED
        )
        with pytest.raises(ConflictError, match="concurrently"):
            await acknowledge_alarm(stale_session, stale_alarm)


async def test_soft_delete_wins_over_stale_lifecycle_transition(
    sessionmaker: async_sessionmaker, engine
):
    """A lifecycle CAS cannot revive an alarm deleted by another session."""
    alarm = _make_alarm(status=AlarmStatus.TRIGGERED)
    await _persist_alarm(sessionmaker, alarm)

    async with sessionmaker() as deleting_session, sessionmaker() as stale_session:
        deleting_alarm = await deleting_session.get(Alarm, alarm.id)
        stale_alarm = await stale_session.get(Alarm, alarm.id)
        await soft_delete_alarm(deleting_session, deleting_alarm, deleted_by="operator-a")

        with pytest.raises(NotFoundError):
            await transition_alarm(
                stale_session,
                stale_alarm,
                target_status=AlarmStatus.RESOLVED,
                actor="operator-b",
            )

    async with sessionmaker() as session:
        persisted = await session.get(Alarm, alarm.id)
        expect(persisted is not None)
        expect(persisted.deleted_at is not None)
        expect(persisted.status == AlarmStatus.TRIGGERED)


async def test_stale_soft_delete_records_only_winner_note_and_discards_pending_outbox(
    sessionmaker: async_sessionmaker, engine
):
    """Concurrent deletes retain only the winner's actor/note in one transaction."""
    alarm = _make_alarm(status=AlarmStatus.TRIGGERED)
    async with sessionmaker() as session:
        session.add(alarm)
        session.add(
            AlarmEventOutbox(
                alarm_id=alarm.id,
                event_type="alarm.state_changed",
                payload={"new_state": "triggered"},
            )
        )
        await session.commit()

    async with sessionmaker() as winning_session, sessionmaker() as stale_session:
        winning_alarm = await winning_session.get(Alarm, alarm.id)
        stale_alarm = await stale_session.get(Alarm, alarm.id)
        await soft_delete_alarm(
            winning_session,
            winning_alarm,
            deleted_by="winner",
            note="duplicate alert",
        )
        with pytest.raises(ConflictError, match="already been deleted"):
            await soft_delete_alarm(
                stale_session,
                stale_alarm,
                deleted_by="loser",
                note="should not persist",
            )

    persisted, notes = await load_alarm_notes(sessionmaker, alarm.id)
    async with sessionmaker() as session:
        pending = list(
            (
                await session.scalars(
                    select(AlarmEventOutbox).where(AlarmEventOutbox.alarm_id == alarm.id)
                )
            ).all()
        )
    expect(persisted is not None)
    expect(persisted.deleted_by == "winner")
    expect(len(notes) == 1)
    expect(notes[0].created_by == "winner")
    expect(notes[0].note == "duplicate alert")
    expect(pending == [])


async def test_acknowledge_alarm_resolved_raises_conflict(sessionmaker: async_sessionmaker, engine):
    """Acknowledging a resolved alarm raises ConflictError."""
    alarm = _make_alarm(status=AlarmStatus.RESOLVED)

    async with sessionmaker() as session:
        session.add(alarm)
        await session.commit()

    async with sessionmaker() as session:
        persisted = await session.get(Alarm, alarm.id)
        with pytest.raises(ConflictError):
            await acknowledge_alarm(session, persisted)


async def test_acknowledge_alarm_without_note(sessionmaker: async_sessionmaker, engine):
    """Acknowledging without a note leaves meta without ack_note key."""
    alarm = _make_alarm(status=AlarmStatus.TRIGGERED)

    async with sessionmaker() as session:
        session.add(alarm)
        await session.commit()

    async with sessionmaker() as session:
        persisted = await session.get(Alarm, alarm.id)
        await acknowledge_alarm(session, persisted, acked_by="Tester")

    async with sessionmaker() as session:
        updated = await session.get(Alarm, alarm.id)

    expect(updated.status == AlarmStatus.ACKNOWLEDGED)
    expect("ack_note" not in updated.meta)


async def _apply_lifecycle_action(
    session,
    alarm: Alarm,
    action: str,
) -> bool:
    if action == "acknowledge":
        return await acknowledge_alarm(session, alarm, acked_by="ack-actor", note="ack note")
    if action == "resolve":
        return await transition_alarm(
            session,
            alarm,
            target_status=AlarmStatus.RESOLVED,
            actor="resolve-actor",
            note="resolve note",
        )
    return await transition_alarm(
        session,
        alarm,
        target_status=AlarmStatus.CANCELLED,
        actor="cancel-actor",
        note="cancel note",
    )


@pytest.mark.parametrize(
    ("winning_action", "losing_action", "expected_status", "winner_note", "loser_note"),
    [
        ("acknowledge", "resolve", AlarmStatus.ACKNOWLEDGED, "ack_note", "resolve_note"),
        ("resolve", "cancel", AlarmStatus.RESOLVED, "resolve_note", "cancel_note"),
        ("cancel", "acknowledge", AlarmStatus.CANCELLED, "cancel_note", "ack_note"),
    ],
)
async def test_lifecycle_transition_compare_and_set_rejects_stale_session(
    sessionmaker: async_sessionmaker,
    engine,
    winning_action: str,
    losing_action: str,
    expected_status: AlarmStatus,
    winner_note: str,
    loser_note: str,
):
    """Only one session loaded from TRIGGERED can persist its lifecycle transition."""
    alarm = _make_alarm(status=AlarmStatus.TRIGGERED)

    async with sessionmaker() as session:
        session.add(alarm)
        await session.commit()

    async with sessionmaker() as winning_session, sessionmaker() as losing_session:
        winning_alarm = await winning_session.get(Alarm, alarm.id)
        losing_alarm = await losing_session.get(Alarm, alarm.id)

        expect(winning_alarm.status == AlarmStatus.TRIGGERED)
        expect(losing_alarm.status == AlarmStatus.TRIGGERED)
        winner_changed = await _apply_lifecycle_action(
            winning_session, winning_alarm, winning_action
        )
        with pytest.raises(ConflictError, match="changed concurrently"):
            await _apply_lifecycle_action(losing_session, losing_alarm, losing_action)
        expect(winner_changed is True)

    async with sessionmaker() as session:
        persisted = await session.get(Alarm, alarm.id)

    expect(persisted.status == expected_status)
    expect(persisted.meta.get(winner_note) is not None)
    expect(loser_note not in persisted.meta)


async def test_lifecycle_note_merge_preserves_concurrent_metadata(
    sessionmaker: async_sessionmaker,
    engine,
):
    """A stale lifecycle object must not replace metadata committed by another writer."""
    alarm = _make_alarm(status=AlarmStatus.TRIGGERED)

    async with sessionmaker() as session:
        session.add(alarm)
        await session.commit()

    async with sessionmaker() as stale_session, sessionmaker() as metadata_session:
        stale_alarm = await stale_session.get(Alarm, alarm.id)
        current_alarm = await metadata_session.get(Alarm, alarm.id)
        current_alarm.meta = {"operator_context": {"source": "workflow"}}
        await metadata_session.commit()

        assert await acknowledge_alarm(
            stale_session,
            stale_alarm,
            acked_by="metadata-safe",
            note="preserve both",
        )

    async with sessionmaker() as session:
        persisted = await session.get(Alarm, alarm.id)

    expect(persisted.meta["ack_note"] == "preserve both")
    expect(persisted.meta["operator_context"]["source"] == "workflow")


# ── transition_alarm ───────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("initial", "target", "actor", "note", "timestamp_field", "note_key"),
    [
        (
            AlarmStatus.TRIGGERED,
            AlarmStatus.RESOLVED,
            "admin",
            "False alarm",
            "resolved_at",
            "resolve_note",
        ),
        (
            AlarmStatus.TRIGGERED,
            AlarmStatus.CANCELLED,
            "admin",
            "Test cancelled",
            "cancelled_at",
            "cancel_note",
        ),
        (AlarmStatus.ACKNOWLEDGED, AlarmStatus.RESOLVED, "nurse", None, None, None),
    ],
)
async def test_transition_valid_paths(
    sessionmaker: async_sessionmaker,
    engine,
    initial,
    target,
    actor,
    note,
    timestamp_field,
    note_key,
):
    """Valid lifecycle transitions set their state, actor, and supplied audit fields."""
    alarm = _make_alarm(status=initial)
    await _persist_alarm(sessionmaker, alarm)

    async with sessionmaker() as session:
        persisted = await session.get(Alarm, alarm.id)
        result = await transition_alarm(
            session, persisted, target_status=target, actor=actor, note=note
        )

    expect(result is True)
    async with sessionmaker() as session:
        updated = await session.get(Alarm, alarm.id)

    expect(updated.status == target)
    expect(getattr(updated, f"{target.value}_by") == actor)
    if timestamp_field is not None:
        expect(getattr(updated, timestamp_field) is not None)
    if note_key is not None:
        expect(updated.meta.get(note_key) == note)


async def test_transition_same_status_returns_false(sessionmaker: async_sessionmaker, engine):
    """Transitioning to the current status is a no-op, returning False."""
    alarm = _make_alarm(status=AlarmStatus.TRIGGERED)

    await _persist_alarm(sessionmaker, alarm)

    async with sessionmaker() as session:
        persisted = await session.get(Alarm, alarm.id)
        result = await transition_alarm(session, persisted, target_status=AlarmStatus.TRIGGERED)

    expect(result is False)


async def test_transition_invalid_raises_conflict(sessionmaker: async_sessionmaker, engine):
    """An invalid transition (e.g., RESOLVED -> TRIGGERED) raises ConflictError."""
    alarm = _make_alarm(status=AlarmStatus.RESOLVED)

    await _persist_alarm(sessionmaker, alarm)

    async with sessionmaker() as session:
        persisted = await session.get(Alarm, alarm.id)
        with pytest.raises(ConflictError, match="Invalid status transition"):
            await transition_alarm(session, persisted, target_status=AlarmStatus.TRIGGERED)


async def test_transition_cancelled_to_resolved_raises_conflict(
    sessionmaker: async_sessionmaker, engine
):
    """CANCELLED is a terminal state; cannot transition out of it."""
    alarm = _make_alarm(status=AlarmStatus.CANCELLED)

    await _persist_alarm(sessionmaker, alarm)

    async with sessionmaker() as session:
        persisted = await session.get(Alarm, alarm.id)
        with pytest.raises(ConflictError):
            await transition_alarm(session, persisted, target_status=AlarmStatus.RESOLVED)


# ── get_alarm_or_404 ──────────────────────────────────────────────────


async def test_get_alarm_or_404_found(sessionmaker: async_sessionmaker, engine):
    """Returns the alarm when it exists."""
    alarm = _make_alarm()

    async with sessionmaker() as session:
        session.add(alarm)
        await session.commit()

    async with sessionmaker() as session:
        result = await get_alarm_or_404(session, alarm.id)

    expect(result.id == alarm.id)
    expect(result.status == AlarmStatus.TRIGGERED)


async def test_get_alarm_or_404_not_found(sessionmaker: async_sessionmaker, engine):
    """Raises NotFoundError when the alarm does not exist."""
    missing_id = uuid.uuid4()

    async with sessionmaker() as session:
        with pytest.raises(NotFoundError, match=str(missing_id)):
            await get_alarm_or_404(session, missing_id)


async def test_get_alarm_or_404_with_uuid_object(sessionmaker: async_sessionmaker, engine):
    """get_alarm_or_404 works when given a uuid.UUID object."""
    alarm = _make_alarm()

    async with sessionmaker() as session:
        session.add(alarm)
        await session.commit()

    async with sessionmaker() as session:
        # Pass a freshly constructed UUID object (not the ORM-attached one)
        result = await get_alarm_or_404(session, uuid.UUID(str(alarm.id)))

    expect(result.id == alarm.id)
