"""Shared optimistic-concurrency and lifecycle guards for master data."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from escalane.config.errors import ConflictError
from escalane.persistence.models import Person, Room, Site


async def lock_active_referenced_parents(
    session: AsyncSession,
    *,
    resource_name: str,
    values: dict[str, Any],
) -> None:
    """Lock and validate parents before an active child write.

    A parent deactivation uses ``FOR UPDATE`` before counting dependants. Child
    writers acquire the same lock before they insert or update an active Room
    or Device. On PostgreSQL this makes the two transactions serialize: the
    child either commits first and is counted, or observes the inactive parent
    after deactivation commits and fails with a controlled conflict. SQLite
    treats ``FOR UPDATE`` as a no-op, so it can only cover the inactive-parent
    response path deterministically.
    """
    if not values.get("active", True):
        return

    references: dict[str, tuple[tuple[str, type[Any]], ...]] = {
        "rooms": (("site_id", Site),),
        # Keep a stable lock order for a Device that references both parents.
        "devices": (("person_id", Person), ("room_id", Room)),
    }
    for field, model in references.get(resource_name, ()):
        parent_id = values.get(field)
        if parent_id is None:
            continue
        parent = await session.scalar(select(model).where(model.id == parent_id).with_for_update())
        if parent is None or not parent.active:
            raise ConflictError(
                "Active resource cannot reference an inactive or missing parent",
                details={
                    "resource_type": resource_name,
                    "parent_field": field,
                    "parent_id": parent_id,
                },
            )
