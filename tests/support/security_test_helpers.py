"""Shared in-process HTTP fixture for security boundary tests."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from httpx import AsyncClient

from escalane.config.settings import Settings
from tests.support.api_test_helpers import app_client


@asynccontextmanager
async def security_client(
    settings: Settings, engine: Any, fake_redis: Any
) -> AsyncIterator[AsyncClient]:
    """Run one app lifespan around an isolated in-process HTTP client."""
    async with app_client(settings=settings, engine=engine, redis=fake_redis) as client:
        yield client
