"""Tests for alarm_broker.core.idempotency, alarm_broker.core.rate_limit, and errors."""

from __future__ import annotations

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

pytestmark = [pytest.mark.unit]


# ── bucket_10s ──────────────────────────────────────────────────────────


class TestBucket10s:
    def test_explicit_epoch(self):
        """bucket_10s with an explicit epoch returns epoch // 10."""
        assert bucket_10s(120) == 12
        assert bucket_10s(129) == 12
        assert bucket_10s(130) == 13

    def test_zero_epoch(self):
        assert bucket_10s(0) == 0

    def test_none_uses_current_time(self):
        """When called without argument, bucket_10s uses time.time()."""
        expected = int(time.time()) // 10
        result = bucket_10s()
        # Allow a 1-bucket tolerance in case the second ticks over
        assert abs(result - expected) <= 1

    def test_same_10s_window_same_bucket(self):
        """Two timestamps within the same 10-second window produce the same bucket."""
        assert bucket_10s(1020) == bucket_10s(1029)

    def test_different_10s_window_different_bucket(self):
        """Timestamps in different 10-second windows produce different buckets."""
        assert bucket_10s(1020) != bucket_10s(1030)


# ── idempotency_key ────────────────────────────────────────────────────


class TestIdempotencyKey:
    def test_deterministic(self):
        """Same inputs always produce the same key."""
        key1 = idempotency_key("abc", 42)
        key2 = idempotency_key("abc", 42)
        assert key1 == key2

    def test_matches_sha256(self):
        """The key is the sha256 hex digest of '{token}:{bucket}'."""
        token, bucket = "my-token", 99
        expected = hashlib.sha256(f"{token}:{bucket}".encode()).hexdigest()
        assert idempotency_key(token, bucket) == expected

    def test_different_tokens_different_keys(self):
        """Different tokens produce different keys."""
        assert idempotency_key("a", 1) != idempotency_key("b", 1)

    def test_different_buckets_different_keys(self):
        """Different buckets produce different keys."""
        assert idempotency_key("a", 1) != idempotency_key("a", 2)

    def test_key_is_hex_string(self):
        """The result is a 64-character hex string (sha256)."""
        key = idempotency_key("x", 0)
        assert len(key) == 64
        assert all(c in "0123456789abcdef" for c in key)


# ── minute_bucket ──────────────────────────────────────────────────────


class TestMinuteBucket:
    def test_explicit_epoch(self):
        """minute_bucket with an explicit epoch returns epoch // 60."""
        assert minute_bucket(120) == 2
        assert minute_bucket(179) == 2
        assert minute_bucket(180) == 3

    def test_zero_epoch(self):
        assert minute_bucket(0) == 0

    def test_none_uses_current_time(self):
        """When called without argument, minute_bucket uses time.time()."""
        expected = int(time.time()) // 60
        result = minute_bucket()
        assert abs(result - expected) <= 1

    def test_same_minute_same_bucket(self):
        assert minute_bucket(600) == minute_bucket(659)

    def test_different_minute_different_bucket(self):
        assert minute_bucket(600) != minute_bucket(660)


# ── rate_limit_key ─────────────────────────────────────────────────────


class TestRateLimitKey:
    def test_deterministic(self):
        """Same inputs always produce the same key."""
        key1 = rate_limit_key("tok", 10)
        key2 = rate_limit_key("tok", 10)
        assert key1 == key2

    def test_format(self):
        """The key has the format 'rl:{sha256(token)}:{bucket}'."""
        token, bucket = "my-token", 5
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        expected = f"rl:{token_hash}:{bucket}"
        assert rate_limit_key(token, bucket) == expected

    def test_different_tokens_different_keys(self):
        assert rate_limit_key("a", 1) != rate_limit_key("b", 1)

    def test_different_buckets_different_keys(self):
        assert rate_limit_key("a", 1) != rate_limit_key("a", 2)

    def test_token_is_hashed(self):
        """The raw token should not appear in the key (it's hashed)."""
        token = "secret-device-token"
        key = rate_limit_key(token, 0)
        assert token not in key
        assert key.startswith("rl:")


# ── AlarmBrokerError ───────────────────────────────────────────────────


class TestAlarmBrokerError:
    def test_basic_message(self):
        err = AlarmBrokerError("something failed")
        assert str(err) == "something failed"
        assert err.message == "something failed"
        assert err.details == {}

    def test_with_details(self):
        err = AlarmBrokerError("oops", details={"key": "val"})
        assert err.details == {"key": "val"}

    def test_to_dict_no_details(self):
        result = AlarmBrokerError("msg").to_dict()
        assert result == {"error": "msg"}

    def test_to_dict_with_details(self):
        result = AlarmBrokerError("msg", details={"x": 1}).to_dict()
        assert result == {"error": "msg", "details": {"x": 1}}


# ── ValidationError ────────────────────────────────────────────────────


class TestValidationError:
    def test_without_field(self):
        err = ValidationError("bad input")
        assert err.field is None
        d = err.to_dict()
        assert d == {"error": "bad input"}

    def test_with_field(self):
        err = ValidationError("too long", field="title")
        assert err.field == "title"
        d = err.to_dict()
        assert d["field"] == "title"
        assert d["error"] == "too long"

    def test_with_field_and_details(self):
        err = ValidationError("bad", field="name", details={"max": 500})
        d = err.to_dict()
        assert d["field"] == "name"
        assert d["details"] == {"max": 500}


# ── NotFoundError ──────────────────────────────────────────────────────


class TestNotFoundError:
    def test_without_resource_id(self):
        err = NotFoundError("alarm")
        assert "alarm not found" in str(err)
        assert err.resource_type == "alarm"
        assert err.resource_id is None

    def test_with_resource_id(self):
        err = NotFoundError("alarm", resource_id="abc-123")
        assert "abc-123" in str(err)
        assert err.resource_id == "abc-123"


# ── ConflictError / ConfigurationError / simple subclasses ────────────


class TestSimpleSubclasses:
    def test_conflict_error(self):
        err = ConflictError("duplicate")
        assert err.message == "duplicate"

    def test_configuration_error(self):
        err = ConfigurationError("missing env")
        assert err.message == "missing env"

    def test_authentication_error(self):
        err = AuthenticationError("bad token")
        assert err.message == "bad token"

    def test_authorization_error(self):
        err = AuthorizationError("forbidden")
        assert err.message == "forbidden"

    def test_idempotency_error(self):
        err = IdempotencyError("collision")
        assert err.message == "collision"


# ── ConnectorError ─────────────────────────────────────────────────────


class TestConnectorError:
    def test_without_original_error(self):
        err = ConnectorError("zammad", "create_ticket")
        assert "zammad" in str(err)
        assert "create_ticket" in str(err)
        assert err.connector == "zammad"
        assert err.operation == "create_ticket"
        assert err.original_error is None

    def test_with_original_error(self):
        orig = ValueError("connection refused")
        err = ConnectorError("signal", "send", original_error=orig)
        assert "connection refused" in str(err)
        assert err.original_error is orig

    def test_with_details(self):
        err = ConnectorError("sms", "send_sms", details={"code": 503})
        assert err.details == {"code": 503}


# ── RateLimitError ─────────────────────────────────────────────────────


class TestRateLimitError:
    def test_message_format(self):
        err = RateLimitError(limit=10, window_seconds=60)
        assert "10" in str(err)
        assert "60" in str(err)
        assert err.limit == 10
        assert err.window_seconds == 60

    def test_with_details(self):
        err = RateLimitError(5, 30, details={"token": "x"})
        assert err.details == {"token": "x"}
