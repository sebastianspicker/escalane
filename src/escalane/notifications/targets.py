"""Escalation-target queries and schedule validation."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from escalane.config.errors import ConfigurationError
from escalane.persistence.models import EscalationStep, EscalationTarget


async def get_escalation_targets(
    session: AsyncSession,
    policy_id: str,
    step_no: int,
) -> list[EscalationTarget]:
    """Fetch enabled escalation targets for a policy step."""
    steps = (
        await session.scalars(
            select(EscalationStep)
            .options(selectinload(EscalationStep.target))
            .where(EscalationStep.policy_id == policy_id)
            .where(EscalationStep.step_no == step_no)
        )
    ).all()
    return [step.target for step in steps if step.target is not None and step.target.enabled]


async def get_escalation_schedule(
    session: AsyncSession,
    policy_id: str,
) -> list[tuple[int, int]]:
    """Return each scheduled escalation step after validating its delay."""
    rows = (
        await session.execute(
            select(EscalationStep.step_no, EscalationStep.after_seconds)
            .where(EscalationStep.policy_id == policy_id)
            .where(EscalationStep.step_no > 0)
            .distinct()
            .order_by(EscalationStep.step_no, EscalationStep.after_seconds)
        )
    ).all()
    schedule: dict[int, int] = {}
    for step_no, after_seconds in rows:
        previous_delay = schedule.setdefault(step_no, after_seconds)
        if previous_delay != after_seconds:
            raise ConfigurationError(
                f"Escalation policy {policy_id!r} has conflicting delays for step {step_no}"
            )
    return list(schedule.items())
