"""Tests for escalane.services.policy_service."""

from __future__ import annotations

try:
    from tests.assertions import expect
except ModuleNotFoundError:
    from assertions import expect

import pytest
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from escalane.api.schemas import EscalationPolicyIn, StepIn, TargetIn
from escalane.core.errors import ValidationError
from escalane.db.models import EscalationPolicy, EscalationStep, EscalationTarget
from escalane.services.policy_service import apply_escalation_policy

pytestmark = [pytest.mark.unit]


def _make_target(
    target_id: str = "tgt-sms-1",
    label: str = "SMS Target",
    channel: str = "sms",
    address: str = "+491234567890",
    enabled: bool = True,
) -> TargetIn:
    return TargetIn(id=target_id, label=label, channel=channel, address=address, enabled=enabled)


def _make_step(
    step_no: int = 1, after_seconds: int = 60, target_ids: list[str] | None = None
) -> StepIn:
    return StepIn(
        step_no=step_no, after_seconds=after_seconds, target_ids=target_ids or ["tgt-sms-1"]
    )


# ── apply_escalation_policy: create new ─────────────────────────────


async def test_apply_new_policy_creates_policy_and_targets(
    sessionmaker: async_sessionmaker, engine
):
    """A new policy with targets and steps is persisted correctly."""
    body = EscalationPolicyIn(
        policy_id="pol-new",
        name="New Policy",
        targets=[
            _make_target(target_id="tgt-1", label="SMS 1", channel="sms", address="+491111111111"),
            _make_target(
                target_id="tgt-2",
                label="Signal Group",
                channel="signal",
                address="group-id-1",
            ),
        ],
        steps=[
            _make_step(step_no=1, after_seconds=60, target_ids=["tgt-1"]),
            _make_step(step_no=2, after_seconds=180, target_ids=["tgt-1", "tgt-2"]),
        ],
    )

    async with sessionmaker() as session:
        result = await apply_escalation_policy(session, body)

    expect(result == "pol-new")

    async with sessionmaker() as session:
        policy = await session.get(EscalationPolicy, "pol-new")
        expect(policy is not None)
        expect(policy.name == "New Policy")

        targets = (await session.scalars(select(EscalationTarget))).all()
        target_ids = {t.id for t in targets}
        expect("tgt-1" in target_ids)
        expect("tgt-2" in target_ids)

        steps = (
            await session.scalars(
                select(EscalationStep).where(EscalationStep.policy_id == "pol-new")
            )
        ).all()
        expect(len(steps) == 3)  # step 1 has 1 target, step 2 has 2 targets


async def test_apply_new_policy_no_steps(sessionmaker: async_sessionmaker, engine):
    """A policy with targets but no steps is valid."""
    body = EscalationPolicyIn(
        policy_id="pol-empty",
        name="Empty Steps",
        targets=[_make_target(target_id="tgt-empty")],
        steps=[],
    )

    async with sessionmaker() as session:
        result = await apply_escalation_policy(session, body)

    expect(result == "pol-empty")

    async with sessionmaker() as session:
        policy = await session.get(EscalationPolicy, "pol-empty")
        expect(policy is not None)
        expect(policy.name == "Empty Steps")

        steps = (
            await session.scalars(
                select(EscalationStep).where(EscalationStep.policy_id == "pol-empty")
            )
        ).all()
        expect(len(steps) == 0)


# ── apply_escalation_policy: update existing ────────────────────────


async def test_apply_existing_policy_updates_name_and_steps(
    sessionmaker: async_sessionmaker, engine
):
    """Applying a policy with an existing ID updates the name and replaces steps."""
    # First, create the policy
    body_v1 = EscalationPolicyIn(
        policy_id="pol-update",
        name="Version 1",
        targets=[_make_target(target_id="tgt-u1")],
        steps=[_make_step(step_no=1, after_seconds=30, target_ids=["tgt-u1"])],
    )

    async with sessionmaker() as session:
        await apply_escalation_policy(session, body_v1)

    # Now update it with a new name and different steps
    body_v2 = EscalationPolicyIn(
        policy_id="pol-update",
        name="Version 2",
        targets=[
            _make_target(target_id="tgt-u1"),
            _make_target(
                target_id="tgt-u2", label="New Target", channel="email", address="a@b.com"
            ),
        ],
        steps=[
            _make_step(step_no=1, after_seconds=60, target_ids=["tgt-u1", "tgt-u2"]),
        ],
    )

    async with sessionmaker() as session:
        result = await apply_escalation_policy(session, body_v2)

    expect(result == "pol-update")

    async with sessionmaker() as session:
        policy = await session.get(EscalationPolicy, "pol-update")
        expect(policy.name == "Version 2")

        steps = (
            await session.scalars(
                select(EscalationStep).where(EscalationStep.policy_id == "pol-update")
            )
        ).all()
        # Old step (1 row) replaced by new step (2 rows: tgt-u1, tgt-u2)
        expect(len(steps) == 2)
        expect(all(s.step_no == 1 for s in steps))
        expect(all(s.after_seconds == 60 for s in steps))


async def test_apply_existing_policy_updates_target_fields(
    sessionmaker: async_sessionmaker, engine
):
    """Re-applying with updated target fields overwrites existing target attributes."""
    body_v1 = EscalationPolicyIn(
        policy_id="pol-tgt-upd",
        name="Target Update Test",
        targets=[_make_target(target_id="tgt-mut", label="Old Label", address="+490000000000")],
        steps=[],
    )

    async with sessionmaker() as session:
        await apply_escalation_policy(session, body_v1)

    body_v2 = EscalationPolicyIn(
        policy_id="pol-tgt-upd",
        name="Target Update Test",
        targets=[
            _make_target(
                target_id="tgt-mut",
                label="New Label",
                address="+491111111111",
                enabled=False,
            )
        ],
        steps=[],
    )

    async with sessionmaker() as session:
        await apply_escalation_policy(session, body_v2)

    async with sessionmaker() as session:
        target = await session.get(EscalationTarget, "tgt-mut")
        expect(target.label == "New Label")
        expect(target.address == "+491111111111")
        expect(target.enabled is False)


# ── Validation: duplicate target IDs in step ─────────────────────────


async def test_duplicate_target_ids_in_step_raises_validation_error(
    sessionmaker: async_sessionmaker, engine
):
    """A step with duplicate target_ids raises ValidationError."""
    body = EscalationPolicyIn(
        policy_id="pol-dup",
        name="Dup Test",
        targets=[_make_target(target_id="tgt-dup")],
        steps=[_make_step(step_no=1, after_seconds=60, target_ids=["tgt-dup", "tgt-dup"])],
    )

    async with sessionmaker() as session:
        with pytest.raises(ValidationError, match="Duplicate target ids in step 1"):
            await apply_escalation_policy(session, body)


async def test_duplicate_step_target_pair_across_steps_raises_validation_error(
    sessionmaker: async_sessionmaker, engine
):
    """Same step_no + target_id appearing in two separate StepIn entries raises ValidationError."""
    body = EscalationPolicyIn(
        policy_id="pol-cross-dup",
        name="Cross-Step Dup",
        targets=[_make_target(target_id="tgt-x")],
        steps=[
            StepIn(step_no=1, after_seconds=60, target_ids=["tgt-x"]),
            StepIn(step_no=1, after_seconds=120, target_ids=["tgt-x"]),
        ],
    )

    async with sessionmaker() as session:
        with pytest.raises(ValidationError, match="Duplicate step/target pair"):
            await apply_escalation_policy(session, body)


async def test_conflicting_delays_for_same_step_raise_validation_error(
    sessionmaker: async_sessionmaker, engine
) -> None:
    body = EscalationPolicyIn(
        policy_id="pol-conflicting-delay",
        name="Conflicting Delay",
        targets=[
            _make_target(target_id="tgt-a"),
            _make_target(target_id="tgt-b"),
        ],
        steps=[
            _make_step(step_no=1, after_seconds=60, target_ids=["tgt-a"]),
            _make_step(step_no=1, after_seconds=120, target_ids=["tgt-b"]),
        ],
    )

    async with sessionmaker() as session:
        with pytest.raises(ValidationError, match="Conflicting after_seconds"):
            await apply_escalation_policy(session, body)


@pytest.mark.parametrize(
    "values",
    [
        {"step_no": -1, "after_seconds": 60},
        {"step_no": 1, "after_seconds": -1},
    ],
)
def test_step_schema_rejects_negative_schedule_values(values: dict[str, int]) -> None:
    with pytest.raises(PydanticValidationError):
        StepIn(**values, target_ids=["tgt-a"])


def test_step_schema_rejects_empty_targets() -> None:
    with pytest.raises(PydanticValidationError):
        StepIn(step_no=1, after_seconds=60, target_ids=[])


# ── Validation: missing target references ────────────────────────────


async def test_missing_target_references_raises_validation_error(
    sessionmaker: async_sessionmaker, engine
):
    """Steps referencing targets not in the payload or DB raise ValidationError."""
    body = EscalationPolicyIn(
        policy_id="pol-miss",
        name="Missing Target Test",
        targets=[_make_target(target_id="tgt-exists")],
        steps=[
            _make_step(step_no=1, after_seconds=60, target_ids=["tgt-exists", "tgt-ghost"]),
        ],
    )

    async with sessionmaker() as session:
        with pytest.raises(ValidationError, match="Unknown escalation target ids.*tgt-ghost"):
            await apply_escalation_policy(session, body)


async def test_missing_target_only_in_step_without_targets_payload(
    sessionmaker: async_sessionmaker, engine
):
    """Steps referencing unknown targets with empty targets list raise ValidationError."""
    body = EscalationPolicyIn(
        policy_id="pol-miss2",
        name="Missing Target Test 2",
        targets=[],
        steps=[
            _make_step(step_no=1, after_seconds=60, target_ids=["tgt-nonexistent"]),
        ],
    )

    async with sessionmaker() as session:
        with pytest.raises(ValidationError, match="Unknown escalation target ids"):
            await apply_escalation_policy(session, body)


async def test_step_referencing_preexisting_db_target_succeeds(
    sessionmaker: async_sessionmaker, engine
):
    """Steps can reference targets already in the DB (not in the current payload)."""
    # Pre-seed a target into the database
    async with sessionmaker() as session:
        session.add(
            EscalationTarget(
                id="tgt-preexist",
                label="Pre-existing",
                channel="sms",
                address="+499999999999",
                enabled=True,
            )
        )
        await session.commit()

    body = EscalationPolicyIn(
        policy_id="pol-preexist",
        name="Pre-exist Test",
        targets=[],  # No new targets in payload
        steps=[
            _make_step(step_no=1, after_seconds=60, target_ids=["tgt-preexist"]),
        ],
    )

    async with sessionmaker() as session:
        result = await apply_escalation_policy(session, body)

    expect(result == "pol-preexist")

    async with sessionmaker() as session:
        steps = (
            await session.scalars(
                select(EscalationStep).where(EscalationStep.policy_id == "pol-preexist")
            )
        ).all()
        expect(len(steps) == 1)
        expect(steps[0].target_id == "tgt-preexist")
