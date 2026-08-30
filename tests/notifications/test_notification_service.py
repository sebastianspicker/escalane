"""Notification-service behavior and delivery retry classification tests."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import select

from escalane.contracts.alarms import AlarmStatus
from escalane.notifications.delivery import is_retryable_delivery_error
from escalane.notifications.dispatch import NotificationService
from escalane.persistence.models import Alarm, AlarmNotification
from escalane.providers.base import BaseConnector, BaseConnectorConfig
from tests.support.assertions import expect
from tests.support.constants import value_for_test

pytestmark = pytest.mark.unit


class _DummyZammad:
    def enabled(self) -> bool:
        return True

    async def add_internal_note(self, ticket_id: int, subject: str, body: str) -> None:
        expect(ticket_id > 0)
        expect(subject)
        expect(body)


class _DummyNoop:
    pass


async def test_add_zammad_ack_note_logs_with_real_alarm_id(sessionmaker, seeded_db):
    alarm_id = uuid.uuid4()
    now = datetime.now(UTC)

    async with sessionmaker() as session:
        session.add(
            Alarm(
                id=alarm_id,
                status=AlarmStatus.ACKNOWLEDGED,
                source="test",
                event="alarm.trigger",
                person_id="ma-012",
                room_id="bg-1.23",
                site_id="bg",
                device_id="ylk-t5-10023",
                severity="P0",
                silent=True,
                ack_token=value_for_test("ack-note"),
                acked_at=now,
                acked_by="Responder",
                meta={},
            )
        )
        await session.commit()

        svc = NotificationService(
            zammad=_DummyZammad(),
            sendxms=_DummyNoop(),
            signal=_DummyNoop(),
        )

        ok = await svc.add_zammad_ack_note(
            session,
            alarm_id=alarm_id,
            ticket_id=42,
            acked_by="Responder",
            acked_at=now,
            note="all good",
        )
        expect(ok is True)

        row = await session.scalar(
            select(AlarmNotification)
            .where(AlarmNotification.channel == "zammad")
            .where(AlarmNotification.alarm_id == alarm_id)
            .order_by(AlarmNotification.created_at.desc())
        )

    expect(row is not None)
    expect(row.payload.get("action") == "ack_update")
    expect(row.payload.get("ticket_id") == 42)


async def test_connector_makes_one_request_per_worker_attempt():
    """Connector retries are owned by the worker, not the provider client."""
    mock_http = AsyncMock(spec=httpx.AsyncClient)
    mock_http.request = AsyncMock(side_effect=httpx.ConnectError("connection refused"))

    config = BaseConnectorConfig(enabled=True, base_url="http://example.test")
    connector = BaseConnector(mock_http, config)

    with pytest.raises(httpx.ConnectError):
        await connector._post("/test", json={"a": 1})

    expect(mock_http.request.call_count == 1)


def test_delivery_retry_classification_uses_transient_http_statuses_only():
    request = httpx.Request("POST", "https://provider.example.test/send")
    permanent = httpx.HTTPStatusError(
        "bad request", request=request, response=httpx.Response(400, request=request)
    )
    throttled = httpx.HTTPStatusError(
        "too many requests", request=request, response=httpx.Response(429, request=request)
    )
    unavailable = httpx.HTTPStatusError(
        "unavailable", request=request, response=httpx.Response(503, request=request)
    )

    expect(is_retryable_delivery_error(permanent) is False)
    expect(is_retryable_delivery_error(throttled) is True)
    expect(is_retryable_delivery_error(unavailable) is True)
