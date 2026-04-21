"""Tests for alarm_broker.services.event_publisher."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from alarm_broker.services.event_publisher import EventPublisher

pytestmark = [pytest.mark.unit]


def _make_redis() -> MagicMock:
    redis = MagicMock()
    redis.enqueue_job = AsyncMock()
    return redis


async def test_publish_alarm_resolved_enqueues_job():
    redis = _make_redis()
    publisher = EventPublisher(redis)

    await publisher.publish_alarm_resolved(alarm_id="abc", resolved_by="admin")

    redis.enqueue_job.assert_called_once()
    call_args = redis.enqueue_job.call_args
    payload = call_args[0][1]
    assert payload["event_type"] == "alarm.resolved"
    assert payload["alarm_id"] == "abc"
    assert payload["resolved_by"] == "admin"


async def test_publish_alarm_cancelled_enqueues_job():
    redis = _make_redis()
    publisher = EventPublisher(redis)

    await publisher.publish_alarm_cancelled(alarm_id=42, cancelled_by="system")

    redis.enqueue_job.assert_called_once()
    call_args = redis.enqueue_job.call_args
    payload = call_args[0][1]
    assert payload["event_type"] == "alarm.cancelled"
    assert payload["cancelled_by"] == "system"


def test_from_alarm_factory_returns_publisher_instance():
    redis = _make_redis()
    alarm = MagicMock()

    publisher = EventPublisher.from_alarm(redis, alarm)

    assert isinstance(publisher, EventPublisher)
    assert publisher._redis is redis
