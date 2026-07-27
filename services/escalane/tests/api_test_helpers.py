"""Shared HTTP and model factories for integration-focused API tests."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

from httpx import ASGITransport, AsyncClient

from escalane.api.main import create_app
from escalane.db.models import Alarm, AlarmStatus


@asynccontextmanager
async def app_client(
    *, settings: Any, engine: Any, redis: Any, base_url: str = "http://test"
) -> AsyncIterator[AsyncClient]:
    """Yield a client while the application lifespan is active."""
    app = create_app(settings=settings, injected_engine=engine, injected_redis=redis)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url=base_url) as client:
            yield client


def make_alarm(**overrides: Any) -> Alarm:
    """Create a complete triggered alarm, with test-specific overrides."""
    if "alarm_id" in overrides:
        overrides["id"] = overrides.pop("alarm_id")
    values = {
        "id": uuid.uuid4(),
        "status": AlarmStatus.TRIGGERED,
        "source": "test",
        "event": "alarm.trigger",
        "person_id": "ma-012",
        "room_id": "bg-1.23",
        "site_id": "bg",
        "device_id": "ylk-t5-10023",
        "severity": "P0",
        "silent": True,
        "ack_token": f"tok-{uuid.uuid4().hex[:8]}",
        "created_at": datetime.now(UTC),
        "meta": {},
        "resolved_at": None,
        "resolved_by": None,
        "cancelled_at": None,
        "cancelled_by": None,
        "acked_at": None,
        "acked_by": None,
    }
    values.update(overrides)
    return Alarm(**values)
