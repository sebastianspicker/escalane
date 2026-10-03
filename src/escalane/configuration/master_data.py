"""Versioned master-data reads, writes, and lifecycle guards."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from escalane.config.errors import ConflictError, NotFoundError
from escalane.configuration.audit import add_admin_audit_event
from escalane.persistence.models import Alarm, Device, Person, Room, Site

RESOURCE_MODELS: dict[str, Any] = {
    "sites": Site,
    "rooms": Room,
    "people": Person,
    "devices": Device,
}
RESOURCE_FIELDS: dict[str, tuple[str, ...]] = {
    "sites": ("name",),
    "rooms": ("site_id", "label", "floor", "notes"),
    "people": ("display_name", "role", "phone_mobile", "phone_ext"),
    "devices": (
        "vendor",
        "model_family",
        "mac",
        "account_ext",
        "device_token",
        "person_id",
        "room_id",
    ),
}
REQUIRED_FIELDS = frozenset({"name", "site_id", "label", "display_name", "vendor", "model_family"})


class UnknownResourceError(NotFoundError):
    """Raised when a master-data type is not an allowlisted configuration resource."""


class ResourceNotFoundError(NotFoundError):
    """Raised when a versioned master-data row does not exist."""


class ResourceStateConflict(ConflictError):
    """Raised when a lifecycle rule blocks a mutation; ``public_detail`` is client-safe."""

    def __init__(self, public_detail: str | dict[str, int]) -> None:
        super().__init__("Resource lifecycle rule blocks this change")
        self.public_detail = public_detail


@dataclass(frozen=True, slots=True)
class ResourcePage:
    """One keyset page of master-data rows."""

    items: list[Any]
    has_next: bool


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


def is_resource_name(resource_name: str) -> bool:
    """Return whether the name is an allowlisted master-data resource type."""
    return resource_name in RESOURCE_MODELS


def resource_model(resource_name: str) -> Any:
    """Resolve an allowlisted master-data type and reject unknown configuration pages."""
    model = RESOURCE_MODELS.get(resource_name)
    if model is None:
        raise UnknownResourceError("Configuration resource", resource_name)
    return model


async def list_resources(
    session: AsyncSession, resource_name: str, *, after: str | None, limit: int
) -> ResourcePage:
    """Load one id-ordered page of rows, probing one extra row to detect a next page."""
    model = resource_model(resource_name)
    query = select(model).order_by(model.id).limit(limit + 1)
    if after is not None:
        query = query.where(model.id > after)
    items = list((await session.scalars(query)).all())
    return ResourcePage(items=items[:limit], has_next=len(items) > limit)


def _mutation_applied(result: Any) -> bool:
    """Normalize SQLAlchemy row counts for optimistic-concurrency checks."""
    return bool(result.rowcount)


def _version_conflict(resource_id: str) -> ConflictError:
    """Report stale form submissions rather than overwrite another operator's edit."""
    return ConflictError(
        "Resource has changed since it was loaded",
        details={"resource_id": resource_id},
    )


async def _create_resource(
    session: AsyncSession, model: Any, resource_id: str, values: dict[str, Any]
) -> None:
    """Insert a new master-data row after its referenced parents were locked."""
    session.add(model(id=resource_id, **values))
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise _version_conflict(resource_id) from exc


async def _update_resource_if_current(
    session: AsyncSession,
    model: Any,
    resource_id: str,
    version: int,
    values: dict[str, Any],
) -> None:
    """Update only the expected version to prevent lost operator edits."""
    result = await session.execute(
        update(model)
        .where(model.id == resource_id, model.version == version)
        .values(**values, version=model.version + 1)
    )
    if not _mutation_applied(result):
        raise _version_conflict(resource_id)


async def save_resource(
    session: AsyncSession,
    *,
    resource_name: str,
    resource_id: str,
    version: int | None,
    values: dict[str, Any],
    operator_name: str,
    request_id: str | None,
) -> None:
    """Create (``version is None``) or version-check update a row, audit it, and commit."""
    model = resource_model(resource_name)
    await lock_active_referenced_parents(session, resource_name=resource_name, values=values)
    if version is None:
        await _create_resource(session, model, resource_id, values)
    else:
        await _update_resource_if_current(session, model, resource_id, version, values)
    add_admin_audit_event(
        session,
        operator_name=operator_name,
        action="create" if version is None else "update",
        resource_type=resource_name,
        resource_id=resource_id,
        changed_fields=values,
        request_id=request_id,
    )
    await session.commit()


async def _active_dependency_counts(
    session: AsyncSession, resource_name: str, resource_id: str
) -> dict[str, int]:
    """Count active child records that would become unusable after deactivation."""
    filters: dict[str, tuple[Any, Any]] = {
        "sites": (Room.id, (Room.site_id == resource_id) & Room.active.is_(True)),
        "rooms": (Device.id, (Device.room_id == resource_id) & Device.active.is_(True)),
        "people": (Device.id, (Device.person_id == resource_id) & Device.active.is_(True)),
    }
    selected = filters.get(resource_name)
    if selected is None:
        return {}
    column, condition = selected
    count = int(await session.scalar(select(func.count(column)).where(condition)) or 0)
    return {"active_dependencies": count}


async def _historical_dependency_count(
    session: AsyncSession, resource_name: str, resource_id: str
) -> int:
    """Count historical references that require the resource to remain audit-visible."""
    conditions: dict[str, list[tuple[Any, Any]]] = {
        "sites": [(Room.id, Room.site_id == resource_id), (Alarm.id, Alarm.site_id == resource_id)],
        "rooms": [
            (Device.id, Device.room_id == resource_id),
            (Alarm.id, Alarm.room_id == resource_id),
        ],
        "people": [
            (Device.id, Device.person_id == resource_id),
            (Alarm.id, Alarm.person_id == resource_id),
        ],
        "devices": [(Alarm.id, Alarm.device_id == resource_id)],
    }
    counts = [
        int(await session.scalar(select(func.count(column)).where(condition)) or 0)
        for column, condition in conditions[resource_name]
    ]
    return sum(counts)


async def _lock_resource_for_mutation(
    session: AsyncSession, resource_name: str, resource_id: str, version: int
) -> tuple[Any, Any]:
    """Lock a parent row, then check its version before dependency checks.

    PostgreSQL's ``FOR UPDATE`` also blocks concurrent child inserts which
    need a key-share lock for their foreign key. SQLite accepts this as a
    no-op, so its tests cover response semantics rather than lock behavior.
    """
    model = resource_model(resource_name)
    item = await session.scalar(select(model).where(model.id == resource_id).with_for_update())
    if item is None:
        raise ResourceNotFoundError("Resource", resource_id)
    if item.version != version:
        raise _version_conflict(resource_id)
    return model, item


async def _commit_resource_mutation(session: AsyncSession, resource_id: str) -> None:
    """Commit a dependency-checked mutation and normalize concurrent reference failures."""
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ConflictError(
            "Resource is referenced by a concurrent change",
            details={"resource_id": resource_id},
        ) from exc


async def deactivate_resource(
    session: AsyncSession,
    *,
    resource_name: str,
    resource_id: str,
    version: int,
    operator_name: str,
    request_id: str | None,
) -> None:
    """Deactivate the expected version unless active dependants still need the row."""
    model, _item = await _lock_resource_for_mutation(session, resource_name, resource_id, version)
    blockers = await _active_dependency_counts(session, resource_name, resource_id)
    if any(blockers.values()):
        raise ResourceStateConflict(blockers)
    result = await session.execute(
        update(model)
        .where(model.id == resource_id, model.version == version)
        .values(active=False, version=model.version + 1)
    )
    if not _mutation_applied(result):
        raise _version_conflict(resource_id)
    add_admin_audit_event(
        session,
        operator_name=operator_name,
        action="deactivate",
        resource_type=resource_name,
        resource_id=resource_id,
        changed_fields={"active": False},
        request_id=request_id,
    )
    await _commit_resource_mutation(session, resource_id)


async def delete_resource(
    session: AsyncSession,
    *,
    resource_name: str,
    resource_id: str,
    version: int,
    operator_name: str,
    request_id: str | None,
) -> None:
    """Delete an inactive, unreferenced row at the expected version."""
    model, item = await _lock_resource_for_mutation(session, resource_name, resource_id, version)
    if await _historical_dependency_count(session, resource_name, resource_id):
        raise ResourceStateConflict("resource_is_referenced_deactivate_instead")
    if item.active:
        raise ResourceStateConflict("deactivate_before_delete")
    result = await session.execute(
        delete(model).where(model.id == resource_id, model.version == version)
    )
    if not _mutation_applied(result):
        raise _version_conflict(resource_id)
    add_admin_audit_event(
        session,
        operator_name=operator_name,
        action="delete",
        resource_type=resource_name,
        resource_id=resource_id,
        request_id=request_id,
    )
    await _commit_resource_mutation(session, resource_id)
