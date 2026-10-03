"""Feature-level tests for master-data mutations, device upsert, and the audit read."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from escalane.config.errors import ConflictError
from escalane.configuration.audit import REDACTED_VALUE, list_recent_admin_events
from escalane.configuration.devices import (
    DeviceUpsertCommand,
    DeviceUpsertConflictError,
    upsert_device,
)
from escalane.configuration.master_data import (
    ResourceNotFoundError,
    ResourceStateConflict,
    UnknownResourceError,
    deactivate_resource,
    delete_resource,
    list_resources,
    save_resource,
)
from escalane.persistence.models import AdminAuditEvent, Device, Room, Site

pytestmark = [pytest.mark.unit]

_OPERATOR = {"operator_name": "Admin", "request_id": "req-1"}


async def test_stale_version_update_conflicts_without_audit(sessionmaker: async_sessionmaker):
    async with sessionmaker() as session:
        await save_resource(
            session,
            resource_name="sites",
            resource_id="site-1",
            version=None,
            values={"name": "North", "active": True},
            **_OPERATOR,
        )
        await save_resource(
            session,
            resource_name="sites",
            resource_id="site-1",
            version=1,
            values={"name": "North 2", "active": True},
            **_OPERATOR,
        )
        with pytest.raises(ConflictError) as raised:
            await save_resource(
                session,
                resource_name="sites",
                resource_id="site-1",
                version=1,
                values={"name": "Stale", "active": True},
                **_OPERATOR,
            )
        await session.rollback()

    assert raised.value.details == {"resource_id": "site-1"}
    async with sessionmaker() as session:
        site = await session.get(Site, "site-1")
        assert (site.name, site.version) == ("North 2", 2)
        actions = list(await session.scalars(select(AdminAuditEvent.action)))
        assert sorted(actions) == ["create", "update"]


async def test_deactivate_with_active_dependants_reports_blockers(
    sessionmaker: async_sessionmaker,
):
    async with sessionmaker() as session:
        session.add_all(
            [Site(id="site-1", name="North"), Room(id="room-1", site_id="site-1", label="A")]
        )
        await session.commit()
        with pytest.raises(ResourceStateConflict) as raised:
            await deactivate_resource(
                session, resource_name="sites", resource_id="site-1", version=1, **_OPERATOR
            )
    assert raised.value.public_detail == {"active_dependencies": 1}


async def test_delete_requires_inactive_unreferenced_current_row(
    sessionmaker: async_sessionmaker,
):
    async with sessionmaker() as session:
        session.add(Site(id="site-1", name="North"))
        await session.commit()
        with pytest.raises(ResourceStateConflict) as active:
            await delete_resource(
                session, resource_name="sites", resource_id="site-1", version=1, **_OPERATOR
            )
        await deactivate_resource(
            session, resource_name="sites", resource_id="site-1", version=1, **_OPERATOR
        )
        with pytest.raises(ConflictError):
            await delete_resource(
                session, resource_name="sites", resource_id="site-1", version=1, **_OPERATOR
            )
        await session.rollback()
        await delete_resource(
            session, resource_name="sites", resource_id="site-1", version=2, **_OPERATOR
        )
        with pytest.raises(ResourceNotFoundError):
            await delete_resource(
                session, resource_name="sites", resource_id="site-1", version=2, **_OPERATOR
            )

    assert active.value.public_detail == "deactivate_before_delete"


async def test_unknown_resource_name_is_rejected(sessionmaker: async_sessionmaker):
    async with sessionmaker() as session:
        with pytest.raises(UnknownResourceError):
            await list_resources(session, "unknown", after=None, limit=10)


async def test_list_resources_pages_by_id(sessionmaker: async_sessionmaker):
    async with sessionmaker() as session:
        session.add_all([Site(id=f"site-{n}", name="S") for n in range(3)])
        await session.commit()
        first = await list_resources(session, "sites", after=None, limit=2)
        second = await list_resources(session, "sites", after="site-1", limit=2)

    assert [item.id for item in first.items] == ["site-0", "site-1"]
    assert first.has_next is True
    assert [item.id for item in second.items] == ["site-2"]
    assert second.has_next is False


async def test_save_audit_event_redacts_sensitive_values(sessionmaker: async_sessionmaker):
    async with sessionmaker() as session:
        await save_resource(
            session,
            resource_name="people",
            resource_id="person-1",
            version=None,
            values={"display_name": "Alex", "phone_mobile": "+491234", "active": True},
            **_OPERATOR,
        )
        events = await list_recent_admin_events(session)

    assert len(events) == 1
    assert events[0].changed_fields["phone_mobile"] == REDACTED_VALUE
    assert events[0].changed_fields["display_name"] == "Alex"


async def test_device_upsert_inserts_then_updates_by_token(sessionmaker: async_sessionmaker):
    async with sessionmaker() as session:
        first = await upsert_device(
            session,
            DeviceUpsertCommand(
                id="device-1", device_token="token-1", vendor="v", model_family="m"
            ),
        )
        second = await upsert_device(
            session,
            DeviceUpsertCommand(device_token="token-1", vendor="v2", model_family="m"),
        )
        device = await session.get(Device, "device-1")

    assert first == second == "device-1"
    assert (device.vendor, device.version) == ("v2", 2)


async def test_device_upsert_integrity_error_maps_to_conflict(sessionmaker: async_sessionmaker):
    async with sessionmaker() as session:
        await upsert_device(
            session,
            DeviceUpsertCommand(
                id="device-1", device_token="token-1", vendor="v", model_family="m"
            ),
        )
        with pytest.raises(DeviceUpsertConflictError):
            await upsert_device(
                session,
                DeviceUpsertCommand(
                    id="device-1", device_token="token-2", vendor="v", model_family="m"
                ),
            )
