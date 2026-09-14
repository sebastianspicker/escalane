"""Durable dashboard invalidation and revision-scoped status counts."""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from escalane.contracts.alarms import AlarmStatus
from escalane.persistence.models import Alarm, DashboardRevision

logger = logging.getLogger("escalane")


async def dashboard_revision(session: AsyncSession) -> str:
    epoch, version = (
        await session.execute(
            select(DashboardRevision.epoch, DashboardRevision.version).where(
                DashboardRevision.id == 1
            )
        )
    ).one()
    return f"{epoch}:{version}"


async def visible_counts(session: AsyncSession) -> dict[str, int]:
    rows = (
        await session.execute(
            select(Alarm.status, func.count(Alarm.id))
            .where(Alarm.deleted_at.is_(None))
            .group_by(Alarm.status)
        )
    ).all()
    counts = {status.value: 0 for status in AlarmStatus}
    counts.update({status.value: int(count) for status, count in rows})
    return counts


async def dashboard_counts(session: AsyncSession, redis: Any) -> dict[str, int]:
    revision = await dashboard_revision(session)
    key = f"escalane:dashboard:counts:{revision}"
    try:
        cached = await redis.get(key)
        if cached is not None:
            counts = json.loads(cached)
            if (
                isinstance(counts, dict)
                and set(counts) == {s.value for s in AlarmStatus}
                and all(type(value) is int and value >= 0 for value in counts.values())
            ):
                return counts
    except Exception:
        logger.debug("dashboard_cache_unavailable")
    counts = await visible_counts(session)
    # READ COMMITTED statements can observe concurrent commits. Never poison a
    # revision key with counts from another revision. An uncached result is safe.
    if await dashboard_revision(session) == revision:
        try:
            await redis.set(key, json.dumps(counts), ex=60)
        except Exception:
            logger.debug("dashboard_cache_write_unavailable")
    return counts
