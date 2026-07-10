"""Tests for alarm_broker.core.idempotency, alarm_broker.core.rate_limit, and errors."""

from __future__ import annotations

try:
    from tests.assertions import expect
except ModuleNotFoundError:
    from assertions import expect

import hashlib
import time

import pytest

from alarm_broker.core.errors import (
    AlarmBrokerError,
    AuthenticationError,
    AuthorizationError,
    ConfigurationError,
    ConflictError,
    ConnectorError,
    IdempotencyError,
    NotFoundError,
    RateLimitError,
    ValidationError,
)
from alarm_broker.core.idempotency import bucket_10s, idempotency_key
from alarm_broker.core.rate_limit import minute_bucket, rate_limit_key

try:
    from tests.constants import TEST_DEVICE_TOKEN, value_for_test
except ModuleNotFoundError:
    from constants import TEST_DEVICE_TOKEN, value_for_test

pytestmark = [pytest.mark.unit]


# ── bucket_10s ──────────────────────────────────────────────────────────


class TestBucket10s:
    def test_explicit_epoch(self):
        """bucket_10s with an explicit epoch returns epoch // 10."""
        expect(bucket_10s(120) == 12)
        expect(bucket_10s(129) == 12)
        expect(bucket_10s(130) == 13)

    def test_zero_epoch(self):
        expect(bucket_10s(0) == 0)

    def test_none_uses_current_time(self):
        """When called without argument, bucket_10s uses time.time()."""
        expected = int(time.time()) // 10
        result = bucket_10s()
        # Allow a 1-bucket tolerance in case the second ticks over
        expect(abs(result - expected) <= 1)

    def test_same_10s_window_same_bucket(self):
        """Two timestamps within the same 10-second window produce the same bucket."""
        expect(bucket_10s(1020) == bucket_10s(1029))

    def test_different_10s_window_different_bucket(self):
        """Timestamps in different 10-second windows produce different buckets."""
        expect(bucket_10s(1020) != bucket_10s(1030))


# ── idempotency_key ────────────────────────────────────────────────────


class TestIdempotencyKey:
    def test_deterministic(self):
        """Same inputs always produce the same key."""
        key1 = idempotency_key("abc", 42)
        key2 = idempotency_key("abc", 42)
        expect(key1 == key2)

    def test_matches_sha256(self):
        """The key is the sha256 hex digest of '{token}:{bucket}'."""
        token, bucket = "my-token", 99
        expected = hashlib.sha256(f"{token}:{bucket}".encode()).hexdigest()
        expect(idempotency_key(token, bucket) == expected)

    def test_different_tokens_different_keys(self):
        """Different tokens produce different keys."""
        expect(idempotency_key("a", 1) != idempotency_key("b", 1))

    def test_different_buckets_different_keys(self):
        """Different buckets produce different keys."""
        expect(idempotency_key("a", 1) != idempotency_key("a", 2))

    def test_key_is_hex_string(self):
        """The result is a 64-character hex string (sha256)."""
        key = idempotency_key("x", 0)
        expect(len(key) == 64)
        expect(all(c in "0123456789abcdef" for c in key))


# ── minute_bucket ──────────────────────────────────────────────────────


class TestMinuteBucket:
    def test_explicit_epoch(self):
        """minute_bucket with an explicit epoch returns epoch // 60."""
        expect(minute_bucket(120) == 2)
        expect(minute_bucket(179) == 2)
        expect(minute_bucket(180) == 3)

    def test_zero_epoch(self):
        expect(minute_bucket(0) == 0)

    def test_none_uses_current_time(self):
        """When called without argument, minute_bucket uses time.time()."""
        expected = int(time.time()) // 60
        result = minute_bucket()
        expect(abs(result - expected) <= 1)

    def test_same_minute_same_bucket(self):
        expect(minute_bucket(600) == minute_bucket(659))

    def test_different_minute_different_bucket(self):
        expect(minute_bucket(600) != minute_bucket(660))


# ── rate_limit_key ─────────────────────────────────────────────────────


class TestRateLimitKey:
    def test_deterministic(self):
        """Same inputs always produce the same key."""
        key1 = rate_limit_key("tok", 10)
        key2 = rate_limit_key("tok", 10)
        expect(key1 == key2)

    def test_format(self):
        """The key has the format 'rl:{sha256(token)}:{bucket}'."""
        token, bucket = "my-token", 5
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        expected = f"rl:{token_hash}:{bucket}"
        expect(rate_limit_key(token, bucket) == expected)

    def test_different_tokens_different_keys(self):
        expect(rate_limit_key("a", 1) != rate_limit_key("b", 1))

    def test_different_buckets_different_keys(self):
        expect(rate_limit_key("a", 1) != rate_limit_key("a", 2))

    def test_token_is_hashed(self):
        """The raw token should not appear in the key (it's hashed)."""
        token = TEST_DEVICE_TOKEN
        key = rate_limit_key(token, 0)
        expect(token not in key)
        expect(key.startswith("rl:"))


# ── AlarmBrokerError ───────────────────────────────────────────────────


class TestAlarmBrokerError:
    def test_basic_message(self):
        err = AlarmBrokerError("something failed")
        expect(str(err) == "something failed")
        expect(err.message == "something failed")
        expect(err.details == {})

    def test_with_details(self):
        err = AlarmBrokerError("oops", details={"key": "val"})
        expect(err.details == {"key": "val"})

    def test_to_dict_no_details(self):
        result = AlarmBrokerError("msg").to_dict()
        expect(result == {"error": "msg"})

    def test_to_dict_with_details(self):
        result = AlarmBrokerError("msg", details={"x": 1}).to_dict()
        expect(result == {"error": "msg", "details": {"x": 1}})


# ── ValidationError ────────────────────────────────────────────────────


class TestValidationError:
    def test_without_field(self):
        err = ValidationError("bad input")
        expect(err.field is None)
        d = err.to_dict()
        expect(d == {"error": "bad input"})

    def test_with_field(self):
        err = ValidationError("too long", field="title")
        expect(err.field == "title")
        d = err.to_dict()
        expect(d["field"] == "title")
        expect(d["error"] == "too long")

    def test_with_field_and_details(self):
        err = ValidationError("bad", field="name", details={"max": 500})
        d = err.to_dict()
        expect(d["field"] == "name")
        expect(d["details"] == {"max": 500})


# ── NotFoundError ──────────────────────────────────────────────────────


class TestNotFoundError:
    def test_without_resource_id(self):
        err = NotFoundError("alarm")
        expect("alarm not found" in str(err))
        expect(err.resource_type == "alarm")
        expect(err.resource_id is None)

    def test_with_resource_id(self):
        err = NotFoundError("alarm", resource_id="abc-123")
        expect("abc-123" in str(err))
        expect(err.resource_id == "abc-123")


# ── ConflictError / ConfigurationError / simple subclasses ────────────


class TestSimpleSubclasses:
    def test_conflict_error(self):
        err = ConflictError("duplicate")
        expect(err.message == "duplicate")

    def test_configuration_error(self):
        err = ConfigurationError("missing env")
        expect(err.message == "missing env")

    def test_authentication_error(self):
        err = AuthenticationError("bad token")
        expect(err.message == "bad token")

    def test_authorization_error(self):
        err = AuthorizationError("forbidden")
        expect(err.message == "forbidden")

    def test_idempotency_error(self):
        err = IdempotencyError("collision")
        expect(err.message == "collision")


# ── ConnectorError ─────────────────────────────────────────────────────


class TestConnectorError:
    def test_without_original_error(self):
        err = ConnectorError("zammad", "create_ticket")
        expect("zammad" in str(err))
        expect("create_ticket" in str(err))
        expect(err.connector == "zammad")
        expect(err.operation == "create_ticket")
        expect(err.original_error is None)

    def test_with_original_error(self):
        orig = ValueError("connection refused")
        err = ConnectorError("signal", "send", original_error=orig)
        expect("connection refused" in str(err))
        expect(err.original_error is orig)

    def test_with_details(self):
        err = ConnectorError("sms", "send_sms", details={"code": 503})
        expect(err.details == {"code": 503})


# ── RateLimitError ─────────────────────────────────────────────────────


class TestRateLimitError:
    def test_message_format(self):
        err = RateLimitError(limit=10, window_seconds=60)
        expect("10" in str(err))
        expect("60" in str(err))
        expect(err.limit == 10)
        expect(err.window_seconds == 60)

    def test_with_details(self):
        detail_token = value_for_test("rate-limit-detail")
        err = RateLimitError(5, 30, details={"token": detail_token})
        expect(err.details == {"token": detail_token})
