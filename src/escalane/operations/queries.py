"""Database queries for Prometheus metrics."""

from __future__ import annotations

import logging

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from escalane.contracts.alarms import AlarmStatus
from escalane.persistence.models import Alarm, AlarmNotification

logger = logging.getLogger("escalane")


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


async def historical_metrics(
    session: AsyncSession, redis
) -> tuple[dict[str, int], list[tuple[str, str, int]]]:
    """Cache only historical aggregates, retaining soft-deleted-alarm semantics."""
    import json

    key = "escalane:metrics:history:v1"
    try:
        raw = await redis.get(key)
        if raw is not None:
            value = json.loads(raw)
            alarms = value["alarms"]
            notifications = value["notifications"]
            if (
                isinstance(alarms, dict)
                and set(alarms) == {status.value for status in AlarmStatus}
                and all(type(count) is int and count >= 0 for count in alarms.values())
                and isinstance(notifications, list)
                and all(
                    isinstance(row, list)
                    and len(row) == 3
                    and isinstance(row[0], str)
                    and isinstance(row[1], str)
                    and type(row[2]) is int
                    and row[2] >= 0
                    for row in notifications
                )
            ):
                return alarms, [tuple(row) for row in notifications]
    except Exception:
        logger.debug("queries_cache_unavailable")
    alarms = await get_alarm_counts(session)
    notifications = await get_notification_counts(session)
    try:
        await redis.set(key, json.dumps({"alarms": alarms, "notifications": notifications}), ex=10)
    except Exception:
        logger.debug("queries_cache_unavailable")
    return alarms, notifications


async def outbox_gauges(session: AsyncSession) -> dict[str, float]:
    """Report current durable pending work independently of historical caches."""
    from datetime import UTC, datetime

    from escalane.persistence.models import AlarmEventOutbox

    count, oldest = (
        await session.execute(
            select(func.count(AlarmEventOutbox.id), func.min(AlarmEventOutbox.created_at)).where(
                AlarmEventOutbox.published_at.is_(None)
            )
        )
    ).one()
    age = 0.0
    if oldest is not None:
        aware = oldest if oldest.tzinfo else oldest.replace(tzinfo=UTC)
        age = max(0.0, (datetime.now(UTC) - aware).total_seconds())
    return {"outbox_pending": float(count), "outbox_oldest_age_seconds": age}
