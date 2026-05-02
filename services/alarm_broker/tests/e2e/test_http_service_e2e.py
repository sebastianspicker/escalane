from __future__ import annotations

import asyncio
import re
import socket
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest
import pytest_asyncio
import uvicorn
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from alarm_broker.api.main import create_app
from alarm_broker.db.base import Base
from alarm_broker.db.models import Alarm
from alarm_broker.db.session import create_sessionmaker
from alarm_broker.settings import Settings
from tests.helpers import FakeRedis

pytestmark = pytest.mark.e2e

ADMIN_KEY = "e2e-admin-key"
REPO_ROOT = Path(__file__).resolve().parents[4]
SEED_FILE = REPO_ROOT / "deploy" / "seed.example.yaml"


@dataclass(frozen=True)
class ServedApp:
    base_url: str
    sessionmaker: async_sessionmaker


async def _wait_for_server(server: uvicorn.Server, task: asyncio.Task[None]) -> None:
    deadline = asyncio.get_running_loop().time() + 5
    while not server.started:
        if task.done():
            task.result()
        if asyncio.get_running_loop().time() > deadline:
            raise TimeoutError("Timed out waiting for the E2E HTTP server to start.")
        await asyncio.sleep(0.02)


def _bound_loopback_socket() -> socket.socket:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    return listener


@pytest_asyncio.fixture
async def served_app(tmp_path: Path) -> AsyncIterator[ServedApp]:
    db_path = tmp_path / "alarm-broker-e2e.sqlite"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{db_path}",
        redis_url="redis://e2e-fake/0",
        base_url="http://127.0.0.1",
        admin_api_key=ADMIN_KEY,
        zammad_api_token="",
        sendxms_enabled=False,
        signal_enabled=False,
        signal_target_group_id="e2e-signal-group",
        webhook_enabled=False,
        simulation_enabled=True,
    )
    fake_redis = FakeRedis()
    app = create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)

    listener = _bound_loopback_socket()
    host, port = listener.getsockname()
    config = uvicorn.Config(app, host=host, port=port, lifespan="on", log_level="warning")
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve(sockets=[listener]))

    try:
        await _wait_for_server(server, task)
        yield ServedApp(
            base_url=f"http://{host}:{port}",
            sessionmaker=create_sessionmaker(engine),
        )
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, timeout=5)
        await fake_redis.close()
        await engine.dispose()


async def _ack_token_for_alarm(served_app: ServedApp, alarm_id: uuid.UUID) -> str:
    async with served_app.sessionmaker() as session:
        alarm = await session.get(Alarm, alarm_id)
        assert alarm is not None
        assert alarm.ack_token is not None
        return alarm.ack_token


async def _submit_ack_form(client: httpx.AsyncClient, ack_token: str) -> httpx.Response:
    ack_page = await client.get(f"/a/{ack_token}")
    assert ack_page.status_code == 200, ack_page.text
    match = re.search(r'name="csrf_token"\s+value="([^"]+)"', ack_page.text)
    assert match is not None

    return await client.post(
        f"/a/{ack_token}",
        data={
            "acked_by": "E2E Responder",
            "note": "acknowledged by served HTTP E2E",
            "csrf_token": match.group(1),
        },
    )


async def test_served_http_trigger_ack_and_admin_dashboard(served_app: ServedApp) -> None:
    headers = {"X-Admin-Key": ADMIN_KEY}
    async with httpx.AsyncClient(base_url=served_app.base_url, timeout=5.0) as client:
        health = await client.get("/healthz")
        assert health.status_code == 200
        assert health.json() == {"ok": "true"}

        ready = await client.get("/readyz")
        assert ready.status_code == 200
        assert ready.json()["ok"] == "true"

        seed = await client.post(
            "/v1/admin/seed",
            headers={**headers, "Content-Type": "application/x-yaml"},
            content=SEED_FILE.read_bytes(),
        )
        assert seed.status_code == 200, seed.text

        trigger = await client.get("/v1/yealink/alarm", params={"token": "YLK_T54W_3F9A"})
        assert trigger.status_code == 200, trigger.text
        alarm_id = uuid.UUID(trigger.json()["alarm_id"])

        detail = await client.get(f"/v1/alarms/{alarm_id}", headers=headers)
        assert detail.status_code == 200, detail.text
        assert detail.json()["status"] == "triggered"

        ack_token = await _ack_token_for_alarm(served_app, alarm_id)
        ack_response = await _submit_ack_form(client, ack_token)
        assert ack_response.status_code == 200, ack_response.text

        acknowledged = await client.get(f"/v1/alarms/{alarm_id}", headers=headers)
        assert acknowledged.status_code == 200, acknowledged.text
        assert acknowledged.json()["status"] == "acknowledged"
        assert acknowledged.json()["acked_by"] == "E2E Responder"

        login = await client.post(
            "/admin/login",
            data={"admin_key": ADMIN_KEY},
            follow_redirects=False,
        )
        assert login.status_code == 303, login.text

        dashboard = await client.get("/admin")
        assert dashboard.status_code == 200, dashboard.text
        assert str(alarm_id)[:8] in dashboard.text
        assert "acknowledged" in dashboard.text
