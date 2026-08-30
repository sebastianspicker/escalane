"""Recover notification work from durable alarm lifecycle events."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from escalane.config import constants
from escalane.notifications.delivery import completed_notification
from escalane.persistence.models import Alarm, AlarmEventOutbox

ACK_EVENT_REPLAY_STALE_SECONDS = 600


async def rearm_stale_acknowledgement_events(
    session: AsyncSession,
    *,
    limit: int = 500,
) -> int:
    """Reopen stale ACK events that still need a Zammad acknowledgement note."""
    cutoff = datetime.now(UTC) - timedelta(seconds=ACK_EVENT_REPLAY_STALE_SECONDS)
    rows = (
        await session.execute(
            select(AlarmEventOutbox, Alarm.zammad_ticket_id)
            .join(Alarm, Alarm.id == AlarmEventOutbox.alarm_id)
            .where(
                AlarmEventOutbox.event_type == constants.EVENT_ALARM_ACKNOWLEDGED,
                AlarmEventOutbox.published_at <= cutoff,
                Alarm.zammad_ticket_id.is_not(None),
                Alarm.deleted_at.is_(None),
            )
            .order_by(AlarmEventOutbox.published_at, AlarmEventOutbox.id)
            .limit(limit)
        )
    ).all()
    rearmed = 0
    for event, ticket_id in rows:
        if await completed_notification(
            session,
            alarm_id=event.alarm_id,
            channel="zammad",
            target_id=None,
            payload_matches={"action": "ack_update", "ticket_id": ticket_id},
        ):
            continue
        event.published_at = None
        rearmed += 1
    await session.commit()
    return rearmed
