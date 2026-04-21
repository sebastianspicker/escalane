from __future__ import annotations

import logging
import uuid
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from alarm_broker.api.deps import get_redis, get_session, require_admin
from alarm_broker.api.schemas import (
    AckIn,
    AlarmOut,
    AlarmPatchSchema,
    BulkAckIn,
    BulkOperationOut,
    BulkTransitionIn,
    TransitionIn,
)
from alarm_broker.core.errors import ConflictError
from alarm_broker.db.models import Alarm, AlarmStatus
from alarm_broker.services.alarm_service import (
    acknowledge_alarm,
    get_alarm_or_404,
    transition_alarm,
)
from alarm_broker.services.event_service import (
    enqueue_alarm_acked_event,
    enqueue_alarm_state_changed_event,
)

router = APIRouter(prefix="/v1/alarms", dependencies=[Depends(require_admin)])
logger = logging.getLogger("alarm_broker")


async def _execute_bulk_operation(
    session: AsyncSession,
    alarm_ids: list[uuid.UUID],
    process_alarm: Callable[[Alarm], Coroutine[Any, Any, bool]],
    after_change: Callable[[Alarm], Coroutine[Any, Any, None]] | None = None,
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

        try:
            was_changed = await process_alarm(alarm)
        except HTTPException as exc:
            if exc.status_code == status.HTTP_409_CONFLICT:
                unchanged += 1
                continue
            raise
        except ConflictError:
            unchanged += 1
            continue

        if was_changed:
            changed += 1
            if after_change:
                await after_change(alarm)
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
    is_ack: bool = False,
) -> BulkOperationOut:
    """Execute a bulk state transition on alarms."""
    redis = get_redis(request)

    async def process(alarm: Alarm) -> bool:
        if is_ack:
            return await acknowledge_alarm(
                session,
                alarm,
                acked_by=actor_or_acked_by,
                note=note,
            )
        return await transition_alarm(
            session,
            alarm,
            target_status=target_status,
            actor=actor_or_acked_by,
            note=note,
        )

    async def after_change(alarm: Alarm) -> None:
        if is_ack:
            ack_result = await enqueue_alarm_acked_event(
                redis,
                alarm_id=alarm.id,
                acked_by=actor_or_acked_by,
                note=note,
                logger=logger,
            )
            if not ack_result.success:
                logger.warning(
                    "bulk_ack_event_enqueue_failed",
                    extra={"alarm_id": str(alarm.id), "error": ack_result.error},
                )
        state_result = await enqueue_alarm_state_changed_event(
            redis,
            alarm_id=alarm.id,
            state=alarm.status.value,
            logger=logger,
        )
        if not state_result.success:
            logger.warning(
                "bulk_state_event_enqueue_failed",
                extra={"alarm_id": str(alarm.id), "error": state_result.error},
            )

    return await _execute_bulk_operation(
        session,
        alarm_ids,
        process,
        after_change,
    )


async def _execute_single_state_transition(
    request: Request,
    session: AsyncSession,
    alarm_id: uuid.UUID,
    *,
    target_status: AlarmStatus,
    actor_or_acked_by: str | None,
    note: str | None,
    is_ack: bool = False,
) -> Response:
    """Execute one alarm state transition and enqueue follow-up events."""
    alarm = await get_alarm_or_404(session, alarm_id)
    request.state.alarm_id = str(alarm.id)

    if is_ack:
        changed = await acknowledge_alarm(session, alarm, acked_by=actor_or_acked_by, note=note)
    else:
        changed = await transition_alarm(
            session,
            alarm,
            target_status=target_status,
            actor=actor_or_acked_by,
            note=note,
        )

    if changed:
        redis = get_redis(request)
        if is_ack:
            ack_result = await enqueue_alarm_acked_event(
                redis,
                alarm_id=alarm.id,
                acked_by=actor_or_acked_by,
                note=note,
                logger=logger,
            )
            if not ack_result.success:
                logger.warning(
                    "ack_event_enqueue_failed",
                    extra={"alarm_id": str(alarm.id), "error": ack_result.error},
                )
        state_result = await enqueue_alarm_state_changed_event(
            redis,
            alarm_id=alarm.id,
            state=alarm.status.value,
            logger=logger,
        )
        if not state_result.success:
            logger.warning(
                "state_event_enqueue_failed",
                extra={"alarm_id": str(alarm.id), "error": state_result.error},
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
        is_ack=True,
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
        is_ack=False,
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
        is_ack=False,
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

    patch_data = patch.model_dump(exclude_none=True)

    # NOTE: The meta JSON updates below are not atomic at the database level.
    # Concurrent PATCH requests to the same alarm may cause a lost-update race
    # condition where one writer overwrites the other's changes. A fully atomic
    # approach would use PostgreSQL's jsonb_set via func.jsonb_set, but the ORM
    # model currently maps meta as a plain dict. For now, callers should be aware
    # that concurrent partial updates to meta are not safe without external
    # locking (e.g., SELECT ... FOR UPDATE).
    if "title" in patch_data:
        alarm.meta = {**(alarm.meta or {}), "title": patch_data["title"]}

    if "description" in patch_data:
        alarm.meta = {**(alarm.meta or {}), "description": patch_data["description"]}

    if "severity" in patch_data:
        alarm.severity = patch_data["severity"]

    if "tags" in patch_data:
        alarm.meta = {**(alarm.meta or {}), "tags": patch_data["tags"]}

    await session.commit()
    await session.refresh(alarm)

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
        is_ack=True,
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
    from alarm_broker.api.deps import get_app_settings

    alarm = await get_alarm_or_404(session, alarm_id)

    if alarm.deleted_at is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Alarm has already been deleted. No action taken.",
        )

    alarm.deleted_at = datetime.now(UTC)

    settings = get_app_settings(request)
    if settings.admin_api_key:
        alarm.deleted_by = "admin"

    await session.commit()
