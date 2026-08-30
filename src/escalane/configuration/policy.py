"""Validate and atomically replace versioned escalation policies."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from escalane.config.errors import ConflictError, ValidationError
from escalane.persistence.models import EscalationPolicy, EscalationStep, EscalationTarget


@dataclass(frozen=True, slots=True)
class TargetCommand:
    """Describe one escalation destination for a policy replacement."""

    id: str
    label: str
    channel: str
    address: str
    enabled: bool = True


@dataclass(frozen=True, slots=True)
class StepCommand:
    """Describe one delay and its escalation targets."""

    step_no: int
    after_seconds: int
    target_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class EscalationPolicyCommand:
    """Describe a complete versioned escalation-policy replacement."""

    policy_id: str
    name: str
    targets: tuple[TargetCommand, ...]
    steps: tuple[StepCommand, ...]


async def _upsert_policy(
    session: AsyncSession,
    command: EscalationPolicyCommand,
    *,
    expected_version: int | None,
) -> None:
    """Create or atomically advance the policy version before replacing its contents."""
    if expected_version == 0:
        session.add(EscalationPolicy(id=command.policy_id, name=command.name, version=1))
        try:
            await session.flush()
        except IntegrityError as exc:
            await session.rollback()
            raise ConflictError("Policy has changed since it was loaded") from exc
        return

    conditions = [EscalationPolicy.id == command.policy_id]
    if expected_version is not None:
        conditions.append(EscalationPolicy.version == expected_version)

    result = await session.execute(
        update(EscalationPolicy)
        .where(*conditions)
        .values(name=command.name, version=EscalationPolicy.version + 1)
    )
    if bool(getattr(result, "rowcount", 0)):
        return

    if expected_version is not None:
        raise ConflictError("Policy has changed since it was loaded")

    # The API upsert has no client-supplied version. Create only when no row
    # exists; a concurrent creator is retried by the caller as an update.
    session.add(EscalationPolicy(id=command.policy_id, name=command.name, version=1))
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise ConflictError("Policy changed while it was being updated") from exc


async def _upsert_policy_targets(session: AsyncSession, targets: tuple[TargetCommand, ...]) -> None:
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


def _validate_step_duplicates(steps: tuple[StepCommand, ...]) -> None:
    seen_pairs: set[tuple[int, str]] = set()
    delays_by_step: dict[int, int] = {}
    for step in steps:
        if len(step.target_ids) != len(set(step.target_ids)):
            raise ValidationError(f"Duplicate target ids in step {step.step_no}")

        for target_id in step.target_ids:
            pair = (step.step_no, target_id)
            if pair in seen_pairs:
                duplicate_pair = f"step {step.step_no}, target {target_id}"
                raise ValidationError(f"Duplicate step/target pair: {duplicate_pair}")
            seen_pairs.add(pair)

        previous_delay = delays_by_step.setdefault(step.step_no, step.after_seconds)
        if previous_delay != step.after_seconds:
            raise ValidationError(
                f"Conflicting after_seconds values for step {step.step_no}: "
                f"{previous_delay} and {step.after_seconds}"
            )


async def _validate_referenced_targets(
    session: AsyncSession,
    *,
    targets: tuple[TargetCommand, ...],
    steps: tuple[StepCommand, ...],
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
    steps: tuple[StepCommand, ...],
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


async def apply_escalation_policy(
    session: AsyncSession,
    command: EscalationPolicyCommand,
    *,
    expected_version: int | None = None,
) -> str:
    """Persist a policy and advance its version for every successful write.

    Browser edits provide ``expected_version`` for compare-and-swap semantics;
    API upserts omit it and atomically advance the current version instead.
    """
    await _upsert_policy(session, command, expected_version=expected_version)
    await _upsert_policy_targets(session, command.targets)
    _validate_step_duplicates(command.steps)
    await _validate_referenced_targets(session, targets=command.targets, steps=command.steps)
    await _replace_policy_steps(session, policy_id=command.policy_id, steps=command.steps)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ConflictError("Policy references changed while it was being updated") from exc
    return command.policy_id
