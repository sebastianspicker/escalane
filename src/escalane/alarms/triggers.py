"""Alarm trigger processing: idempotency, rate limiting, device validation, alarm creation."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import secrets
import uuid
from datetime import UTC, datetime
from typing import Any

from arq.connections import ArqRedis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from escalane.alarms.contracts import TriggerResult
from escalane.alarms.outbox import (
    dispatch_pending_alarm_events,
    has_pending_alarm_events,
)
from escalane.config import constants
from escalane.config.settings import Settings
from escalane.contracts.alarms import AlarmStatus
from escalane.persistence.models import (
    Alarm,
    AlarmEventOutbox,
    Device,
    Person,
    Room,
    Site,
)
from escalane.runtime.rate_limit import rate_limit_key
from escalane.runtime.redis_atomic import compare_and_delete, increment_with_expiry, redis_text

logger = logging.getLogger("escalane")


def _hash_token_for_logging(token: str) -> str:
    """Create a safe hash of the token for logging purposes."""
    return hashlib.sha256(token.encode()).hexdigest()[:16]


def _initial_alarm_outbox_events(alarm: Alarm) -> list[AlarmEventOutbox]:
    """Build the ordered lifecycle events persisted with a new alarm."""
    return [
        AlarmEventOutbox(
            alarm_id=alarm.id,
            event_type=constants.EVENT_ALARM_CREATED,
            payload={},
            sequence=0,
        ),
        AlarmEventOutbox(
            alarm_id=alarm.id,
            event_type=constants.EVENT_ALARM_STATE_CHANGED,
            payload={
                "old_state": "none",
                "new_state": AlarmStatus.TRIGGERED.value,
            },
            sequence=1,
        ),
    ]


def _valid_alarm_reservation(value: Any) -> bool:
    """Return whether a Redis value names an existing alarm reservation."""
    value_text = redis_text(value)
    if value_text is None:
        return False
    try:
        uuid.UUID(value_text)
    except ValueError:
        return False
    return True


class TriggerService:
    """Service for idempotent, rate-limited alarm triggers."""

    _IDEMPOTENCY_LOOKUP_ATTEMPTS = 5
    _IDEMPOTENCY_LOOKUP_DELAY_SECONDS = 0.05

    def __init__(
        self,
        session: AsyncSession,
        redis: ArqRedis,
        settings: Settings,
        rate_limit_bucket: int | None = None,
    ) -> None:
        """Initialize the trigger service.

        Args:
            session: Database session
            redis: Redis connection for idempotency/rate limiting
            settings: Application settings
            rate_limit_bucket: Optional pre-computed rate limit bucket
        """
        self._session = session
        self._redis = redis
        self._settings = settings
        self._rate_limit_bucket = rate_limit_bucket

    def _get_idempotency_key(self, token: str) -> str:
        """Get the Redis key for idempotency checking.

        Args:
            token: Device token

        Returns:
            Redis key string
        """
        # The reservation has a 30-second TTL. Including a shorter, 10-second
        # time bucket in its key let a duplicate cross a bucket boundary and
        # reserve a second alarm. Hash the token so the Redis key remains safe
        # to inspect without exposing the bearer credential itself.
        token_digest = hashlib.sha256(token.encode()).hexdigest()
        return f"idemp:{token_digest}"

    def _get_rate_limit_key(self, token: str) -> str:
        """Get the Redis key for rate limiting.

        Args:
            token: Device token

        Returns:
            Redis key string
        """
        if self._rate_limit_bucket is None:
            raise RuntimeError("Rate limit bucket is not set")
        return rate_limit_key(token, self._rate_limit_bucket)

    async def _compare_and_delete(self, key: str, expected: Any) -> bool:
        """Delete ``key`` atomically only while it still contains ``expected``."""
        return await compare_and_delete(self._redis, key, expected)

    async def check_idempotency(self, token: str) -> tuple[bool, uuid.UUID | None]:
        """Check if this request is idempotent (duplicate).

        Args:
            token: Device token

        Returns:
            Tuple of (is_duplicate, existing_alarm_id)
        """
        idem_key = self._get_idempotency_key(token)

        existing_alarm_id = await self._redis.get(idem_key)
        if existing_alarm_id is None:
            return False, None

        existing_alarm_text = redis_text(existing_alarm_id)
        if existing_alarm_text is None:
            await self._compare_and_delete(idem_key, existing_alarm_id)
            return False, None

        try:
            existing_uuid = uuid.UUID(existing_alarm_text)
        except ValueError:
            # Invalid UUID in Redis: clear only the value that was inspected.
            await self._compare_and_delete(idem_key, existing_alarm_id)
            return False, None

        return True, existing_uuid

    async def reserve_alarm_id(self, token: str) -> uuid.UUID | None:
        """Reserve an alarm ID for idempotency.

        Args:
            token: Device token

        Returns:
            Reserved UUID or None if reservation failed
        """
        idem_key = self._get_idempotency_key(token)

        for _attempt in range(3):
            reserved_id = uuid.uuid4()
            ok = await self._redis.set(idem_key, str(reserved_id), ex=30, nx=True)
            if ok:
                return reserved_id
            existing = await self._redis.get(idem_key)
            if existing is None:
                continue
            if _valid_alarm_reservation(existing):
                return None
            # Retry only after atomically removing the corrupt value we inspected.
            await self._compare_and_delete(idem_key, existing)
        return None

    async def clear_idempotency(self, token: str, alarm_id: uuid.UUID) -> None:
        """Clear this request's idempotency reservation on error.

        Args:
            token: Device token
            alarm_id: Reserved alarm ID that must still own the key
        """
        idem_key = self._get_idempotency_key(token)
        await self._compare_and_delete(idem_key, str(alarm_id))

    async def check_rate_limit(self, token: str) -> bool:
        """Check if the request is within rate limits.

        Args:
            token: Device token

        Returns:
            True if within limits, False if exceeded
        """
        if self._rate_limit_bucket is None:
            return True
        rl_key = self._get_rate_limit_key(token)
        rl_val = await increment_with_expiry(self._redis, rl_key, 70)
        return rl_val <= self._settings.rate_limit_per_minute

    async def validate_device(self, token: str) -> tuple[Device | None, str | None]:
        """Validate device token and get device.

        Args:
            token: Device token

        Returns:
            Tuple of (device, error_message)
        """
        device = await self._session.scalar(select(Device).where(Device.device_token == token))
        if device is None:
            return None, "Unknown token"
        if not device.person_id or not device.room_id:
            return None, "Device mapping incomplete"
        if device.active is False:
            return None, "Device mapping incomplete"
        if not await self._device_context_active(device):
            return None, "Device mapping incomplete"
        return device, None

    async def _device_context_active(self, device: Device) -> bool:
        """Reject devices whose mapped person, room, or site has been deactivated."""
        person = await self._session.get(Person, device.person_id)
        room = await self._session.get(Room, device.room_id)
        if person is not None and person.active is False:
            return False
        if room is not None and room.active is False:
            return False
        if room is None:
            return True
        site = await self._session.get(Site, room.site_id)
        return site is None or site.active is not False

    async def create_alarm(
        self,
        device: Device,
        alarm_id: uuid.UUID,
        client_ip: str,
        user_agent: str,
        event: str | None = None,
        request_id: str | None = None,
    ) -> Alarm:
        """Create a new alarm.

        Args:
            device: Device that triggered the alarm
            alarm_id: Pre-reserved alarm ID
            client_ip: Client IP address
            user_agent: User agent string
            event: Event type (optional)

        Returns:
            Created alarm instance
        """
        room = await self._session.get(Room, device.room_id)
        site_id = room.site_id if room else None

        ack_token = secrets.token_urlsafe(32)
        now = datetime.now(UTC)
        device.last_seen_at = now

        alarm = Alarm(
            id=alarm_id,
            status=AlarmStatus.TRIGGERED,
            source="yealink",
            event=event or "alarm.trigger",
            person_id=device.person_id,
            room_id=device.room_id,
            site_id=site_id,
            device_id=device.id,
            severity=constants.DEFAULT_SEVERITY,
            silent=True,
            ack_token=ack_token,
            meta={
                "received_at": now.isoformat(),
                "client_ip": client_ip,
                "user_agent": user_agent,
                **({"request_id": request_id} if request_id else {}),
                "idempotency": {"key": self._get_idempotency_key(device.device_token)},
            },
        )
        self._session.add_all([alarm, *_initial_alarm_outbox_events(alarm)])
        await self._session.commit()

        return alarm

    async def _load_alarm_with_retry(self, alarm_id: uuid.UUID) -> Alarm | None:
        """Bridge the short Redis-reservation/DB-commit window for duplicate triggers."""
        for attempt in range(self._IDEMPOTENCY_LOOKUP_ATTEMPTS):
            alarm = await self._session.get(Alarm, alarm_id)
            if alarm is not None:
                return alarm
            if attempt < self._IDEMPOTENCY_LOOKUP_ATTEMPTS - 1:
                await asyncio.sleep(self._IDEMPOTENCY_LOOKUP_DELAY_SECONDS)
        return None

    async def _ensure_alarm_events_dispatched(self, alarm: Alarm) -> bool:
        """Best-effort publish of atomically persisted initial lifecycle events."""
        await dispatch_pending_alarm_events(
            self._session,
            self._redis,
            logger=logger,
            alarm_id=alarm.id,
        )
        return not await has_pending_alarm_events(self._session, alarm.id)

    def _validate_trigger(
        self,
        token: str,
        severity: str | None = None,
    ) -> tuple[bool, str | None]:
        """Validate trigger data.

        Checks required fields and severity validity.

        Args:
            token: Device token
            severity: Optional severity level

        Returns:
            Tuple of (is_valid, error_message)
        """
        if not token or not token.strip():
            return False, "Token is required"
        if severity is not None and severity not in constants.PRIORITY_ALL:
            return False, f"Invalid severity: {severity}"
        return True, None

    async def _check_idempotency(
        self,
        token: str,
    ) -> tuple[bool, uuid.UUID | None, Alarm | None]:
        """Check idempotency and return existing alarm if found.

        Args:
            token: Device token

        Returns:
            Tuple of (is_duplicate, alarm_id, existing_alarm)
        """
        is_duplicate, existing_id = await self.check_idempotency(token)
        if is_duplicate and existing_id:
            # The Redis key is reserved before the DB commit. A duplicate request
            # can arrive in that small window, so wait briefly for the row to
            # become visible before deciding this is still "being created".
            existing_alarm = await self._load_alarm_with_retry(existing_id)
            if existing_alarm:
                logger.info(
                    "trigger_idempotent",
                    extra={
                        "alarm_id": str(existing_id),
                        "token_hash": _hash_token_for_logging(token),
                    },
                )
                return True, existing_id, existing_alarm
            return True, existing_id, None
        return False, None, None

    async def _reserve_idempotency_key(self, token: str) -> uuid.UUID | None:
        """Reserve idempotency key for the request.

        Args:
            token: Device token

        Returns:
            Reserved UUID or None if reservation failed
        """
        alarm_id = await self.reserve_alarm_id(token)
        if not alarm_id:
            logger.error(
                "idempotency_reservation_failed",
                extra={"token_hash": _hash_token_for_logging(token)},
            )
        return alarm_id

    async def _send_notifications(self, alarm: Alarm) -> bool:
        """Publish durable outbox events without losing alarms to recoverable queue failures."""
        alarm_id = alarm.id
        try:
            return await self._ensure_alarm_events_dispatched(alarm)
        except Exception:
            await self._rollback_failed_alarm_event_delivery(alarm_id)
            logger.exception(
                "alarm_event_delivery_failed",
                extra={"alarm_id": str(alarm_id)},
            )
            return False

    async def _rollback_failed_alarm_event_delivery(self, alarm_id: uuid.UUID) -> None:
        """Rollback failed outbox work without masking the original delivery error."""
        try:
            await self._session.rollback()
        except Exception:
            logger.exception(
                "alarm_event_delivery_rollback_failed",
                extra={"alarm_id": str(alarm_id)},
            )

    async def _handle_duplicate_alarm(self, alarm: Alarm) -> TriggerResult:
        """Return idempotent success only after retrying any unfinished downstream dispatch."""
        events_ok = await self._send_notifications(alarm)
        if not events_ok:
            return TriggerResult.error(
                503,
                "Alarm already exists, but downstream processing still needs a retry request.",
            )
        return TriggerResult.ok(alarm.id, alarm.status, is_duplicate=True)

    def _rate_limit_error(self, token: str) -> TriggerResult:
        """Log a redacted rate-limit rejection and retain a uniform caller-safe response."""
        logger.warning(
            "rate_limit_exceeded",
            extra={
                "token_hash": _hash_token_for_logging(token),
                "limit": self._settings.rate_limit_per_minute,
            },
        )
        return TriggerResult.error(429, "Rate limit exceeded")

    async def _handle_reservation_failure(self, token: str) -> TriggerResult:
        """Distinguish a duplicate in progress from a genuine idempotency-store failure."""
        is_duplicate, _, existing_alarm = await self._check_idempotency(token)
        if is_duplicate and existing_alarm:
            return await self._handle_duplicate_alarm(existing_alarm)
        if is_duplicate:
            return TriggerResult.error(409, "An alarm for this token is already being created.")
        return TriggerResult.error(500, "Idempotency failure")

    async def _duplicate_trigger_result(self, token: str) -> TriggerResult | None:
        """Return an existing alarm result before allocating another idempotency reservation."""
        is_duplicate, _, existing_alarm = await self._check_idempotency(token)
        if not is_duplicate:
            return None
        if existing_alarm:
            return await self._handle_duplicate_alarm(existing_alarm)
        return TriggerResult.error(409, "An alarm for this token is already being created.")

    async def _validate_device_for_trigger(
        self, token: str, alarm_id: uuid.UUID
    ) -> Device | TriggerResult:
        """Validate device mapping and release the reservation when no alarm can be created."""
        device, device_error = await self.validate_device(token)
        if device_error or device is None:
            await self.clear_idempotency(token, alarm_id)
            # Always 404: distinguishing "unknown token" (404) from "mapping incomplete"
            # (409) would let callers probe for valid device tokens.
            return TriggerResult.error(404, device_error or "Unknown device error")
        return device

    async def _create_alarm_for_trigger(
        self,
        *,
        token: str,
        device: Device,
        alarm_id: uuid.UUID,
        client_ip: str,
        user_agent: str,
        event: str | None,
        request_id: str | None,
    ) -> Alarm | TriggerResult:
        """Create the durable alarm or clear the still-owned reservation after a failed commit."""
        try:
            return await self.create_alarm(
                device=device,
                alarm_id=alarm_id,
                client_ip=client_ip,
                user_agent=user_agent,
                event=event,
                request_id=request_id,
            )
        except Exception:
            await self.clear_idempotency(token, alarm_id)
            logger.exception(
                "alarm_create_failed",
                extra={"alarm_id": str(alarm_id), "token_hash": _hash_token_for_logging(token)},
            )
            return TriggerResult.error(500, "Alarm creation failed")

    async def _send_notifications_for_trigger(self, alarm: Alarm) -> TriggerResult | None:
        """Report a retryable 503 when the alarm committed but its outbox remains pending."""
        alarm_id = alarm.id
        if await self._send_notifications(alarm):
            return None
        logger.warning(
            "alarm_event_enqueue_incomplete",
            extra={"alarm_id": str(alarm_id)},
        )
        return TriggerResult.error(
            503,
            "Alarm was created, but downstream processing still needs a retry request.",
        )

    async def _prepare_new_alarm(
        self,
        *,
        token: str,
        client_ip: str,
        user_agent: str,
        event: str | None,
        request_id: str | None,
    ) -> tuple[Alarm, Device] | TriggerResult:
        """Perform rate limit, reservation, validation, and creation before dispatch begins."""
        if not await self.check_rate_limit(token):
            return self._rate_limit_error(token)

        alarm_id = await self._reserve_idempotency_key(token)
        if not alarm_id:
            return await self._handle_reservation_failure(token)

        device_or_error = await self._validate_device_for_trigger(token, alarm_id)
        if isinstance(device_or_error, TriggerResult):
            return device_or_error

        alarm_or_error = await self._create_alarm_for_trigger(
            token=token,
            device=device_or_error,
            alarm_id=alarm_id,
            client_ip=client_ip,
            user_agent=user_agent,
            event=event,
            request_id=request_id,
        )
        if isinstance(alarm_or_error, TriggerResult):
            return alarm_or_error

        return alarm_or_error, device_or_error

    def _log_alarm_triggered(self, alarm: Alarm, device: Device) -> None:
        """Record non-secret correlation fields after a fully accepted trigger workflow."""
        logger.info(
            "alarm_triggered",
            extra={
                "alarm_id": str(alarm.id),
                "device_id": device.id,
                "person_id": device.person_id,
                "room_id": device.room_id,
            },
        )

    async def process_trigger(
        self,
        token: str,
        client_ip: str,
        user_agent: str,
        event: str | None = None,
        request_id: str | None = None,
    ) -> TriggerResult:
        """Process an alarm trigger request (orchestrator).

        Delegates to smaller methods: validate, idempotency check,
        rate limit check, enrich data, evaluate policies,
        create alarm, send notifications.
        """
        is_valid, validation_error = self._validate_trigger(token)
        if not is_valid:
            return TriggerResult.error(400, validation_error or "Validation failed")

        duplicate_result = await self._duplicate_trigger_result(token)
        if duplicate_result is not None:
            return duplicate_result

        prepared = await self._prepare_new_alarm(
            token=token,
            client_ip=client_ip,
            user_agent=user_agent,
            event=event,
            request_id=request_id,
        )
        if isinstance(prepared, TriggerResult):
            return prepared

        alarm, device = prepared
        notification_error = await self._send_notifications_for_trigger(alarm)
        if notification_error is not None:
            return notification_error

        self._log_alarm_triggered(alarm, device)
        return TriggerResult.ok(alarm.id, alarm.status)
