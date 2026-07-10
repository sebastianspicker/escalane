from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from alarm_broker.api.main import create_app
from alarm_broker.db.base import Base
from alarm_broker.db.models import Device, Person, Room, Site
from alarm_broker.db.session import create_sessionmaker
from alarm_broker.settings import Settings

try:
    from tests.constants import EMPTY_SECRET_VALUE, TEST_ADMIN_API_KEY, TEST_DEVICE_TOKEN
    from tests.helpers import FakeRedis
except ModuleNotFoundError:
    from constants import EMPTY_SECRET_VALUE, TEST_ADMIN_API_KEY, TEST_DEVICE_TOKEN
    from helpers import FakeRedis


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest_asyncio.fixture
async def engine(tmp_path) -> AsyncIterator[AsyncEngine]:
    db_path = tmp_path / "test.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
def sessionmaker(engine: AsyncEngine) -> async_sessionmaker:
    return create_sessionmaker(engine)


@pytest_asyncio.fixture
async def seeded_db(sessionmaker: async_sessionmaker) -> None:
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
    return Settings(
        database_url="sqlite+aiosqlite:///:memory:",
        redis_url="redis://fake/0",
        base_url="http://localhost:8080",
        admin_api_key=TEST_ADMIN_API_KEY,
        rate_limit_per_minute=10,
        zammad_api_token=EMPTY_SECRET_VALUE,
        sendxms_enabled=False,
        signal_enabled=False,
    )


@pytest.fixture
def fake_redis() -> FakeRedis:
    return FakeRedis()


@pytest.fixture
def app(settings: Settings, engine: AsyncEngine, fake_redis: FakeRedis):
    return create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)
