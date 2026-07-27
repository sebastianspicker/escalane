"""Tests for rate limiting, Redis atomics, URL validation, and errors."""

from __future__ import annotations

try:
    from tests.assertions import expect
except ModuleNotFoundError:
    from assertions import expect

import asyncio
import hashlib
import socket
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.requests import Request

from escalane.api.deps import get_client_ip
from escalane.core.errors import (
    AuthenticationError,
    AuthorizationError,
    ConfigurationError,
    ConflictError,
    ConnectorError,
    EscalaneError,
    IdempotencyError,
    NotFoundError,
    RateLimitError,
    ValidationError,
)
from escalane.core.rate_limit import minute_bucket, rate_limit_key
from escalane.core.redis_atomic import increment_with_expiry
from escalane.core.url_validation import RetryableSSRFError, validate_url_not_internal

try:
    from tests.constants import TEST_DEVICE_TOKEN, value_for_test
    from tests.helpers import FakeRedis
except ModuleNotFoundError:
    from constants import TEST_DEVICE_TOKEN, value_for_test
    from helpers import FakeRedis

pytestmark = [pytest.mark.unit]


async def test_increment_with_expiry_is_atomic_for_concurrent_login_failures() -> None:
    redis = FakeRedis()

    counts = await asyncio.gather(
        *(increment_with_expiry(redis, "admin-login", 60) for _ in range(12))
    )

    expect(sorted(counts) == list(range(1, 13)))
    redis.advance(60)
    expect(await redis.get("admin-login") is None)


async def test_dns_resolution_failure_is_classified_as_retryable() -> None:
    loop = MagicMock()
    loop.getaddrinfo = AsyncMock(side_effect=socket.gaierror("temporary failure"))

    with (
        patch("escalane.core.url_validation.asyncio.get_running_loop", return_value=loop),
        pytest.raises(RetryableSSRFError, match="Cannot resolve hostname"),
    ):
        await validate_url_not_internal("https://hooks.example.test/path")


def test_trusted_proxy_ignores_forged_leftmost_forwarded_address() -> None:
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [(b"x-forwarded-for", b"203.0.113.10, 198.51.100.25")],
            "client": ("127.0.0.1", 12345),
            "server": ("test", 80),
            "scheme": "http",
            "query_string": b"",
        }
    )
    settings = MagicMock(trusted_proxy_cidrs="127.0.0.1/32")

    expect(get_client_ip(request, settings) == "198.51.100.25")


def test_missing_asgi_client_address_is_not_treated_as_loopback() -> None:
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [],
            "server": ("test", 80),
            "scheme": "http",
            "query_string": b"",
        }
    )

    expect(get_client_ip(request) is None)


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


# ── EscalaneError ───────────────────────────────────────────────────


class TestEscalaneError:
    def test_basic_message(self):
        err = EscalaneError("something failed")
        expect(str(err) == "something failed")
        expect(err.message == "something failed")
        expect(err.details == {})

    def test_with_details(self):
        err = EscalaneError("oops", details={"key": "val"})
        expect(err.details == {"key": "val"})

    def test_to_dict_no_details(self):
        result = EscalaneError("msg").to_dict()
        expect(result == {"error": "msg"})

    def test_to_dict_with_details(self):
        result = EscalaneError("msg", details={"x": 1}).to_dict()
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
