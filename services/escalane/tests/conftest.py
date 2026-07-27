"""Reusable in-process application, database, and queue fixtures for API tests."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from escalane.api.main import create_app
from escalane.db.models import Device, Person, Room, Site
from escalane.db.session import create_sessionmaker
from escalane.settings import Settings

try:
    from tests.constants import EMPTY_SECRET_VALUE, TEST_ADMIN_API_KEY, TEST_DEVICE_TOKEN
    from tests.database_test_helpers import initialized_sqlite_engine
    from tests.helpers import FakeRedis
except ModuleNotFoundError:
    from constants import EMPTY_SECRET_VALUE, TEST_ADMIN_API_KEY, TEST_DEVICE_TOKEN
    from database_test_helpers import initialized_sqlite_engine
    from helpers import FakeRedis


@pytest.fixture
def anyio_backend() -> str:
    """Run async tests on asyncio, matching the application's runtime model."""
    return "asyncio"


@pytest_asyncio.fixture
async def engine(tmp_path) -> AsyncIterator[AsyncEngine]:
    """Provide an isolated SQLite schema with the production migration head recorded."""
    db_path = tmp_path / "test.db"
    engine = await initialized_sqlite_engine(db_path)
    yield engine
    await engine.dispose()


@pytest.fixture
def sessionmaker(engine: AsyncEngine) -> async_sessionmaker:
    """Create test sessions through the same factory used by application code."""
    return create_sessionmaker(engine)


@pytest_asyncio.fixture
async def seeded_db(sessionmaker: async_sessionmaker) -> None:
    """Seed the minimal mapped device topology required by trigger-flow tests."""
    async with sessionmaker() as session:
        session.add(Site(id="bg", name="Standort BG"))
        session.add(Room(id="bg-1.23", site_id="bg", label="Raum 1.23", floor="1"))
        session.add(Person(id="ma-012", display_name="Person X", role="Mitarbeiterin", active=True))
        session.add(
            Device(
                id="ylk-t5-10023",
                vendor="yealink",
                model_family="T5",
                account_ext="10023",
                device_token=TEST_DEVICE_TOKEN,
                person_id="ma-012",
                room_id="bg-1.23",
            )
        )
        await session.commit()


@pytest.fixture
def settings() -> Settings:
    """Return explicit safe settings so tests never depend on host environment values."""
    return Settings(
        database_url="sqlite+aiosqlite:///:memory:",
        redis_url="redis://fake/0",
        base_url="http://localhost:8080",
        admin_api_key=TEST_ADMIN_API_KEY,
        yelk_ip_allowlist="127.0.0.1/32",
        rate_limit_per_minute=10,
        zammad_api_token=EMPTY_SECRET_VALUE,
        sendxms_enabled=False,
        signal_enabled=False,
    )


@pytest.fixture
def fake_redis() -> FakeRedis:
    """Provide a deterministic in-memory Redis substitute for unit and API tests."""
    return FakeRedis()


@pytest.fixture
def app(settings: Settings, engine: AsyncEngine, fake_redis: FakeRedis):
    """Build an ASGI app with all external state injected from test fixtures."""
    return create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)
