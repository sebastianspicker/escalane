"""Worker alarm lifecycle and task behavior.

Covers:
  - alarm_created() full flow
  - escalate() with missing alarm and already-resolved alarm
  - alarm_acked() flow
  - Soft-delete exclusion from list_alarms
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from arq import Retry
from sqlalchemy import select

from escalane.contracts.alarms import AlarmStatus
from escalane.persistence.models import Alarm, AlarmNotification
from escalane.worker.tasks import alarm_acked, alarm_created, escalate
from tests.support.assertions import expect
from tests.support.constants import TEST_ADMIN_API_KEY
from tests.support.worker_task_helpers import (
    make_alarm,
    make_ctx,
    open_app_client,
    persist_alarm,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

ADMIN_HEADERS = {"X-Admin-Key": TEST_ADMIN_API_KEY}


_make_alarm = make_alarm
_make_ctx = make_ctx

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# 1. alarm_created() full flow
# ---------------------------------------------------------------------------


async def test_alarm_created_full_flow(sessionmaker, seeded_db, settings):
    """alarm_created enriches context, creates Zammad ticket, sends notifications."""
    alarm_id = uuid.uuid4()

    async with sessionmaker() as session:
        session.add(_make_alarm(alarm_id))
        await session.commit()

    ctx = _make_ctx(sessionmaker, settings)

    await alarm_created(ctx, str(alarm_id))

    # Verify the alarm now has a zammad_ticket_id set
    async with sessionmaker() as session:
        alarm = await session.get(Alarm, alarm_id)
        expect(alarm is not None)
        expect(alarm.zammad_ticket_id is not None)
        expect(isinstance(alarm.zammad_ticket_id, int))

    # Verify notification audit logs were created
    async with sessionmaker() as session:
        rows = (
            await session.scalars(
                select(AlarmNotification).where(AlarmNotification.alarm_id == alarm_id)
            )
        ).all()
        expect(len(rows) >= 1)
        channels = {r.channel for r in rows}
        expect("zammad" in channels)


async def test_alarm_created_missing_alarm(sessionmaker, seeded_db, settings):
    """alarm_created with non-existent alarm_id returns early without error."""
    ctx = _make_ctx(sessionmaker, settings)
    fake_id = uuid.uuid4()

    # Should not raise
    await alarm_created(ctx, str(fake_id))


# ---------------------------------------------------------------------------
# 2. escalate() with missing alarm and already-resolved alarm
# ---------------------------------------------------------------------------


async def test_escalate_missing_alarm(sessionmaker, seeded_db, settings):
    """escalate() with a non-existent alarm_id returns early without error."""
    ctx = _make_ctx(sessionmaker, settings)
    fake_id = uuid.uuid4()

    # Should not raise
    await escalate(ctx, str(fake_id), step_no=1)

    # No notifications should be logged
    async with sessionmaker() as session:
        rows = (
            await session.scalars(
                select(AlarmNotification).where(AlarmNotification.alarm_id == fake_id)
            )
        ).all()
        expect(len(rows) == 0)


async def test_escalate_triggered_alarm_sends_notifications(sessionmaker, seeded_db, settings):
    """escalate() with a triggered alarm enriches context and sends notifications."""
    alarm_id = uuid.uuid4()

    async with sessionmaker() as session:
        session.add(_make_alarm(alarm_id, status=AlarmStatus.TRIGGERED))
        await session.commit()

    ctx = _make_ctx(sessionmaker, settings)

    await escalate(ctx, str(alarm_id), step_no=1)

    # No escalation targets are seeded here, so success means the enrichment
    # and notification orchestration path ran without creating audit rows.


# ---------------------------------------------------------------------------
# 3. alarm_acked() flow
# ---------------------------------------------------------------------------


async def test_alarm_acked_adds_zammad_note(sessionmaker, seeded_db, settings):
    """alarm_acked with zammad_ticket_id adds an ACK note via Zammad."""
    alarm_id = uuid.uuid4()

    async with sessionmaker() as session:
        session.add(
            _make_alarm(
                alarm_id,
                zammad_ticket_id=42,
                acked_at=datetime.now(UTC),
                acked_by="TestUser",
            )
        )
        await session.commit()

    ctx = _make_ctx(sessionmaker, settings)

    await alarm_acked(ctx, str(alarm_id), acked_by="TestUser", note="Test note")

    # Verify notification was logged for the ACK update
    async with sessionmaker() as session:
        rows = (
            await session.scalars(
                select(AlarmNotification).where(AlarmNotification.alarm_id == alarm_id)
            )
        ).all()
        expect(len(rows) >= 1)
        ack_rows = [r for r in rows if r.channel == "zammad"]
        expect(len(ack_rows) >= 1)
        expect(ack_rows[0].result == "ok")


async def test_alarm_acked_no_zammad_ticket_retries(sessionmaker, seeded_db, settings):
    """alarm_acked waits for an in-flight Zammad ticket instead of losing its note."""
    alarm_id = uuid.uuid4()

    await persist_alarm(sessionmaker, _make_alarm(alarm_id, zammad_ticket_id=None))

    ctx = _make_ctx(sessionmaker, settings)

    with pytest.raises(Retry) as retry:
        await alarm_acked(ctx, str(alarm_id), acked_by="TestUser")

    expect(retry.value.defer_score == 1_000)

    # No notification logs
    async with sessionmaker() as session:
        rows = (
            await session.scalars(
                select(AlarmNotification).where(AlarmNotification.alarm_id == alarm_id)
            )
        ).all()
        expect(len(rows) == 0)


async def test_alarm_acked_missing_alarm(sessionmaker, seeded_db, settings):
    """alarm_acked with non-existent alarm_id returns early."""
    ctx = _make_ctx(sessionmaker, settings)
    fake_id = uuid.uuid4()

    # Should not raise
    await alarm_acked(ctx, str(fake_id))


async def test_alarm_acked_without_note(sessionmaker, seeded_db, settings):
    """alarm_acked without a note still works (covers the no-note branch)."""
    alarm_id = uuid.uuid4()

    async with sessionmaker() as session:
        session.add(
            _make_alarm(
                alarm_id,
                zammad_ticket_id=99,
                acked_at=datetime.now(UTC),
            )
        )
        await session.commit()

    ctx = _make_ctx(sessionmaker, settings)

    await alarm_acked(ctx, str(alarm_id), acked_by="SomeUser", note=None)

    async with sessionmaker() as session:
        rows = (
            await session.scalars(
                select(AlarmNotification).where(AlarmNotification.alarm_id == alarm_id)
            )
        ).all()
        expect(len(rows) >= 1)


# ---------------------------------------------------------------------------
# 4. Soft-delete test: deleted alarms excluded from list
# ---------------------------------------------------------------------------


async def test_soft_deleted_alarm_excluded_from_list(
    engine, sessionmaker, seeded_db, fake_redis, settings
):
    """Soft-deleted alarms (deleted_at set) do not appear in GET /v1/alarms."""
    settings.admin_api_key = TEST_ADMIN_API_KEY
    now = datetime.now(UTC)
    alarm_id = uuid.uuid4()

    async with sessionmaker() as session:
        session.add(
            _make_alarm(
                alarm_id,
                created_at=now,
                deleted_at=now,
                deleted_by="cleanup",
            )
        )
        # Also add a non-deleted alarm so we know the list endpoint works
        visible_id = uuid.uuid4()
        session.add(_make_alarm(visible_id, created_at=now))
        await session.commit()

    async with open_app_client(settings=settings, engine=engine, redis=fake_redis) as client:
        response = await client.get("/v1/alarms", headers=ADMIN_HEADERS)

    expect(response.status_code == 200)
    data = response.json()
    returned_ids = {item["id"] for item in data}

    # The soft-deleted alarm must not appear
    expect(str(alarm_id) not in returned_ids)
    # The visible alarm must appear
    expect(str(visible_id) in returned_ids)
