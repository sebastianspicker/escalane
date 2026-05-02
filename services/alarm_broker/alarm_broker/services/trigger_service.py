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

from alarm_broker import constants
from alarm_broker.core.idempotency import bucket_10s, idempotency_key
from alarm_broker.core.rate_limit import rate_limit_key
from alarm_broker.db.models import Alarm, AlarmStatus, Device, Room
from alarm_broker.services.event_service import (
    EventResult,
    enqueue_alarm_created_event,
    enqueue_alarm_state_changed_event,
)
from alarm_broker.settings import Settings

logger = logging.getLogger("alarm_broker")


def _hash_token_for_logging(token: str) -> str:
    """Create a safe hash of the token for logging purposes."""
    return hashlib.sha256(token.encode()).hexdigest()[:16]


class TriggerResult:
    """Result of a trigger operation.

    Attributes:
        success: Whether the trigger was successful
        alarm_id: ID of the alarm (new or existing)
        status: Status of the alarm
        is_duplicate: Whether this was a duplicate/idempotent request
        error_code: HTTP error code if failed
        error_message: Error message if failed
    """

    def __init__(
        self,
        *,
        success: bool = True,
        alarm_id: uuid.UUID | None = None,
        status: AlarmStatus | None = None,
        is_duplicate: bool = False,
        error_code: int | None = None,
        error_message: str | None = None,
    ) -> None:
        self.success = success
        self.alarm_id = alarm_id
        self.status = status
        self.is_duplicate = is_duplicate
        self.error_code = error_code
        self.error_message = error_message

    @classmethod
    def ok(
        cls, alarm_id: uuid.UUID, status: AlarmStatus, is_duplicate: bool = False
    ) -> TriggerResult:
        """Create a successful result."""
        return cls(success=True, alarm_id=alarm_id, status=status, is_duplicate=is_duplicate)

    @classmethod
    def error(cls, code: int, message: str) -> TriggerResult:
        """Create an error result."""
        return cls(success=False, error_code=code, error_message=message)


class TriggerService:
    """Service for handling alarm triggers.

    This service encapsulates all trigger logic including:
    - Idempotency checking
    - Rate limiting
    - Device validation
    - Alarm creation
    """

    _IDEMPOTENCY_LOOKUP_ATTEMPTS = 5
    _IDEMPOTENCY_LOOKUP_DELAY_SECONDS = 0.05
    _EVENT_ENQUEUE_ATTEMPTS = 3
    _EVENT_ENQUEUE_DELAY_SECONDS = 0.1
    _EVENT_RECOVERY_LOCK_TTL_SECONDS = 10

    def __init__(
        self,
        session: AsyncSession,
        redis: ArqRedis,
        settings: Settings,
        idempotency_bucket: int | None = None,
        rate_limit_bucket: int | None = None,
    ) -> None:
        """Initialize the trigger service.

        Args:
            session: Database session
            redis: Redis connection for idempotency/rate limiting
            settings: Application settings
            idempotency_bucket: Optional pre-computed idempotency bucket
            rate_limit_bucket: Optional pre-computed rate limit bucket
        """
        self._session = session
        self._redis = redis
        self._settings = settings
        self._idempotency_bucket = (
            idempotency_bucket if idempotency_bucket is not None else bucket_10s()
        )
        self._rate_limit_bucket = rate_limit_bucket

    def _get_idempotency_key(self, token: str) -> str:
        """Get the Redis key for idempotency checking.

        Args:
            token: Device token

        Returns:
            Redis key string
        """
        idem = idempotency_key(token, self._idempotency_bucket)
        return f"idemp:{idem}"

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

    async def check_idempotency(self, token: str) -> tuple[bool, uuid.UUID | None]:
        """Check if this request is idempotent (duplicate).

        Args:
            token: Device token

        Returns:
            Tuple of (is_duplicate, existing_alarm_id)
        """
        idem_key = self._get_idempotency_key(token)

        existing_alarm_id = await self._redis.get(idem_key)
        if not existing_alarm_id:
            return False, None

        try:
            existing_uuid = uuid.UUID(existing_alarm_id)
        except ValueError:
            # Invalid UUID in Redis, clear it
            await self._redis.delete(idem_key)
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

        # Try up to 3 times to handle race conditions
        for _attempt in range(3):
            reserved_id = uuid.uuid4()
            ok = await self._redis.set(idem_key, str(reserved_id), ex=30, nx=True)
            if ok:
                return reserved_id
            existing = await self._redis.get(idem_key)
            if existing:
                try:
                    uuid.UUID(existing)
                    return None
                except ValueError:
                    # Invalid UUID, delete and retry
                    await self._redis.delete(idem_key)
        return None

    async def clear_idempotency(self, token: str) -> None:
        """Clear idempotency key (on error).

        Args:
            token: Device token
        """
        idem_key = self._get_idempotency_key(token)
        await self._redis.delete(idem_key)

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
        rl_val: int = await self._redis.incr(rl_key)
        if rl_val == 1:
            await self._redis.expire(rl_key, 70)
        return rl_val <= self._settings.rate_limit_per_minute

    async def validate_device(self, token: str) -> tuple[Device | None, str | None]:
        """Validate device token and get device.

        Args:
            token: Device token

        Returns:
            Tuple of (device, error_message)
        """
        device = await self._session.scalar(select(Device).where(Device.device_token == token))
        if not device:
            return None, "Unknown token"
        if not device.person_id or not device.room_id:
            return None, "Device mapping incomplete"
        return device, None

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

        idem = idempotency_key(device.device_token, self._idempotency_bucket)

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
                "idempotency": {"bucket": self._idempotency_bucket, "key": idem},
                "event_delivery": self._event_delivery_defaults(),
            },
        )
        self._session.add(alarm)
        await self._session.commit()

        return alarm

    def _event_delivery_defaults(self) -> dict[str, Any]:
        """Initial recovery state for trigger-side worker event publication.

        The alarm row is the durable source of truth. Redis/ARQ is best effort,
        so these flags let duplicate triggers and the recovery cron finish
        queuing the worker jobs after a transient queue failure.
        """
        return {
            "alarm_created_enqueued": False,
            "alarm_state_changed_enqueued": False,
            "last_error": None,
            "last_attempt_at": None,
        }

    def _event_delivery_state(self, alarm: Alarm) -> dict[str, Any]:
        meta = alarm.meta if isinstance(alarm.meta, dict) else {}
        raw = meta.get("event_delivery")
        state = self._event_delivery_defaults()
        if isinstance(raw, dict):
            state.update(raw)
        return state

    async def _persist_event_delivery_state(
        self,
        alarm: Alarm,
        *,
        alarm_created_enqueued: bool | None = None,
        alarm_state_changed_enqueued: bool | None = None,
        last_error: str | None = None,
    ) -> None:
        state = self._event_delivery_state(alarm)
        if alarm_created_enqueued is not None:
            state["alarm_created_enqueued"] = alarm_created_enqueued
        if alarm_state_changed_enqueued is not None:
            state["alarm_state_changed_enqueued"] = alarm_state_changed_enqueued
        state["last_error"] = last_error
        state["last_attempt_at"] = datetime.now(UTC).isoformat()
        alarm.meta = {**(alarm.meta or {}), "event_delivery": state}
        await self._session.commit()

    async def _load_alarm_with_retry(self, alarm_id: uuid.UUID) -> Alarm | None:
        for attempt in range(self._IDEMPOTENCY_LOOKUP_ATTEMPTS):
            alarm = await self._session.get(Alarm, alarm_id)
            if alarm is not None:
                return alarm
            if attempt < self._IDEMPOTENCY_LOOKUP_ATTEMPTS - 1:
                await asyncio.sleep(self._IDEMPOTENCY_LOOKUP_DELAY_SECONDS)
        return None

    async def _wait_for_event_delivery(self, alarm: Alarm) -> bool:
        for attempt in range(self._IDEMPOTENCY_LOOKUP_ATTEMPTS):
            await self._session.refresh(alarm)
            state = self._event_delivery_state(alarm)
            if state["alarm_created_enqueued"] and state["alarm_state_changed_enqueued"]:
                return True
            if attempt < self._IDEMPOTENCY_LOOKUP_ATTEMPTS - 1:
                await asyncio.sleep(self._IDEMPOTENCY_LOOKUP_DELAY_SECONDS)
        return False

    async def _enqueue_event_with_retry(
        self,
        *,
        enqueue: Any,
    ) -> EventResult:
        last_result = EventResult(success=False, error="enqueue did not run")
        for attempt in range(self._EVENT_ENQUEUE_ATTEMPTS):
            last_result = await enqueue()
            if last_result.success:
                return last_result
            if attempt < self._EVENT_ENQUEUE_ATTEMPTS - 1:
                await asyncio.sleep(self._EVENT_ENQUEUE_DELAY_SECONDS)
        return last_result

    def _get_event_recovery_lock_key(self, alarm_id: uuid.UUID) -> str:
        return f"alarm:event-recovery:{alarm_id}"

    async def _acquire_event_recovery_lock(self, alarm_id: uuid.UUID) -> str | None:
        lock_key = self._get_event_recovery_lock_key(alarm_id)
        token = secrets.token_urlsafe(16)
        ok = await self._redis.set(
            lock_key,
            token,
            ex=self._EVENT_RECOVERY_LOCK_TTL_SECONDS,
            nx=True,
        )
        return token if ok else None

    async def _release_event_recovery_lock(self, alarm_id: uuid.UUID, token: str) -> None:
        lock_key = self._get_event_recovery_lock_key(alarm_id)
        current = await self._redis.get(lock_key)
        if current == token:
            await self._redis.delete(lock_key)

    async def _ensure_alarm_events_dispatched(self, alarm: Alarm) -> bool:
        """Queue the initial worker events exactly once per alarm when possible.

        This method deliberately stores progress in `alarm.meta.event_delivery`
        and uses a short Redis lock. That keeps concurrent duplicate trigger
        requests from publishing duplicate initial fan-out jobs while still
        allowing a later retry or recovery scan to complete partial delivery.
        """
        state = self._event_delivery_state(alarm)
        if state["alarm_created_enqueued"] and state["alarm_state_changed_enqueued"]:
            return True

        lock_token = await self._acquire_event_recovery_lock(alarm.id)
        if lock_token is None:
            return await self._wait_for_event_delivery(alarm)

        try:
            await self._session.refresh(alarm)
            state = self._event_delivery_state(alarm)

            if not state["alarm_created_enqueued"]:
                created_result = await self._enqueue_event_with_retry(
                    enqueue=lambda: enqueue_alarm_created_event(
                        self._redis,
                        alarm_id=alarm.id,
                        logger=logger,
                    )
                )
                if not created_result.success:
                    await self._persist_event_delivery_state(
                        alarm,
                        last_error=created_result.error or "alarm.created enqueue failed",
                    )
                    return False
                await self._persist_event_delivery_state(
                    alarm,
                    alarm_created_enqueued=True,
                )

            if not state["alarm_state_changed_enqueued"]:
                state_result = await self._enqueue_event_with_retry(
                    enqueue=lambda: enqueue_alarm_state_changed_event(
                        self._redis,
                        alarm_id=alarm.id,
                        state=alarm.status.value,
                        logger=logger,
                        old_state="none",
                    )
                )
                if not state_result.success:
                    await self._persist_event_delivery_state(
                        alarm,
                        last_error=state_result.error or "alarm.state_changed enqueue failed",
                    )
                    return False
                await self._persist_event_delivery_state(
                    alarm,
                    alarm_state_changed_enqueued=True,
                    last_error=None,
                )

            return True
        finally:
            await self._release_event_recovery_lock(alarm.id, lock_token)

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
        """Ensure downstream events are enqueued or marked for recovery."""
        return await self._ensure_alarm_events_dispatched(alarm)

    async def recover_alarm_events(self, alarm: Alarm) -> bool:
        """Recover missing downstream events for a persisted alarm."""
        return await self._ensure_alarm_events_dispatched(alarm)

    async def _handle_duplicate_alarm(self, alarm: Alarm) -> TriggerResult:
        events_ok = await self._send_notifications(alarm)
        if not events_ok:
            return TriggerResult.error(
                503,
                "Alarm already exists, but downstream processing still needs a retry request.",
            )
        return TriggerResult.ok(alarm.id, alarm.status, is_duplicate=True)

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
        # Step 1: Validate trigger data
        is_valid, validation_error = self._validate_trigger(token)
        if not is_valid:
            return TriggerResult.error(400, validation_error or "Validation failed")

        # Step 2: Check idempotency - return existing if duplicate
        is_duplicate, _, existing_alarm = await self._check_idempotency(token)
        if is_duplicate and existing_alarm:
            return await self._handle_duplicate_alarm(existing_alarm)
        if is_duplicate:
            return TriggerResult.error(409, "An alarm for this token is already being created.")

        # Step 3: Check rate limit (before reserving idempotency key)
        if not await self.check_rate_limit(token):
            logger.warning(
                "rate_limit_exceeded",
                extra={
                    "token_hash": _hash_token_for_logging(token),
                    "limit": self._settings.rate_limit_per_minute,
                },
            )
            return TriggerResult.error(429, "Rate limit exceeded")

        # Step 4: Reserve idempotency key
        alarm_id = await self._reserve_idempotency_key(token)
        if not alarm_id:
            is_duplicate, _, existing_alarm = await self._check_idempotency(token)
            if is_duplicate and existing_alarm:
                return await self._handle_duplicate_alarm(existing_alarm)
            if is_duplicate:
                return TriggerResult.error(409, "An alarm for this token is already being created.")
            return TriggerResult.error(500, "Idempotency failure")

        # Step 5: Validate device
        device, device_error = await self.validate_device(token)
        if device_error or device is None:
            await self.clear_idempotency(token)
            # Always 404 — distinguishing "unknown token" (404) from "mapping incomplete"
            # (409) would let callers probe for valid device tokens.
            return TriggerResult.error(404, device_error or "Unknown device error")

        try:
            alarm = await self.create_alarm(
                device=device,
                alarm_id=alarm_id,
                client_ip=client_ip,
                user_agent=user_agent,
                event=event,
                request_id=request_id,
            )
        except Exception:
            await self.clear_idempotency(token)
            logger.exception(
                "alarm_create_failed",
                extra={"alarm_id": str(alarm_id), "token_hash": _hash_token_for_logging(token)},
            )
            return TriggerResult.error(500, "Alarm creation failed")

        if not await self._send_notifications(alarm):
            logger.warning(
                "alarm_event_enqueue_incomplete",
                extra={"alarm_id": str(alarm.id)},
            )
            return TriggerResult.error(
                503,
                "Alarm was created, but downstream processing still needs a retry request.",
            )

        logger.info(
            "alarm_triggered",
            extra={
                "alarm_id": str(alarm.id),
                "device_id": device.id,
                "person_id": device.person_id,
                "room_id": device.room_id,
            },
        )
        return TriggerResult.ok(alarm.id, alarm.status)
