"""Focused tests for admin audit redaction and master-data lifecycle guards."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from escalane.db.models import AdminAuditEvent, Device, EscalationPolicy, Person, Room, Site
from escalane.services.admin_audit import (
    REDACTED_VALUE,
    add_admin_audit_event,
    redact_sensitive_fields,
)

try:
    from tests.constants import value_for_test
except ModuleNotFoundError:
    from constants import value_for_test

pytestmark = [pytest.mark.unit]


def test_redact_sensitive_fields_recurses_without_mutating_input():
    changed = {
        "name": "Ops Desk",
        "phone_mobile": "+491234",
        "nested": {"deviceToken": "device-secret", "apiKey": "api-secret", "label": "T5"},
        "targets": [{"address": "ops@example.test"}],
    }

    redacted = redact_sensitive_fields(changed)

    assert redacted == {
        "name": "Ops Desk",
        "phone_mobile": REDACTED_VALUE,
        "nested": {
            "deviceToken": REDACTED_VALUE,
            "apiKey": REDACTED_VALUE,
            "label": "T5",
        },
        "targets": [{"address": REDACTED_VALUE}],
    }
    assert changed["nested"]["deviceToken"] == "device-secret"


async def test_add_admin_audit_event_persists_redacted_fields(sessionmaker: async_sessionmaker):
    async with sessionmaker() as session:
        event = add_admin_audit_event(
            session,
            operator_name="Admin",
            action="update",
            resource_type="device",
            resource_id="device-1",
            changed_fields={"device_token": "secret", "model_family": "T5"},
            request_id="req-1",
        )
        await session.commit()
        event_id = event.id

    async with sessionmaker() as session:
        saved = await session.scalar(select(AdminAuditEvent).where(AdminAuditEvent.id == event_id))
        assert saved is not None
        assert saved.changed_fields == {"device_token": REDACTED_VALUE, "model_family": "T5"}
        assert saved.request_id == "req-1"


async def test_master_data_lifecycle_columns_default_to_active_version_one(
    sessionmaker: async_sessionmaker,
):
    async with sessionmaker() as session:
        site = Site(id="site-defaults", name="Defaults Site")
        room = Room(id="room-defaults", site_id=site.id, label="Defaults Room")
        person = Person(id="person-defaults", display_name="Defaults Person")
        device = Device(
            id="device-defaults",
            device_token=value_for_test("defaults-device"),
            person_id=person.id,
            room_id=room.id,
        )
        policy = EscalationPolicy(id="policy-defaults", name="Defaults Policy")
        session.add_all([site, room, person, device, policy])
        await session.flush()

        assert all(resource.version == 1 for resource in [site, room, person, device, policy])
        assert all(resource.active is True for resource in [site, room, person, device])
