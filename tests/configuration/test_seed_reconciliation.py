"""Seed reconciliation contracts across all durable master-data records."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from escalane.config.errors import ValidationError
from escalane.configuration.seed import apply_seed
from escalane.persistence.models import Device, EscalationStep, EscalationTarget, Person, Room, Site


def _seed(*, site_name: str = "North") -> dict[str, object]:
    return {
        "sites": [{"id": "north", "name": site_name, "active": "yes"}],
        "persons": [{"id": "p1", "display_name": "Responder", "active": "on"}],
        "rooms": [{"id": "r1", "site_id": "north", "label": "Ward", "active": "true"}],
        "devices": [
            {
                "id": "d1",
                "device_token": "${YEALINK_DEVICE_TOKEN}",
                "person_id": "p1",
                "room_id": "r1",
                "active": "1",
            }
        ],
        "escalation_policy": {"id": "default", "name": "Primary"},
        "escalation_targets": [
            {
                "id": "signal",
                "label": "On call",
                "channel": "signal",
                "address": "${SIGNAL_TARGET_GROUP_ID}",
                "enabled": "yes",
            }
        ],
        "escalation_steps": [
            {
                "policy_id": "default",
                "step_no": 0,
                "after_seconds": "${ESCALATE_T1}",
                "target_ids": ["signal"],
            }
        ],
    }


@pytest.mark.asyncio
async def test_apply_seed_creates_then_versions_master_data(sessionmaker, settings) -> None:
    settings.yealink_device_token = "device-secret"
    settings.signal_target_group_id = "signal-group"
    settings.escalate_t1 = 45
    async with sessionmaker() as session:
        await apply_seed(session, _seed(), settings)

    async with sessionmaker() as session:
        site = await session.get(Site, "north")
        person = await session.get(Person, "p1")
        room = await session.get(Room, "r1")
        device = await session.scalar(select(Device).where(Device.id == "d1"))
        target = await session.get(EscalationTarget, "signal")
        step = await session.scalar(select(EscalationStep))
        assert site and site.name == "North" and site.version == 1
        assert person and person.active
        assert room and room.site_id == "north"
        assert device and device.device_token == "device-secret"
        assert target and target.address == "signal-group"
        assert step and step.after_seconds == 45

    async with sessionmaker() as session:
        await apply_seed(session, _seed(site_name="North revised"), settings)
    async with sessionmaker() as session:
        site = await session.get(Site, "north")
        assert site and site.name == "North revised" and site.version == 2


@pytest.mark.asyncio
async def test_apply_seed_rejects_unknown_or_unplaced_placeholders(sessionmaker, settings) -> None:
    with pytest.raises(ValidationError, match="unknown or unresolved"):
        await apply_seed(
            sessionmaker(),
            {"devices": [{"id": "d", "device_token": "${NOT_ALLOWED}"}]},
            settings,
        )
    with pytest.raises(ValidationError, match="not allowed"):
        await apply_seed(sessionmaker(), {"sites": [{"id": "${VALUE}", "name": "Bad"}]}, settings)


@pytest.mark.asyncio
async def test_apply_seed_rejects_invalid_schedule_before_mutating(sessionmaker, settings) -> None:
    async with sessionmaker() as session:
        with pytest.raises(ValidationError, match="must reference a target"):
            await apply_seed(
                session,
                {
                    "escalation_steps": [
                        {"policy_id": "default", "step_no": 1, "after_seconds": 5, "target_ids": []}
                    ]
                },
                settings,
            )
