"""In-process HTTP clients and device-trigger helpers."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from httpx import ASGITransport, AsyncClient

from escalane.web.main import create_app
from tests.support.assertions import expect
from tests.support.constants import TEST_DEVICE_TOKEN


@asynccontextmanager
async def app_client(
    *, settings: Any, engine: Any, redis: Any, base_url: str = "http://test"
) -> AsyncIterator[AsyncClient]:
    """Yield a client while the application lifespan is active."""
    app = create_app(settings=settings, injected_engine=engine, injected_redis=redis)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url=base_url) as client:
            yield client


async def trigger_alarm(client: AsyncClient) -> uuid.UUID:
    """Trigger an alarm via the Yealink endpoint and return the alarm UUID."""
    response = await client.get("/v1/yealink/alarm", params={"token": TEST_DEVICE_TOKEN})
    expect(response.status_code == 200, response.text)
    return uuid.UUID(response.json()["alarm_id"])
