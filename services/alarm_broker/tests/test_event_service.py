"""Tests for alarm_broker.services.event_service — error branches."""

from __future__ import annotations

import logging
import uuid
from unittest.mock import AsyncMock, patch

import pytest

from alarm_broker.services.event_service import (
    EventResult,
    enqueue_alarm_acked_event,
    enqueue_alarm_created_event,
    enqueue_alarm_state_changed_event,
)

pytestmark = [pytest.mark.unit]

_ALARM_ID = uuid.uuid4()
_LOGGER = logging.getLogger("test")


# ── enqueue_alarm_acked_event ─────────────────────────────────────────


async def test_enqueue_alarm_acked_event_success():
    redis = AsyncMock()
    redis.enqueue_job = AsyncMock()

    result = await enqueue_alarm_acked_event(
        redis,
        alarm_id=_ALARM_ID,
        acked_by="user@example.com",
        note="test note",
        logger=_LOGGER,
    )

    assert result.success is True
    assert result.error is None


async def test_enqueue_alarm_acked_event_error():
    redis = AsyncMock()

    with patch(
        "alarm_broker.services.event_service.EventPublisher.publish_alarm_acknowledged",
        new_callable=AsyncMock,
        side_effect=RuntimeError("redis down"),
    ):
        result = await enqueue_alarm_acked_event(
            redis,
            alarm_id=_ALARM_ID,
            acked_by=None,
            note=None,
            logger=_LOGGER,
        )

    assert result.success is False
    assert "redis down" in (result.error or "")


async def test_enqueue_alarm_acked_event_no_acked_by():
    """None acked_by is coerced to 'unknown' and does not raise."""
    redis = AsyncMock()
    redis.enqueue_job = AsyncMock()

    result = await enqueue_alarm_acked_event(
        redis,
        alarm_id=_ALARM_ID,
        acked_by=None,
        note=None,
        logger=_LOGGER,
    )

    assert result.success is True


# ── enqueue_alarm_created_event ───────────────────────────────────────


async def test_enqueue_alarm_created_event_success():
    redis = AsyncMock()
    redis.enqueue_job = AsyncMock()

    result = await enqueue_alarm_created_event(
        redis,
        alarm_id=_ALARM_ID,
        logger=_LOGGER,
    )

    assert result.success is True


async def test_enqueue_alarm_created_event_error():
    redis = AsyncMock()

    with patch(
        "alarm_broker.services.event_service.EventPublisher.publish_alarm_created",
        new_callable=AsyncMock,
        side_effect=ConnectionError("broker unreachable"),
    ):
        result = await enqueue_alarm_created_event(
            redis,
            alarm_id=_ALARM_ID,
            logger=_LOGGER,
        )

    assert result.success is False
    assert "broker unreachable" in (result.error or "")


# ── enqueue_alarm_state_changed_event ─────────────────────────────────


async def test_enqueue_alarm_state_changed_event_success():
    redis = AsyncMock()
    redis.enqueue_job = AsyncMock()

    result = await enqueue_alarm_state_changed_event(
        redis,
        alarm_id=_ALARM_ID,
        state="acknowledged",
        logger=_LOGGER,
        old_state="triggered",
    )

    assert result.success is True


async def test_enqueue_alarm_state_changed_event_error():
    redis = AsyncMock()

    with patch(
        "alarm_broker.services.event_service.EventPublisher.publish_alarm_state_changed",
        new_callable=AsyncMock,
        side_effect=OSError("timeout"),
    ):
        result = await enqueue_alarm_state_changed_event(
            redis,
            alarm_id=_ALARM_ID,
            state="resolved",
            logger=_LOGGER,
        )

    assert result.success is False
    assert "timeout" in (result.error or "")


# ── EventResult ────────────────────────────────────────────────────────


def test_event_result_defaults():
    r = EventResult(success=True)
    assert r.error is None


def test_event_result_failure():
    r = EventResult(success=False, error="oops")
    assert r.success is False
    assert r.error == "oops"
