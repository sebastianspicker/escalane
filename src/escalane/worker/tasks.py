"""arq worker tasks: alarm fan-out, escalation, ACK handling, webhooks."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from functools import wraps
from typing import Any, NoReturn, Protocol, TypedDict, cast

import httpx
from arq import Retry
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from escalane.alarms.enrichment import enrich_alarm_context
from escalane.alarms.outbox import (
    dispatch_pending_alarm_events,
    record_published_alarm_event_failure,
)
from escalane.config import constants
from escalane.config.settings import Settings
from escalane.contracts.alarms import AlarmStatus
from escalane.notifications import targets as notification_targets
from escalane.notifications.delivery import (
    NotificationDeliveryError,
    log_notification,
)
from escalane.notifications.dispatch import NotificationService
from escalane.notifications.recovery import rearm_stale_acknowledgement_events
from escalane.notifications.workflows import (
    ack_note_delivery_error,
    ack_url_for_alarm,
    deliver_initial_notifications,
    deliver_state_webhook,
    restore_zammad_ticket_id,
)
from escalane.operations.metrics import record_event
from escalane.persistence.models import Alarm
from escalane.providers.base import SignalGroupProvider, SmsProvider, ZammadTicketProvider
from escalane.security.url_validation import RetryableSSRFError

logger = logging.getLogger("escalane")
MAX_DELIVERY_ATTEMPTS = 5
WorkerTask = Callable[..., Coroutine[Any, Any, None]]


class JobEnqueuer(Protocol):
    """Subset of an ARQ Redis client used by worker tasks."""

    async def enqueue_job(self, name: str, *args: object, **kwargs: object) -> object | None:
        """Enqueue one job, returning its handle when accepted."""


class WorkerContext(TypedDict, total=False):
    """Resources initialized by the ARQ worker lifecycle hooks."""

    settings: Settings
    engine: AsyncEngine
    sessionmaker: async_sessionmaker[AsyncSession]
    http: httpx.AsyncClient
    redis: JobEnqueuer
    zammad: ZammadTicketProvider
    sendxms: SmsProvider
    signal: SignalGroupProvider
    job_try: int


class _RecordedDeliveryFailure(NotificationDeliveryError):
    """Terminal failure whose telemetry was already emitted by the task body."""


async def _load_active_alarm(
    session: AsyncSession, alarm_id: uuid.UUID, *, log_extra: dict[str, Any]
) -> Alarm | None:
    """Load an alarm unless it was removed, logging a worker-side no-op condition."""
    alarm = cast(Alarm | None, await session.get(Alarm, alarm_id))
    if not alarm:
        logger.warning("alarm_not_found", extra=log_extra)
        return None
    if alarm.deleted_at is not None:
        logger.info("alarm_deleted", extra=log_extra)
        return None
    return alarm


async def _enqueue_escalations(
    redis: JobEnqueuer, schedule: list[tuple[int, int]], *, alarm_id: str
) -> None:
    """Queue deterministic escalation jobs before external notification effects."""
    for step_no, after_seconds in schedule:
        try:
            await redis.enqueue_job(
                "escalate",
                alarm_id,
                step_no,
                _defer_by=int(after_seconds),
                _job_id=f"escalate:{alarm_id}:{step_no}",
            )
        except Exception as exc:
            raise NotificationDeliveryError("Escalation scheduling failed") from exc
        logger.info(
            "escalation_scheduled",
            extra={"alarm_id": alarm_id, "step_no": step_no, "after_seconds": after_seconds},
        )


def _record_delivery_attempt_failure(ctx: WorkerContext, *, operation: str, alarm_id: str) -> None:
    """Expose retry versus exhausted delivery failures to logs and metrics."""
    raw_attempt = ctx.get("job_try", 1)
    attempt = raw_attempt if isinstance(raw_attempt, int) and raw_attempt > 0 else 1
    exhausted = attempt >= MAX_DELIVERY_ATTEMPTS
    event = "notification_delivery_exhausted" if exhausted else "notification_delivery_retry"
    record_event(event)
    log = logger.error if exhausted else logger.warning
    log(
        event,
        extra={"alarm_id": alarm_id, "operation": operation, "attempt": attempt},
    )


def _raise_delivery_retry(
    ctx: WorkerContext,
    *,
    operation: str,
    alarm_id: str,
    cause: Exception,
    source: Exception | None = None,
) -> NoReturn:
    """Ask ARQ to retry a transient delivery failure within its configured budget."""
    raw_attempt = ctx.get("job_try", 1)
    attempt = raw_attempt if isinstance(raw_attempt, int) and raw_attempt > 0 else 1
    _record_delivery_attempt_failure(ctx, operation=operation, alarm_id=alarm_id)
    if attempt >= MAX_DELIVERY_ATTEMPTS:
        terminal = _RecordedDeliveryFailure(str(cause))
        raise terminal from (source or cause)
    retry = Retry(defer=min(2 ** (attempt - 1), 60))
    if source is None:
        raise retry from cause
    raise retry from source


def _retry_delivery_errors(operation: str) -> Callable[[WorkerTask], WorkerTask]:
    """Translate transient delivery and database failures at ARQ task boundaries."""

    def decorate(task: WorkerTask) -> WorkerTask:
        """Wrap one worker task without changing its externally registered identity."""

        @wraps(task)
        async def wrapped(ctx: WorkerContext, alarm_id: str, *args: Any, **kwargs: Any) -> None:
            """Retry transient failures while preserving explicit terminal failures."""
            try:
                await task(ctx, alarm_id, *args, **kwargs)
            except _RecordedDeliveryFailure:
                raise
            except (NotificationDeliveryError, SQLAlchemyError) as exc:
                _raise_delivery_retry(
                    ctx,
                    operation=operation,
                    alarm_id=alarm_id,
                    cause=exc,
                )

        return wrapped

    return decorate


async def _record_terminal_acknowledgement_failure(
    ctx: WorkerContext, alarm_id: str, error: _RecordedDeliveryFailure
) -> None:
    """Persist terminal ACK delivery failure without masking its retry outcome."""
    try:
        async with ctx["sessionmaker"]() as session:
            recorded = await record_published_alarm_event_failure(
                session,
                alarm_id=uuid.UUID(alarm_id),
                event_type=constants.EVENT_ALARM_ACKNOWLEDGED,
                error=str(error),
            )
    except SQLAlchemyError:
        logger.exception(
            "alarm_acknowledgement_failure_record_failed", extra={"alarm_id": alarm_id}
        )
        return
    logger.error(
        "alarm_acknowledgement_failure_recorded",
        extra={"alarm_id": alarm_id, "outbox_events": recorded},
    )


async def _process_acknowledged_event(
    ctx: WorkerContext, alarm_id: str, payload: dict[str, Any]
) -> None:
    """Deliver one ACK event and retain terminal failures for stale-event recovery."""
    acked_by = payload.get("acknowledged_by")
    note = payload.get("note")
    try:
        await alarm_acked(
            ctx, alarm_id, str(acked_by) if acked_by else None, str(note) if note else None
        )
    except _RecordedDeliveryFailure as exc:
        await _record_terminal_acknowledgement_failure(ctx, alarm_id, exc)
        raise


async def process_alarm_event(ctx: WorkerContext, payload: dict[str, Any]) -> None:
    """Process a generic alarm event from the durable alarm outbox.

    This is the worker-side half of the alarm-event queue contract. The current
    event types are `alarm.created`, `alarm.acknowledged`, and
    `alarm.state_changed`; resolved/cancelled lifecycle updates arrive through
    `alarm.state_changed` with the new state in the payload.

    Args:
        ctx: Worker context dictionary
        payload: Event payload containing event_type and event data
    """
    event_type = payload.get("event_type")
    alarm_id = payload.get("alarm_id")

    if not event_type or not alarm_id:
        logger.warning("process_alarm_event_invalid_payload", extra={"payload": payload})
        return

    if event_type == constants.EVENT_ALARM_CREATED:
        await alarm_created(ctx, str(alarm_id))
    elif event_type == constants.EVENT_ALARM_ACKNOWLEDGED:
        await _process_acknowledged_event(ctx, str(alarm_id), payload)
    elif event_type == constants.EVENT_ALARM_STATE_CHANGED:
        state = payload.get("new_state", "")
        await alarm_state_changed(ctx, str(alarm_id), str(state))
    else:
        logger.warning(
            "process_alarm_event_unknown_type",
            extra={"event_type": event_type, "alarm_id": str(alarm_id)},
        )


def _get_notification_service(ctx: WorkerContext) -> NotificationService:
    """Get notification service from worker context.

    Args:
        ctx: Worker context dictionary

    Returns:
        NotificationService instance
    """
    return NotificationService(
        zammad=ctx["zammad"],
        sendxms=ctx["sendxms"],
        signal=ctx["signal"],
    )


@_retry_delivery_errors("alarm_created")
async def alarm_created(ctx: WorkerContext, alarm_id: str) -> None:
    """Schedule escalations, then perform retry-safe initial notification delivery."""
    notification = _get_notification_service(ctx)
    async with ctx["sessionmaker"]() as session:
        alarm = await _load_active_alarm(
            session, uuid.UUID(alarm_id), log_extra={"alarm_id": alarm_id}
        )
        if alarm is None:
            return
        schedule = await notification_targets.get_escalation_schedule(session, "default")
        await _enqueue_escalations(ctx["redis"], schedule, alarm_id=alarm_id)
        enriched = await enrich_alarm_context(session, alarm)
        settings = ctx["settings"]
        ack_url = ack_url_for_alarm(alarm, settings, alarm_id=alarm_id)
        await restore_zammad_ticket_id(session, alarm)
        error = await deliver_initial_notifications(
            session,
            alarm,
            notification=notification,
            enriched=enriched,
            ack_url=ack_url,
            settings=settings,
        )
        if error:
            _raise_delivery_retry(ctx, operation="alarm_created", alarm_id=alarm_id, cause=error)


@_retry_delivery_errors("escalate")
async def escalate(ctx: WorkerContext, alarm_id: str, step_no: int) -> None:
    """Execute an escalation step.

    This task is scheduled by alarm_created for future execution.
    It only sends notifications if the alarm is still in triggered state.

    Args:
        ctx: Worker context with sessionmaker, settings, and connectors
        alarm_id: UUID string of the alarm
        step_no: Escalation step number to execute
    """
    settings = ctx["settings"]
    notification = _get_notification_service(ctx)
    extra = {"alarm_id": alarm_id, "step_no": step_no}
    async with ctx["sessionmaker"]() as session:
        alarm = await _load_active_alarm(session, uuid.UUID(alarm_id), log_extra=extra)
        if alarm is None:
            return

        if alarm.status != AlarmStatus.TRIGGERED:
            logger.info(
                "escalation_skipped",
                extra={
                    "alarm_id": alarm_id,
                    "step_no": step_no,
                    "status": alarm.status.value,
                },
            )
            return

        enriched = await enrich_alarm_context(session, alarm)

        ack_url = ack_url_for_alarm(alarm, settings, alarm_id=alarm_id)

        try:
            await notification.send(
                session=session,
                alarm=alarm,
                enriched=enriched,
                step_no=step_no,
                ack_url=ack_url,
                settings=settings,
            )
        except NotificationDeliveryError as exc:
            _raise_delivery_retry(
                ctx,
                operation=f"escalate:{step_no}",
                alarm_id=alarm_id,
                cause=exc,
            )

        logger.info(
            "escalation_completed",
            extra={"alarm_id": alarm_id, "step_no": step_no},
        )


@_retry_delivery_errors("alarm_acked")
async def alarm_acked(
    ctx: WorkerContext, alarm_id: str, acked_by: str | None = None, note: str | None = None
) -> None:
    """Deliver an alarm acknowledgment note to its Zammad ticket."""
    zammad = ctx["zammad"]
    async with ctx["sessionmaker"]() as session:
        alarm = await _load_active_alarm(
            session, uuid.UUID(alarm_id), log_extra={"alarm_id": alarm_id}
        )
        if alarm is None:
            return

        if not zammad.enabled():
            logger.debug("zammad_disabled", extra={"alarm_id": alarm_id})
            return

        if not alarm.zammad_ticket_id:
            logger.warning(
                "ack_no_zammad_ticket",
                extra={
                    "alarm_id": alarm_id,
                    "detail": "Zammad ticket ID is None; ACK note will not be sent. "
                    "This may indicate a prior Zammad ticket creation failure.",
                },
            )
            _raise_delivery_retry(
                ctx,
                operation="alarm_acked",
                alarm_id=alarm_id,
                cause=NotificationDeliveryError("Zammad ticket creation is incomplete"),
            )

        notification = _get_notification_service(ctx)
        success = await notification.add_zammad_ack_note(
            session,
            alarm_id=alarm.id,
            ticket_id=alarm.zammad_ticket_id,
            acked_by=acked_by,
            acked_at=alarm.acked_at or datetime.now(UTC),
            note=note,
        )

        delivery_error = ack_note_delivery_error(
            success, alarm_id=alarm_id, ticket_id=alarm.zammad_ticket_id
        )
        if delivery_error is not None:
            _raise_delivery_retry(
                ctx,
                operation="alarm_acked",
                alarm_id=alarm_id,
                cause=delivery_error,
            )


@_retry_delivery_errors("alarm_state_changed")
async def alarm_state_changed(ctx: WorkerContext, alarm_id: str, state: str) -> None:
    """Send state-change webhook callbacks with retry and audit logging.

    Args:
        ctx: Worker context with settings, sessionmaker and HTTP client
        alarm_id: UUID string of the alarm
        state: New alarm state value
    """
    settings = ctx["settings"]
    if not settings.is_webhook_enabled():
        return

    extra = {"alarm_id": alarm_id, "state": state, "channel": "webhook"}
    async with ctx["sessionmaker"]() as session:
        alarm = await _load_active_alarm(session, uuid.UUID(alarm_id), log_extra=extra)
        if alarm is None:
            return
        try:
            await deliver_state_webhook(
                session,
                alarm,
                state=state,
                settings=settings,
                http=ctx["http"],
                log_notification=log_notification,
            )
        except RetryableSSRFError as exc:
            _raise_delivery_retry(
                ctx,
                operation=f"alarm_state_changed:{state}",
                alarm_id=alarm_id,
                cause=NotificationDeliveryError("Webhook DNS resolution failed"),
                source=exc,
            )
        except NotificationDeliveryError as exc:
            _raise_delivery_retry(
                ctx, operation=f"alarm_state_changed:{state}", alarm_id=alarm_id, cause=exc
            )


async def recover_incomplete_alarm_events(ctx: WorkerContext) -> None:
    """Rearm stale ACK work and publish pending durable lifecycle events."""
    sessionmaker = ctx["sessionmaker"]
    redis = ctx["redis"]
    batch_size = 500

    async with sessionmaker() as session:
        zammad = ctx.get("zammad")
        if zammad is not None and zammad.enabled():
            rearmed = await rearm_stale_acknowledgement_events(session, limit=batch_size)
            if rearmed:
                logger.warning("stale_alarm_acknowledgements_rearmed", extra={"count": rearmed})

        published = await dispatch_pending_alarm_events(
            session, redis, logger=logger, limit=batch_size
        )
        if published:
            logger.info("alarm_event_outbox_recovered", extra={"published": published})
