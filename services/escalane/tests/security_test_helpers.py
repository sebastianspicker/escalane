"""Shared in-process HTTP fixture for security boundary tests."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from httpx import ASGITransport, AsyncClient

from escalane.api.main import create_app
from escalane.settings import Settings


@asynccontextmanager
async def security_client(
    settings: Settings, engine: Any, fake_redis: Any
) -> AsyncIterator[AsyncClient]:
    """Run one app lifespan around an isolated in-process HTTP client."""
    app = create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield client
