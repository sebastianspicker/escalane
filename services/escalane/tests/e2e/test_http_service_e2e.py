"""Served HTTP smoke tests that exercise the ASGI stack over loopback sockets."""

from __future__ import annotations

try:
    from tests.assertions import expect
except ModuleNotFoundError:
    from assertions import expect

import asyncio
import os
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
from sqlalchemy.ext.asyncio import async_sessionmaker

from escalane.api.main import create_app
from escalane.db.models import Alarm
from escalane.db.session import create_sessionmaker
from escalane.settings import Settings
from tests.constants import EMPTY_SECRET_VALUE, TEST_ADMIN_API_KEY, TEST_DEVICE_TOKEN
from tests.database_test_helpers import initialized_sqlite_engine
from tests.helpers import FakeRedis

pytestmark = pytest.mark.e2e

ADMIN_KEY = TEST_ADMIN_API_KEY
REPO_ROOT = Path(__file__).resolve().parents[4]
SEED_FILE = REPO_ROOT / "deploy" / "seed.example.yaml"


@dataclass(frozen=True)
class ServedApp:
    """Addresses and database access exposed by the temporary served application."""

    base_url: str
    sessionmaker: async_sessionmaker


async def _wait_for_server(server: uvicorn.Server, task: asyncio.Task[None]) -> None:
    """Wait for Uvicorn startup while surfacing an early task failure immediately."""
    deadline = asyncio.get_running_loop().time() + 5
    while not server.started:
        if task.done():
            task.result()
        if asyncio.get_running_loop().time() > deadline:
            raise TimeoutError("Timed out waiting for the E2E HTTP server to start.")
        await asyncio.sleep(0.02)


def _bound_loopback_socket() -> socket.socket:
    """Reserve an ephemeral IPv4 loopback port before Uvicorn starts.

    The pre-bound socket removes a race where another process claims the
    selected ephemeral port between discovery and server startup.
    """
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    return listener


@pytest_asyncio.fixture
async def served_app(tmp_path: Path) -> AsyncIterator[ServedApp]:
    """Serve an isolated app over HTTP to cover the real ASGI transport boundary."""
    previous_device_token = os.environ.get("YEALINK_DEVICE_TOKEN")
    os.environ["YEALINK_DEVICE_TOKEN"] = TEST_DEVICE_TOKEN
    db_path = tmp_path / "escalane-e2e.sqlite"
    engine = await initialized_sqlite_engine(db_path)

    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{db_path}",
        redis_url="redis://e2e-fake/0",
        base_url="http://127.0.0.1",
        admin_api_key=ADMIN_KEY,
        zammad_api_token=EMPTY_SECRET_VALUE,
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
        if previous_device_token is None:
            os.environ.pop("YEALINK_DEVICE_TOKEN", None)
        else:
            os.environ["YEALINK_DEVICE_TOKEN"] = previous_device_token
        server.should_exit = True
        await asyncio.wait_for(task, timeout=5)
        await fake_redis.close()
        await engine.dispose()


async def _ack_token_for_alarm(served_app: ServedApp, alarm_id: uuid.UUID) -> str:
    """Read the acknowledgement token from persistence without exposing it via the API."""
    async with served_app.sessionmaker() as session:
        alarm = await session.get(Alarm, alarm_id)
        expect(alarm is not None)
        expect(alarm.ack_token is not None)
        return alarm.ack_token


async def _submit_ack_form(client: httpx.AsyncClient, ack_token: str) -> httpx.Response:
    """Fetch the acknowledgement page first so the POST carries its CSRF token."""
    ack_page = await client.get(f"/a/{ack_token}")
    expect(ack_page.status_code == 200, ack_page.text)
    match = re.search(r'name="csrf_token"\s+value="([^"]+)"', ack_page.text)
    expect(match is not None)

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
        expect(health.status_code == 200)
        expect(health.json() == {"ok": "true"})

        ready = await client.get("/readyz")
        expect(ready.status_code == 200)
        expect(ready.json()["ok"] == "true")

        seed = await client.post(
            "/v1/admin/seed",
            headers={**headers, "Content-Type": "application/x-yaml"},
            content=SEED_FILE.read_bytes(),
        )
        expect(seed.status_code == 200, seed.text)

        trigger = await client.get("/v1/yealink/alarm", params={"token": TEST_DEVICE_TOKEN})
        expect(trigger.status_code == 200, trigger.text)
        alarm_id = uuid.UUID(trigger.json()["alarm_id"])

        detail = await client.get(f"/v1/alarms/{alarm_id}", headers=headers)
        expect(detail.status_code == 200, detail.text)
        expect(detail.json()["status"] == "triggered")

        ack_token = await _ack_token_for_alarm(served_app, alarm_id)
        ack_response = await _submit_ack_form(client, ack_token)
        expect(ack_response.status_code == 200, ack_response.text)

        acknowledged = await client.get(f"/v1/alarms/{alarm_id}", headers=headers)
        expect(acknowledged.status_code == 200, acknowledged.text)
        expect(acknowledged.json()["status"] == "acknowledged")
        expect(acknowledged.json()["acked_by"] == "E2E Responder")

        login = await client.post(
            "/admin/login",
            data={"admin_key": ADMIN_KEY},
            follow_redirects=False,
        )
        expect(login.status_code == 303, login.text)

        dashboard = await client.get("/admin")
        expect(dashboard.status_code == 200, dashboard.text)
        expect(str(alarm_id)[:8] in dashboard.text)
        expect("acknowledged" in dashboard.text)
