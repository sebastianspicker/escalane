"""Tests for alarm_broker.services.alarm_service."""

from __future__ import annotations

try:
    from tests.assertions import expect
except ModuleNotFoundError:
    from assertions import expect

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from alarm_broker.core.errors import ConflictError, NotFoundError
from alarm_broker.db.models import Alarm, AlarmStatus
from alarm_broker.services.alarm_service import (
    acknowledge_alarm,
    get_alarm_by_ack_token,
    get_alarm_or_404,
    transition_alarm,
)

try:
    from tests.constants import ACK_FOUND_TOKEN, ACK_SOFT_DELETED_TOKEN
except ModuleNotFoundError:
    from constants import ACK_FOUND_TOKEN, ACK_SOFT_DELETED_TOKEN

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


# ── get_alarm_by_ack_token ─────────────────────────────────────────────


async def test_get_alarm_by_ack_token_found(sessionmaker: async_sessionmaker, engine):
    """Returns the alarm when the ack_token matches."""
    alarm = _make_alarm(ack_token=ACK_FOUND_TOKEN)

    async with sessionmaker() as session:
        session.add(alarm)
        await session.commit()

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

    async with sessionmaker() as session:
        session.add(alarm)
        await session.commit()

    async with sessionmaker() as session:
        with pytest.raises(NotFoundError):
            await get_alarm_by_ack_token(session, ACK_SOFT_DELETED_TOKEN)


# ── acknowledge_alarm ──────────────────────────────────────────────────


async def test_acknowledge_alarm_success(sessionmaker: async_sessionmaker, engine):
    """A TRIGGERED alarm can be acknowledged, setting status and timestamps."""
    alarm = _make_alarm(status=AlarmStatus.TRIGGERED)

    async with sessionmaker() as session:
        session.add(alarm)
        await session.commit()

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
    """Acknowledging an already-acknowledged alarm raises ConflictError."""
    alarm = _make_alarm(status=AlarmStatus.ACKNOWLEDGED)

    async with sessionmaker() as session:
        session.add(alarm)
        await session.commit()

    async with sessionmaker() as session:
        persisted = await session.get(Alarm, alarm.id)
        with pytest.raises(ConflictError, match="Cannot acknowledge"):
            await acknowledge_alarm(session, persisted)


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


# ── transition_alarm ───────────────────────────────────────────────────


async def test_transition_triggered_to_resolved(sessionmaker: async_sessionmaker, engine):
    """TRIGGERED -> RESOLVED is a valid transition."""
    alarm = _make_alarm(status=AlarmStatus.TRIGGERED)

    async with sessionmaker() as session:
        session.add(alarm)
        await session.commit()

    async with sessionmaker() as session:
        persisted = await session.get(Alarm, alarm.id)
        result = await transition_alarm(
            session,
            persisted,
            target_status=AlarmStatus.RESOLVED,
            actor="admin",
            note="False alarm",
        )

    expect(result is True)

    async with sessionmaker() as session:
        updated = await session.get(Alarm, alarm.id)

    expect(updated.status == AlarmStatus.RESOLVED)
    expect(updated.resolved_at is not None)
    expect(updated.resolved_by == "admin")
    expect(updated.meta.get("resolve_note") == "False alarm")


async def test_transition_triggered_to_cancelled(sessionmaker: async_sessionmaker, engine):
    """TRIGGERED -> CANCELLED is a valid transition."""
    alarm = _make_alarm(status=AlarmStatus.TRIGGERED)

    async with sessionmaker() as session:
        session.add(alarm)
        await session.commit()

    async with sessionmaker() as session:
        persisted = await session.get(Alarm, alarm.id)
        result = await transition_alarm(
            session,
            persisted,
            target_status=AlarmStatus.CANCELLED,
            actor="admin",
            note="Test cancelled",
        )

    expect(result is True)

    async with sessionmaker() as session:
        updated = await session.get(Alarm, alarm.id)

    expect(updated.status == AlarmStatus.CANCELLED)
    expect(updated.cancelled_at is not None)
    expect(updated.cancelled_by == "admin")
    expect(updated.meta.get("cancel_note") == "Test cancelled")


async def test_transition_acknowledged_to_resolved(sessionmaker: async_sessionmaker, engine):
    """ACKNOWLEDGED -> RESOLVED is a valid transition."""
    alarm = _make_alarm(status=AlarmStatus.ACKNOWLEDGED)

    async with sessionmaker() as session:
        session.add(alarm)
        await session.commit()

    async with sessionmaker() as session:
        persisted = await session.get(Alarm, alarm.id)
        result = await transition_alarm(
            session, persisted, target_status=AlarmStatus.RESOLVED, actor="nurse"
        )

    expect(result is True)

    async with sessionmaker() as session:
        updated = await session.get(Alarm, alarm.id)

    expect(updated.status == AlarmStatus.RESOLVED)
    expect(updated.resolved_by == "nurse")


async def test_transition_same_status_returns_false(sessionmaker: async_sessionmaker, engine):
    """Transitioning to the current status is a no-op, returning False."""
    alarm = _make_alarm(status=AlarmStatus.TRIGGERED)

    async with sessionmaker() as session:
        session.add(alarm)
        await session.commit()

    async with sessionmaker() as session:
        persisted = await session.get(Alarm, alarm.id)
        result = await transition_alarm(session, persisted, target_status=AlarmStatus.TRIGGERED)

    expect(result is False)


async def test_transition_invalid_raises_conflict(sessionmaker: async_sessionmaker, engine):
    """An invalid transition (e.g., RESOLVED -> TRIGGERED) raises ConflictError."""
    alarm = _make_alarm(status=AlarmStatus.RESOLVED)

    async with sessionmaker() as session:
        session.add(alarm)
        await session.commit()

    async with sessionmaker() as session:
        persisted = await session.get(Alarm, alarm.id)
        with pytest.raises(ConflictError, match="Invalid status transition"):
            await transition_alarm(session, persisted, target_status=AlarmStatus.TRIGGERED)


async def test_transition_cancelled_to_resolved_raises_conflict(
    sessionmaker: async_sessionmaker, engine
):
    """CANCELLED is a terminal state; cannot transition out of it."""
    alarm = _make_alarm(status=AlarmStatus.CANCELLED)

    async with sessionmaker() as session:
        session.add(alarm)
        await session.commit()

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
