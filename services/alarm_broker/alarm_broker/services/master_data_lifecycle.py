"""Shared optimistic-concurrency and lifecycle guards for master data."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from alarm_broker.core.errors import ConflictError


class VersionedResource(Protocol):
    id: str
    version: int


class VersionedMasterData(VersionedResource, Protocol):
    active: bool


def require_current_version(resource: VersionedResource, expected_version: int) -> None:
    """Reject an edit based on a stale representation of a resource."""
    if resource.version != expected_version:
        raise ConflictError(
            "Resource has changed since it was loaded",
            details={
                "resource_id": resource.id,
                "expected_version": expected_version,
                "current_version": resource.version,
            },
        )


def require_no_dependencies(
    resource: VersionedMasterData, dependency_counts: Mapping[str, int]
) -> None:
    """Reject deactivation while active dependants still reference a resource."""
    dependencies = {name: count for name, count in dependency_counts.items() if count > 0}
    if dependencies:
        raise ConflictError(
            "Resource cannot be deactivated while dependencies remain",
            details={"resource_id": resource.id, "dependencies": dependencies},
        )


def deactivate_resource(
    resource: VersionedMasterData,
    *,
    expected_version: int,
    dependency_counts: Mapping[str, int] | None = None,
) -> bool:
    """Deactivate a resource and increment its version, if its contract permits it.

    Returns ``False`` for an idempotent deactivate request.  A caller should pass
    counts for active dependants when that resource type has dependency rules.
    """
    require_current_version(resource, expected_version)
    if not resource.active:
        return False
    require_no_dependencies(resource, dependency_counts or {})
    resource.active = False
    resource.version += 1
    return True
