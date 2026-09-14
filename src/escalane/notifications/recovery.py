"""Recover notification work from durable alarm lifecycle events."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import String, and_, cast, exists, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from escalane.config import constants
from escalane.persistence.models import Alarm, AlarmEventOutbox, AlarmNotification

ACK_EVENT_REPLAY_STALE_SECONDS = 600


async def rearm_stale_acknowledgement_events(
    session: AsyncSession,
    *,
    limit: int = 500,
) -> int:
    """Reopen stale ACK events that still need a Zammad acknowledgement note."""
    cutoff = datetime.now(UTC) - timedelta(seconds=ACK_EVENT_REPLAY_STALE_SECONDS)
    completed = aliased(AlarmNotification)
    current_delivery_key = literal("ack_update:") + cast(Alarm.zammad_ticket_id, String)
    has_terminal_delivery = exists(
        select(1).where(
            completed.alarm_id == AlarmEventOutbox.alarm_id,
            completed.channel == "zammad",
            completed.target_id.is_(None),
            completed.result.in_(("ok", "permanent_error")),
            or_(
                completed.logical_delivery_key == current_delivery_key,
                and_(
                    completed.logical_delivery_key.is_(None),
                    completed.payload["action"].as_string() == "ack_update",
                    completed.payload["ticket_id"].as_integer() == Alarm.zammad_ticket_id,
                ),
            ),
        )
    )
    rows = (
        await session.scalars(
            select(AlarmEventOutbox)
            .join(Alarm, Alarm.id == AlarmEventOutbox.alarm_id)
            .where(
                AlarmEventOutbox.event_type == constants.EVENT_ALARM_ACKNOWLEDGED,
                AlarmEventOutbox.published_at <= cutoff,
                Alarm.zammad_ticket_id.is_not(None),
                Alarm.deleted_at.is_(None),
                ~has_terminal_delivery,
            )
            .order_by(AlarmEventOutbox.published_at, AlarmEventOutbox.id)
            .limit(limit)
        )
    ).all()
    for event in rows:
        event.published_at = None
    await session.commit()
    return len(rows)
