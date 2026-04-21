"""Unit tests for TriggerService internal branches (no real DB/Redis needed)."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from alarm_broker.db.models import AlarmStatus, Device
from alarm_broker.services.event_service import EventResult
from alarm_broker.services.trigger_service import TriggerResult, TriggerService
from alarm_broker.settings import Settings

pytestmark = [pytest.mark.unit]


# ── helpers ────────────────────────────────────────────────────────────


def _make_settings() -> Settings:
    return Settings(
        database_url="sqlite+aiosqlite:///:memory:",
        redis_url="redis://fake/0",
        base_url="http://localhost:8080",
        admin_api_key="test-key",
        simulation_enabled=True,
        rate_limit_per_minute=100,
        zammad_api_token="",
        sendxms_enabled=False,
        signal_enabled=False,
    )


def _make_service(*, redis: MagicMock | None = None) -> tuple[TriggerService, MagicMock, MagicMock]:
    session = AsyncMock()
    r = redis or MagicMock()
    svc = TriggerService(
        session=session,
        redis=r,
        settings=_make_settings(),
        idempotency_bucket=100,
        rate_limit_bucket=200,
    )
    return svc, session, r


def _make_device(*, has_person: bool = True, has_room: bool = True) -> Device:
    d = MagicMock(spec=Device)
    d.id = "dev-1"
    d.person_id = "p-1" if has_person else None
    d.room_id = "r-1" if has_room else None
    return d


# ── _validate_trigger ─────────────────────────────────────────────────


def test_validate_trigger_empty_token():
    svc, _, _ = _make_service()
    valid, msg = svc._validate_trigger("")
    assert not valid
    assert msg == "Token is required"


def test_validate_trigger_whitespace_token():
    svc, _, _ = _make_service()
    valid, msg = svc._validate_trigger("   ")
    assert not valid
    assert msg == "Token is required"


def test_validate_trigger_invalid_severity():
    svc, _, _ = _make_service()
    valid, msg = svc._validate_trigger("TOK", severity="INVALID")
    assert not valid
    assert msg is not None
    assert "INVALID" in msg


def test_validate_trigger_valid():
    svc, _, _ = _make_service()
    valid, msg = svc._validate_trigger("TOK")
    assert valid
    assert msg is None


def test_validate_trigger_valid_severity():
    svc, _, _ = _make_service()
    valid, msg = svc._validate_trigger("TOK", severity="P0")
    assert valid
    assert msg is None


# ── _get_rate_limit_key raises when bucket not set ────────────────────


def test_get_rate_limit_key_raises_when_bucket_none():
    svc, _, _ = _make_service()
    svc._rate_limit_bucket = None  # force None

    with pytest.raises(RuntimeError, match="Rate limit bucket is not set"):
        svc._get_rate_limit_key("some-token")


# ── check_rate_limit returns True when bucket is None ─────────────────


async def test_check_rate_limit_bucket_none_always_passes():
    svc, _, r = _make_service()
    svc._rate_limit_bucket = None

    result = await svc.check_rate_limit("any-token")

    assert result is True


# ── check_idempotency: invalid UUID in Redis ──────────────────────────


async def test_check_idempotency_invalid_uuid_clears_key():
    svc, _, r = _make_service()
    r.get = AsyncMock(return_value="not-a-uuid")
    r.delete = AsyncMock()

    is_dup, existing_id = await svc.check_idempotency("TOK")

    assert not is_dup
    assert existing_id is None
    r.delete.assert_called_once()


async def test_check_idempotency_valid_uuid_returns_duplicate():
    existing = uuid.uuid4()
    svc, _, r = _make_service()
    r.get = AsyncMock(return_value=str(existing))

    is_dup, existing_id = await svc.check_idempotency("TOK")

    assert is_dup is True
    assert existing_id == existing


async def test_check_idempotency_no_key_returns_no_duplicate():
    svc, _, r = _make_service()
    r.get = AsyncMock(return_value=None)

    is_dup, existing_id = await svc.check_idempotency("TOK")

    assert not is_dup
    assert existing_id is None


# ── reserve_alarm_id: race condition (invalid UUID triggers retry) ─────


async def test_reserve_alarm_id_returns_none_if_other_owner_wins():
    """NX set fails and the existing value is a valid UUID → we yield."""
    existing = uuid.uuid4()
    svc, _, r = _make_service()
    r.set = AsyncMock(return_value=None)  # NX fails every time
    r.get = AsyncMock(return_value=str(existing))

    result = await svc.reserve_alarm_id("TOK")
    assert result is None


async def test_reserve_alarm_id_clears_invalid_and_retries():
    """NX fails, existing value is invalid UUID → delete and retry."""
    _ = uuid.uuid4()  # unused but needed for call counter context
    calls = {"n": 0}

    async def fake_set(key, val, ex, nx):  # noqa: ANN001
        calls["n"] += 1
        if calls["n"] == 1:
            return None  # first attempt fails (race)
        return True  # second attempt succeeds

    async def fake_get(key):  # noqa: ANN001
        return "not-a-valid-uuid"  # always invalid → triggers delete

    svc, _, r = _make_service()
    r.set = AsyncMock(side_effect=fake_set)
    r.get = AsyncMock(side_effect=fake_get)
    r.delete = AsyncMock()

    result = await svc.reserve_alarm_id("TOK")
    assert result is not None  # eventually succeeds
    assert r.delete.called


# ── clear_idempotency ─────────────────────────────────────────────────


async def test_clear_idempotency_deletes_key():
    svc, _, r = _make_service()
    r.delete = AsyncMock()

    await svc.clear_idempotency("TOK")

    r.delete.assert_called_once()


# ── validate_device ───────────────────────────────────────────────────


async def test_validate_device_unknown_token():
    svc, session, _ = _make_service()
    session.scalar = AsyncMock(return_value=None)

    device, err = await svc.validate_device("UNKNOWN_TOK")

    assert device is None
    assert err == "Unknown token"


async def test_validate_device_mapping_incomplete():
    svc, session, _ = _make_service()
    session.scalar = AsyncMock(return_value=_make_device(has_person=False))

    device, err = await svc.validate_device("TOK")

    assert device is None
    assert err == "Device mapping incomplete"


async def test_validate_device_room_missing_mapping_incomplete():
    svc, session, _ = _make_service()
    session.scalar = AsyncMock(return_value=_make_device(has_room=False))

    device, err = await svc.validate_device("TOK")

    assert device is None
    assert err == "Device mapping incomplete"


async def test_validate_device_ok():
    svc, session, _ = _make_service()
    session.scalar = AsyncMock(return_value=_make_device())

    device, err = await svc.validate_device("TOK")

    assert device is not None
    assert err is None


# ── _acquire / _release event recovery lock ───────────────────────────


async def test_acquire_event_recovery_lock_success():
    svc, _, r = _make_service()
    r.set = AsyncMock(return_value=True)

    token = await svc._acquire_event_recovery_lock(uuid.uuid4())

    assert token is not None


async def test_acquire_event_recovery_lock_fails_when_locked():
    svc, _, r = _make_service()
    r.set = AsyncMock(return_value=None)

    token = await svc._acquire_event_recovery_lock(uuid.uuid4())

    assert token is None


async def test_release_event_recovery_lock_skips_if_token_mismatch():
    alarm_id = uuid.uuid4()
    svc, _, r = _make_service()
    r.get = AsyncMock(return_value="other-token")
    r.delete = AsyncMock()

    await svc._release_event_recovery_lock(alarm_id, "my-token")

    r.delete.assert_not_called()


async def test_release_event_recovery_lock_deletes_if_token_matches():
    alarm_id = uuid.uuid4()
    svc, _, r = _make_service()
    r.get = AsyncMock(return_value="my-token")
    r.delete = AsyncMock()

    await svc._release_event_recovery_lock(alarm_id, "my-token")

    r.delete.assert_called_once()


# ── _enqueue_event_with_retry: exhausted ─────────────────────────────


async def test_enqueue_event_with_retry_exhausted_returns_last_failure(monkeypatch):
    svc, _, _ = _make_service()
    monkeypatch.setattr(TriggerService, "_EVENT_ENQUEUE_ATTEMPTS", 2)
    monkeypatch.setattr(TriggerService, "_EVENT_ENQUEUE_DELAY_SECONDS", 0)

    call_count = {"n": 0}

    async def always_fail():
        call_count["n"] += 1
        return EventResult(success=False, error="queue down")

    result = await svc._enqueue_event_with_retry(enqueue=always_fail)

    assert result.success is False
    assert call_count["n"] == 2


# ── process_trigger: full orchestrator paths ───────────────────────────


async def test_process_trigger_empty_token_returns_400():
    svc, _, _ = _make_service()

    result = await svc.process_trigger(token="", client_ip="127.0.0.1", user_agent="test")

    assert not result.success
    assert result.error_code == 400


async def test_process_trigger_rate_limit_exceeded_returns_429():
    svc, _, r = _make_service()
    r.get = AsyncMock(return_value=None)  # no existing idempotency key
    r.incr = AsyncMock(return_value=999)  # far above limit

    result = await svc.process_trigger(token="TOK", client_ip="127.0.0.1", user_agent="test")

    assert not result.success
    assert result.error_code == 429


async def test_process_trigger_unknown_device_returns_404():
    svc, session, r = _make_service()
    r.get = AsyncMock(return_value=None)  # no idempotency key
    r.incr = AsyncMock(return_value=1)
    r.expire = AsyncMock()
    r.set = AsyncMock(return_value=True)  # idempotency reservation succeeds
    r.delete = AsyncMock()
    session.scalar = AsyncMock(return_value=None)  # device not found

    result = await svc.process_trigger(token="UNKNOWN", client_ip="127.0.0.1", user_agent="test")

    assert not result.success
    assert result.error_code == 404


async def test_process_trigger_device_mapping_incomplete_returns_404():
    svc, session, r = _make_service()
    r.get = AsyncMock(return_value=None)
    r.incr = AsyncMock(return_value=1)
    r.expire = AsyncMock()
    r.set = AsyncMock(return_value=True)
    r.delete = AsyncMock()
    session.scalar = AsyncMock(return_value=_make_device(has_person=False))

    result = await svc.process_trigger(token="TOK", client_ip="127.0.0.1", user_agent="test")

    assert not result.success
    assert result.error_code == 404


async def test_process_trigger_idempotency_reservation_failure_no_existing_alarm():
    """reserve_alarm_id returns None and no existing alarm → 500."""
    svc, _, r = _make_service()
    r.get = AsyncMock(return_value=None)  # no initial duplicate
    r.incr = AsyncMock(return_value=1)
    r.expire = AsyncMock()
    r.set = AsyncMock(return_value=None)  # NX fails → reservation fails

    result = await svc.process_trigger(token="TOK", client_ip="127.0.0.1", user_agent="test")

    assert not result.success
    assert result.error_code in (409, 500)


async def test_process_trigger_alarm_creation_exception_returns_500():
    svc, session, r = _make_service()
    r.get = AsyncMock(return_value=None)
    r.incr = AsyncMock(return_value=1)
    r.expire = AsyncMock()
    r.set = AsyncMock(return_value=True)
    r.delete = AsyncMock()
    session.scalar = AsyncMock(return_value=_make_device())
    # create_alarm raises
    with patch.object(
        svc, "create_alarm", new_callable=AsyncMock, side_effect=RuntimeError("db error")
    ):
        result = await svc.process_trigger(token="TOK", client_ip="127.0.0.1", user_agent="test")

    assert not result.success
    assert result.error_code == 500


# ── TriggerResult helpers ─────────────────────────────────────────────


def test_trigger_result_ok():
    alarm_id = uuid.uuid4()
    r = TriggerResult.ok(alarm_id, AlarmStatus.TRIGGERED)
    assert r.success
    assert r.alarm_id == alarm_id
    assert r.is_duplicate is False


def test_trigger_result_ok_duplicate():
    alarm_id = uuid.uuid4()
    r = TriggerResult.ok(alarm_id, AlarmStatus.TRIGGERED, is_duplicate=True)
    assert r.is_duplicate is True


def test_trigger_result_error():
    r = TriggerResult.error(503, "downstream failed")
    assert not r.success
    assert r.error_code == 503
    assert r.error_message == "downstream failed"
