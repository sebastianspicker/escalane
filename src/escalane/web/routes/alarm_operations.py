"""Apply single and bulk alarm lifecycle operations with durable event dispatch."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from escalane.alarms.lifecycle import (
    AlarmPatchCommand,
    AlarmStateOutcome,
    apply_alarm_patch,
    apply_alarm_state_change,
    get_alarm_or_404,
    soft_delete_alarm,
)
from escalane.config.errors import ConflictError
from escalane.contracts.alarms import AlarmStatus
from escalane.persistence.models import Alarm
from escalane.web.deps import get_redis, get_session, require_admin
from escalane.web.schemas import (
    AckIn,
    AlarmOut,
    AlarmPatchSchema,
    BulkAckIn,
    BulkOperationOut,
    BulkTransitionIn,
    TransitionIn,
)

router = APIRouter(prefix="/v1/alarms", dependencies=[Depends(require_admin)])
logger = logging.getLogger("escalane")


async def _process_bulk_alarm(
    alarm: Alarm,
    process_alarm: Callable[[Alarm], Coroutine[Any, Any, AlarmStateOutcome]],
) -> AlarmStateOutcome | None:
    try:
        return await process_alarm(alarm)
    except HTTPException as exc:
        if exc.status_code == status.HTTP_409_CONFLICT:
            return None
        raise
    except ConflictError:
        return None


async def _execute_bulk_operation(
    session: AsyncSession,
    alarm_ids: list[uuid.UUID],
    process_alarm: Callable[[Alarm], Coroutine[Any, Any, AlarmStateOutcome]],
) -> BulkOperationOut:
    """Execute a bulk operation on alarms with common pattern.

    Each individual operation commits independently (acknowledge_alarm and
    transition_alarm already call session.commit()), so no outer
    session.begin() wrapper is used to avoid double-commit errors.
    """
    alarms = (
        await session.scalars(
            select(Alarm).where(Alarm.id.in_(alarm_ids), Alarm.deleted_at.is_(None))
        )
    ).all()
    by_id = {alarm.id: alarm for alarm in alarms}

    changed = 0
    unchanged = 0
    missing: list[uuid.UUID] = []

    for alarm_id in alarm_ids:
        alarm = by_id.get(alarm_id)
        if alarm is None:
            missing.append(alarm_id)
            continue

        outcome = await _process_bulk_alarm(alarm, process_alarm)
        if outcome is None:
            unchanged += 1
            continue
        if outcome.pending:
            logger.warning(
                "bulk_event_delivery_pending",
                extra={
                    "alarm_id": str(alarm.id),
                    "published": outcome.published,
                },
            )
        if outcome.changed:
            changed += 1
        else:
            unchanged += 1

    return BulkOperationOut(
        requested=len(alarm_ids),
        changed=changed,
        unchanged=unchanged,
        missing=missing,
    )


async def _execute_bulk_state_transition(
    alarm_ids: list[uuid.UUID],
    target_status: AlarmStatus,
    request: Request,
    session: AsyncSession,
    actor_or_acked_by: str | None,
    note: str | None,
) -> BulkOperationOut:
    """Execute a bulk state transition on alarms."""
    redis = get_redis(request)

    async def process(alarm: Alarm) -> AlarmStateOutcome:
        """Apply the shared target state while reusing this request's resources."""
        return await apply_alarm_state_change(
            session,
            redis,
            alarm,
            target_status=target_status,
            actor=actor_or_acked_by,
            note=note,
            logger=logger,
        )

    return await _execute_bulk_operation(
        session,
        alarm_ids,
        process,
    )


async def _execute_single_state_transition(
    request: Request,
    session: AsyncSession,
    alarm_id: uuid.UUID,
    *,
    target_status: AlarmStatus,
    actor_or_acked_by: str | None,
    note: str | None,
) -> Response:
    """Execute one alarm state transition and enqueue follow-up events."""
    alarm = await get_alarm_or_404(session, alarm_id)
    request.state.alarm_id = str(alarm.id)

    outcome = await apply_alarm_state_change(
        session,
        get_redis(request),
        alarm,
        target_status=target_status,
        actor=actor_or_acked_by,
        note=note,
        logger=logger,
    )
    if outcome.pending:
        logger.warning(
            "event_delivery_pending",
            extra={
                "alarm_id": str(alarm.id),
                "published": outcome.published,
            },
        )

    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/bulk/ack", response_model=BulkOperationOut)
async def bulk_ack_alarms(
    request: Request,
    body: BulkAckIn,
    session: AsyncSession = Depends(get_session),
) -> BulkOperationOut:
    """Acknowledge multiple alarms in bulk."""
    return await _execute_bulk_state_transition(
        alarm_ids=body.alarm_ids,
        target_status=AlarmStatus.ACKNOWLEDGED,
        request=request,
        session=session,
        actor_or_acked_by=body.acked_by,
        note=body.note,
    )


@router.post("/bulk/resolve", response_model=BulkOperationOut)
async def bulk_resolve_alarms(
    request: Request,
    body: BulkTransitionIn,
    session: AsyncSession = Depends(get_session),
) -> BulkOperationOut:
    """Resolve multiple alarms in bulk."""
    return await _execute_bulk_state_transition(
        alarm_ids=body.alarm_ids,
        target_status=AlarmStatus.RESOLVED,
        request=request,
        session=session,
        actor_or_acked_by=body.actor,
        note=body.note,
    )


@router.post("/bulk/cancel", response_model=BulkOperationOut)
async def bulk_cancel_alarms(
    request: Request,
    body: BulkTransitionIn,
    session: AsyncSession = Depends(get_session),
) -> BulkOperationOut:
    """Cancel multiple alarms in bulk."""
    return await _execute_bulk_state_transition(
        alarm_ids=body.alarm_ids,
        target_status=AlarmStatus.CANCELLED,
        request=request,
        session=session,
        actor_or_acked_by=body.actor,
        note=body.note,
    )


@router.get("/{alarm_id}", response_model=AlarmOut)
async def get_alarm(
    alarm_id: uuid.UUID,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> AlarmOut:
    """Get a single alarm by ID."""
    alarm = await get_alarm_or_404(session, alarm_id)
    request.state.alarm_id = str(alarm.id)
    return AlarmOut.model_validate(alarm, from_attributes=True)


@router.patch("/{alarm_id}", response_model=AlarmOut)
async def patch_alarm(
    alarm_id: uuid.UUID,
    patch: AlarmPatchSchema,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> AlarmOut:
    """Partial update of an alarm.

    Only updates fields present in the request body.
    None values are ignored.
    """
    alarm = await get_alarm_or_404(session, alarm_id)
    request.state.alarm_id = str(alarm.id)

    await apply_alarm_patch(
        session,
        alarm,
        AlarmPatchCommand(
            title=patch.title,
            description=patch.description,
            severity=patch.severity,
            tags=tuple(patch.tags) if patch.tags is not None else None,
        ),
    )

    return AlarmOut.model_validate(alarm, from_attributes=True)


@router.post("/{alarm_id}/ack")
async def ack_alarm_api(
    request: Request,
    alarm_id: uuid.UUID,
    body: AckIn,
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Acknowledge an alarm by ID."""
    return await _execute_single_state_transition(
        request,
        session,
        alarm_id,
        target_status=AlarmStatus.ACKNOWLEDGED,
        actor_or_acked_by=body.acked_by,
        note=body.note,
    )


@router.post("/{alarm_id}/resolve")
async def resolve_alarm_api(
    request: Request,
    alarm_id: uuid.UUID,
    body: TransitionIn,
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Resolve an alarm by ID."""
    return await _execute_single_state_transition(
        request,
        session,
        alarm_id,
        target_status=AlarmStatus.RESOLVED,
        actor_or_acked_by=body.actor,
        note=body.note,
    )


@router.post("/{alarm_id}/cancel")
async def cancel_alarm_api(
    request: Request,
    alarm_id: uuid.UUID,
    body: TransitionIn,
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Cancel an alarm by ID."""
    return await _execute_single_state_transition(
        request,
        session,
        alarm_id,
        target_status=AlarmStatus.CANCELLED,
        actor_or_acked_by=body.actor,
        note=body.note,
    )


@router.delete("/{alarm_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_alarm(
    alarm_id: uuid.UUID,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> None:
    """Soft-delete an alarm."""
    from escalane.web.deps import get_app_settings

    alarm = await get_alarm_or_404(session, alarm_id)

    settings = get_app_settings(request)
    await soft_delete_alarm(
        session,
        alarm,
        deleted_by="admin" if settings.admin_api_key else None,
    )
