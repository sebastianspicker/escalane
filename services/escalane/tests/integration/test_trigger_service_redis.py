"""TriggerService correctness checks against a real Redis server."""

from __future__ import annotations

import os
import uuid
from unittest.mock import MagicMock

import pytest
from redis.asyncio import Redis

from escalane.services.trigger_service import TriggerService

try:
    from tests.assertions import expect
except ModuleNotFoundError:
    from assertions import expect

TEST_REDIS_URL = os.getenv("TEST_REDIS_URL")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(TEST_REDIS_URL is None, reason="TEST_REDIS_URL is not set"),
]


@pytest.fixture
async def real_redis():
    if TEST_REDIS_URL is None:
        raise RuntimeError("TEST_REDIS_URL is required for real Redis tests")
    client = Redis.from_url(TEST_REDIS_URL)
    await client.ping()
    try:
        yield client
    finally:
        await client.aclose()


def _service(redis: Redis) -> TriggerService:
    return TriggerService(
        session=MagicMock(),
        redis=redis,  # type: ignore[arg-type]
        settings=MagicMock(),
    )


async def test_real_redis_uuid_bytes_cross_utf8_boundary(real_redis):
    service = _service(real_redis)
    token = f"redis-text-{uuid.uuid4()}"
    key = service._get_idempotency_key(token)
    alarm_id = uuid.uuid4()
    try:
        await real_redis.set(key, str(alarm_id))

        is_duplicate, existing_id = await service.check_idempotency(token)

        expect(is_duplicate is True)
        expect(existing_id == alarm_id)
    finally:
        await real_redis.delete(key)


async def test_real_redis_corrupt_idempotency_value_is_atomically_deleted(real_redis):
    service = _service(real_redis)
    token = f"redis-corrupt-{uuid.uuid4()}"
    key = service._get_idempotency_key(token)
    try:
        await real_redis.set(key, b"\xff")

        is_duplicate, existing_id = await service.check_idempotency(token)

        expect(is_duplicate is False)
        expect(existing_id is None)
        expect(await real_redis.get(key) is None)
    finally:
        await real_redis.delete(key)


async def test_real_redis_corrupt_cleanup_cannot_delete_replacement(real_redis, monkeypatch):
    service = _service(real_redis)
    token = f"redis-race-{uuid.uuid4()}"
    key = service._get_idempotency_key(token)
    replacement = str(uuid.uuid4())
    compare_and_delete = service._compare_and_delete

    async def replace_before_compare(compare_key: str, expected: object) -> bool:
        await real_redis.set(compare_key, replacement)
        return await compare_and_delete(compare_key, expected)

    monkeypatch.setattr(service, "_compare_and_delete", replace_before_compare)
    try:
        await real_redis.set(key, "corrupt")

        is_duplicate, existing_id = await service.check_idempotency(token)

        expect(is_duplicate is False)
        expect(existing_id is None)
        expect(await real_redis.get(key) == replacement.encode("utf-8"))
    finally:
        await real_redis.delete(key)


async def test_real_redis_idempotency_owner_cannot_delete_replacement(real_redis):
    service = _service(real_redis)
    token = f"redis-owner-{uuid.uuid4()}"
    key = service._get_idempotency_key(token)
    original = uuid.uuid4()
    replacement = uuid.uuid4()
    try:
        await real_redis.set(key, str(replacement))

        await service.clear_idempotency(token, original)

        expect(await real_redis.get(key) == str(replacement).encode("utf-8"))
    finally:
        await real_redis.delete(key)
