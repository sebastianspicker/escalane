from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from alarm_broker.api.schemas import (
    EscalationPolicyIn,  # acceptable: schema is the canonical policy definition
    StepIn,
    TargetIn,
)
from alarm_broker.core.errors import ValidationError
from alarm_broker.db.models import EscalationPolicy, EscalationStep, EscalationTarget


async def _upsert_policy(session: AsyncSession, body: EscalationPolicyIn) -> None:
    policy = await session.get(EscalationPolicy, body.policy_id)
    if not policy:
        policy = EscalationPolicy(id=body.policy_id, name=body.name)
        session.add(policy)
    else:
        policy.name = body.name


async def _upsert_policy_targets(session: AsyncSession, targets: list[TargetIn]) -> None:
    for target_in in targets:
        target = await session.get(EscalationTarget, target_in.id)
        if not target:
            session.add(
                EscalationTarget(
                    id=target_in.id,
                    label=target_in.label,
                    channel=target_in.channel,
                    address=target_in.address,
                    enabled=target_in.enabled,
                )
            )
            continue

        target.label = target_in.label
        target.channel = target_in.channel
        target.address = target_in.address
        target.enabled = target_in.enabled


def _validate_step_duplicates(steps: list[StepIn]) -> None:
    seen_pairs: set[tuple[int, str]] = set()
    for step in steps:
        if len(step.target_ids) != len(set(step.target_ids)):
            raise ValidationError(f"Duplicate target ids in step {step.step_no}")

        for target_id in step.target_ids:
            pair = (step.step_no, target_id)
            if pair in seen_pairs:
                duplicate_pair = f"step {step.step_no}, target {target_id}"
                raise ValidationError(f"Duplicate step/target pair: {duplicate_pair}")
            seen_pairs.add(pair)


async def _validate_referenced_targets(
    session: AsyncSession,
    *,
    targets: list[TargetIn],
    steps: list[StepIn],
) -> None:
    incoming_target_ids = {target.id for target in targets}
    referenced_target_ids = {target_id for step in steps for target_id in step.target_ids}
    if referenced_target_ids:
        existing_target_ids = set(
            await session.scalars(
                select(EscalationTarget.id).where(EscalationTarget.id.in_(referenced_target_ids))
            )
        )
        missing_target_ids = referenced_target_ids - incoming_target_ids - existing_target_ids
        if missing_target_ids:
            missing_targets = ", ".join(sorted(missing_target_ids))
            raise ValidationError(f"Unknown escalation target ids: {missing_targets}")


async def _replace_policy_steps(
    session: AsyncSession,
    *,
    policy_id: str,
    steps: list[StepIn],
) -> None:
    existing_steps = await session.scalars(
        select(EscalationStep).where(EscalationStep.policy_id == policy_id)
    )
    for existing_step in existing_steps:
        await session.delete(existing_step)

    for step in steps:
        for target_id in step.target_ids:
            session.add(
                EscalationStep(
                    policy_id=policy_id,
                    step_no=step.step_no,
                    after_seconds=step.after_seconds,
                    target_id=target_id,
                )
            )


async def apply_escalation_policy(session: AsyncSession, body: EscalationPolicyIn) -> str:
    await _upsert_policy(session, body)
    await _upsert_policy_targets(session, body.targets)
    _validate_step_duplicates(body.steps)
    await _validate_referenced_targets(session, targets=body.targets, steps=body.steps)
    await _replace_policy_steps(session, policy_id=body.policy_id, steps=body.steps)
    await session.commit()
    return body.policy_id
