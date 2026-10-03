"""Deterministic in-memory fakes for external services."""

from __future__ import annotations

import time


def _is_increment_with_expiry_script(script: str) -> bool:
    """Recognize the rate-limit Lua script supported by the narrow Redis fake."""
    return 'redis.call("incr"' in script and 'redis.call("expire"' in script


def _redis_bytes(value: object) -> bytes | None:
    """Normalize Redis's text-or-bytes values for compare-and-delete emulation."""
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        return value.encode("utf-8")
    return None


class FakeRedis:
    """In-memory subset of Redis used to test idempotency, TTLs, and queued jobs.

    Time advances only when a test calls :meth:`advance`, avoiding sleeps and
    making expiry boundaries deterministic.
    """

    def __init__(self) -> None:
        """Initialize isolated key, expiry, and queued-job state."""
        self._store: dict[str, str] = {}
        self._expiries: dict[str, float] = {}
        self.jobs: list[tuple[str, tuple]] = []
        self._job_ids: set[str] = set()
        self._now = time.monotonic()

    def _purge_expired(self, key: str) -> None:
        """Apply lazy TTL expiry before an operation reads or changes ``key``."""
        expires_at = self._expiries.get(key)
        if expires_at is not None and expires_at <= self._now:
            self._store.pop(key, None)
            self._expiries.pop(key, None)

    def advance(self, seconds: float) -> None:
        """Move the fake monotonic clock forward without blocking the test."""
        self._now += seconds

    async def close(self) -> None:
        """Match the async Redis client shutdown interface without resources to release."""
        return None

    async def get(self, key: str) -> str | None:
        """Read a live key after applying the fake's lazy expiration rule."""
        self._purge_expired(key)
        return self._store.get(key)

    async def set(self, key: str, value: str, *, ex: int | None = None, nx: bool = False) -> bool:
        """Store a value with Redis-like NX and expiry behavior."""
        self._purge_expired(key)
        if nx and key in self._store:
            return False
        self._store[key] = value
        if ex is not None:
            self._expiries[key] = self._now + ex
        else:
            self._expiries.pop(key, None)
        return True

    async def delete(self, key: str) -> int:
        """Delete a live key and report whether it existed, as Redis does."""
        self._purge_expired(key)
        self._expiries.pop(key, None)
        return 1 if self._store.pop(key, None) is not None else 0

    async def _eval_increment_with_expiry(self, key: str, expiry: object) -> int:
        """Execute the fake's supported increment-and-expire Lua operation."""
        if isinstance(expiry, bool) or not isinstance(expiry, int):
            return 0
        count = await self.incr(key)
        if count == 1:
            await self.expire(key, expiry)
        return count

    async def eval(self, script: str, numkeys: int, *keys_and_args: object) -> int:
        """Implement the one-key Lua operations used by the application."""
        if numkeys != 1 or len(keys_and_args) != 2:
            raise NotImplementedError("FakeRedis only supports one-key operations")
        key, expected = keys_and_args
        if not isinstance(key, str):
            return 0
        self._purge_expired(key)
        if _is_increment_with_expiry_script(script):
            return await self._eval_increment_with_expiry(key, expected)
        current = self._store.get(key)

        if _redis_bytes(current) != _redis_bytes(expected):
            return 0
        return await self.delete(key)

    async def incr(self, key: str) -> int:
        """Increment a numeric key after applying expiration."""
        self._purge_expired(key)
        current = int(self._store.get(key, "0")) + 1
        self._store[key] = str(current)
        return current

    async def expire(self, key: str, seconds: int) -> bool:
        """Set a key TTL only when the key exists, matching Redis semantics."""
        self._purge_expired(key)
        if key not in self._store:
            return False
        self._expiries[key] = self._now + seconds
        return True

    async def enqueue_job(self, name: str, *args, **kwargs):
        """Record jobs and emulate Arq's stable-ID deduplication contract."""
        job_id = kwargs.get("_job_id")
        if isinstance(job_id, str):
            if job_id in self._job_ids:
                return None
            self._job_ids.add(job_id)
        self.jobs.append((name, args))
        return object()
