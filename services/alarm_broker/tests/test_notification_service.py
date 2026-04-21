from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import select

from alarm_broker.connectors.base import BaseConnector, BaseConnectorConfig
from alarm_broker.db.models import Alarm, AlarmNotification, AlarmStatus
from alarm_broker.services.notification_service import NotificationService


class _DummyZammad:
    def enabled(self) -> bool:
        return True

    async def add_internal_note(self, ticket_id: int, subject: str, body: str) -> None:
        assert ticket_id > 0
        assert subject
        assert body


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
                ack_token="ack-note-test",
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
        assert ok is True

        row = await session.scalar(
            select(AlarmNotification)
            .where(AlarmNotification.channel == "zammad")
            .where(AlarmNotification.alarm_id == alarm_id)
            .order_by(AlarmNotification.created_at.desc())
        )

    assert row is not None
    assert row.payload.get("action") == "ack_update"
    assert row.payload.get("ticket_id") == 42


async def test_connector_retry_exactly_3_times():
    """Verify _post_with_retry causes exactly 3 attempts, not 9 (regression test)."""
    mock_http = AsyncMock(spec=httpx.AsyncClient)
    mock_http.request = AsyncMock(side_effect=httpx.ConnectError("connection refused"))

    config = BaseConnectorConfig(enabled=True, base_url="http://example.test")
    connector = BaseConnector(mock_http, config)

    with pytest.raises(httpx.ConnectError):
        await connector._post_with_retry("/test", json={"a": 1})

    assert mock_http.request.call_count == 3
