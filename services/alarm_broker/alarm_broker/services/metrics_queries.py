"""Database queries for Prometheus metrics."""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from alarm_broker.db.models import Alarm, AlarmNotification, AlarmStatus


async def get_alarm_counts(session: AsyncSession) -> dict[str, int]:
    """Get alarm counts grouped by status."""
    rows = (
        await session.execute(select(Alarm.status, func.count(Alarm.id)).group_by(Alarm.status))
    ).all()
    counts = {s.value: 0 for s in AlarmStatus}
    for alarm_status, count in rows:
        counts[alarm_status.value] = int(count)
    return counts


async def get_notification_counts(session: AsyncSession) -> list[tuple[str, str, int]]:
    """Get notification attempt counts grouped by channel and result."""
    rows = (
        await session.execute(
            select(
                AlarmNotification.channel,
                func.coalesce(AlarmNotification.result, "unknown"),
                func.count(AlarmNotification.id),
            ).group_by(AlarmNotification.channel, AlarmNotification.result)
        )
    ).all()
    return [(str(channel), str(result), int(count)) for channel, result, count in rows]
