"""Rate-limit key derivation and atomic Redis counter tests."""

from __future__ import annotations

import asyncio
import hashlib
import time

import pytest

from escalane.runtime.rate_limit import minute_bucket, rate_limit_key
from escalane.runtime.redis_atomic import increment_with_expiry
from tests.support.assertions import expect
from tests.support.constants import TEST_DEVICE_TOKEN
from tests.support.fakes import FakeRedis

pytestmark = [pytest.mark.unit]


async def test_increment_with_expiry_is_atomic_for_concurrent_login_failures() -> None:
    redis = FakeRedis()

    counts = await asyncio.gather(
        *(increment_with_expiry(redis, "admin-login", 60) for _ in range(12))
    )

    expect(sorted(counts) == list(range(1, 13)))
    redis.advance(60)
    expect(await redis.get("admin-login") is None)


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
