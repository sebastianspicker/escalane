"""Concurrency and hierarchy guards for administrative seed imports."""

from __future__ import annotations

import pytest

from escalane.core.errors import ConflictError, ValidationError
from escalane.db.models import Site
from escalane.services.seed_service import apply_seed_payload

pytestmark = pytest.mark.integration


async def test_seed_rejects_active_room_under_inactive_site(
    sessionmaker,
    seeded_db,
    settings,
) -> None:
    async with sessionmaker() as session:
        site = await session.get(Site, "bg")
        assert site is not None
        site.active = False
        site.version += 1
        await session.commit()

    async with sessionmaker() as session:
        with pytest.raises(ConflictError, match="inactive or missing parent"):
            await apply_seed_payload(
                session,
                data={
                    "rooms": [
                        {
                            "id": "invalid-active-room",
                            "site_id": "bg",
                            "label": "Invalid Room",
                            "active": True,
                        }
                    ]
                },
                settings=settings,
            )


@pytest.mark.parametrize(
    "steps",
    [
        [
            {
                "policy_id": "default",
                "step_no": 1,
                "after_seconds": 60,
                "target_ids": [],
            }
        ],
        [
            {
                "policy_id": "default",
                "step_no": -1,
                "after_seconds": 60,
                "target_ids": ["target-a"],
            }
        ],
        [
            {
                "policy_id": "default",
                "step_no": 1,
                "after_seconds": 60,
                "target_ids": ["target-a"],
            },
            {
                "policy_id": "default",
                "step_no": 1,
                "after_seconds": 120,
                "target_ids": ["target-b"],
            },
        ],
    ],
)
async def test_seed_rejects_invalid_escalation_schedule(
    sessionmaker,
    settings,
    steps: list[dict[str, object]],
) -> None:
    async with sessionmaker() as session:
        with pytest.raises(ValidationError):
            await apply_seed_payload(
                session,
                data={"escalation_steps": steps},
                settings=settings,
            )
