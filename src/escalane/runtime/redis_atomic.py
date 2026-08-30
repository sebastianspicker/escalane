"""Small Redis value and atomic ownership helpers."""

from __future__ import annotations

from typing import Any

_COMPARE_AND_DELETE_SCRIPT = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
end
return 0
"""

_INCREMENT_WITH_EXPIRY_SCRIPT = """
local count = redis.call("incr", KEYS[1])
if count == 1 then
    redis.call("expire", KEYS[1], ARGV[1])
end
return count
"""


def redis_text(value: object) -> str | None:
    """Normalize Redis text responses at a single strict UTF-8 boundary."""
    if isinstance(value, str):
        return value
    if isinstance(value, bytes):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            return None
    return None


async def compare_and_delete(redis: Any, key: str, expected: Any) -> bool:
    """Delete a Redis key atomically only while it contains ``expected``."""
    try:
        deleted = await redis.eval(_COMPARE_AND_DELETE_SCRIPT, 1, key, expected)
    except TypeError:
        return False
    return bool(deleted)


async def increment_with_expiry(redis: Any, key: str, ttl_seconds: int) -> int:
    """Increment a counter and establish its first TTL in one Redis operation."""
    if ttl_seconds <= 0:
        raise ValueError("ttl_seconds must be positive")
    count = await redis.eval(_INCREMENT_WITH_EXPIRY_SCRIPT, 1, key, ttl_seconds)
    if isinstance(count, bool) or not isinstance(count, int) or count < 1:
        raise RuntimeError("Redis returned an invalid counter value")
    return count
