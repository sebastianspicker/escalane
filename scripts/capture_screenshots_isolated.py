"""Capture Mock University screenshots from an isolated served Escalane app.

Used when Docker Compose is unavailable. Boots SQLite + FakeRedis with
simulation, seeds the simulation catalog, drains enqueued worker jobs, then
delegates the Playwright gallery sequence to scripts/demo_capture.py.

Never use --mock-screens for documentation evidence.
"""

from __future__ import annotations

import asyncio
import os
import socket
import sys
import tempfile
from pathlib import Path

import httpx
import uvicorn
from sqlalchemy.ext.asyncio import async_sessionmaker

REPO_ROOT = Path(__file__).resolve().parents[1]
SERVICE_ROOT = REPO_ROOT / "services" / "escalane"
sys.path[:0] = [str(REPO_ROOT), str(SERVICE_ROOT)]

from escalane.api.main import create_app
from escalane.connectors.mock import (
    MockSendXmsClient,
    MockSignalClient,
    MockZammadClient,
    get_mock_store,
)
from escalane.db.session import create_sessionmaker
from escalane.settings import Settings
from escalane.worker.tasks import process_alarm_event
from tests.database_test_helpers import initialized_sqlite_engine
from tests.helpers import FakeRedis

from scripts.demo_capture import (
    CaptureConfig,
    DemoCaptureError,
    run_capture,
)

ADMIN_KEY = "screenshot-review-admin-key"
SEED_FILE = REPO_ROOT / "deploy" / "simulation_seed.yaml"
OUTPUT_DIR = REPO_ROOT / "docs" / "assets" / "screenshots" / "generated"
CURATED = (
    "01-admin-overview.png",
    "04-admin-alarm-detail.png",
    "06-ack-page-triggered-mobile.png",
    "09-simulation-feed.png",
)


def _bound_loopback_socket() -> socket.socket:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    return listener


def _screenshot_settings(db_path: Path) -> Settings:
    """Build the self-contained runtime configuration for screenshot capture."""
    return Settings(
        database_url=f"sqlite+aiosqlite:///{db_path}",
        redis_url="redis://screenshot-fake/0",
        base_url="http://127.0.0.1",
        admin_api_key=ADMIN_KEY,
        zammad_api_token="",
        sendxms_enabled=True,
        signal_enabled=True,
        signal_target_group_id="screenshot-signal-group",
        webhook_enabled=False,
        simulation_enabled=True,
        yelk_ip_allowlist="0.0.0.0/0",
    )


def _worker_context(
    sessionmaker: async_sessionmaker, settings: Settings, fake_redis: FakeRedis
) -> dict:
    """Create worker dependencies for the isolated HTTP-only demo runtime."""
    return {
        "sessionmaker": sessionmaker,
        "settings": settings,
        "redis": fake_redis,
        "http": httpx.AsyncClient(),
        "zammad": MockZammadClient(),
        "sendxms": MockSendXmsClient(),
        "signal": MockSignalClient(),
        "job_try": 1,
    }


async def _wait_for_server(server: uvicorn.Server, task: asyncio.Task[None]) -> None:
    deadline = asyncio.get_running_loop().time() + 10
    while not server.started:
        if task.done():
            task.result()
        if asyncio.get_running_loop().time() > deadline:
            raise TimeoutError("Timed out waiting for screenshot HTTP server")
        await asyncio.sleep(0.02)


async def _drain_jobs(fake_redis: FakeRedis, ctx: dict) -> None:
    """Run queued process_alarm_event jobs until the queue is empty."""
    # Multiple triggers enqueue several jobs; drain with a safety bound.
    for _ in range(50):
        if not fake_redis.jobs:
            return
        name, args = fake_redis.jobs.pop(0)
        if name != "process_alarm_event":
            continue
        payload = args[0] if args else {}
        if not isinstance(payload, dict):
            continue
        await process_alarm_event(ctx, payload)


async def _seed_and_prime(base_url: str, fake_redis: FakeRedis, ctx: dict) -> None:
    headers = {"X-Admin-Key": ADMIN_KEY}
    async with httpx.AsyncClient(base_url=base_url, timeout=30.0) as client:
        for _ in range(40):
            ready = await client.get("/readyz")
            if ready.status_code == 200:
                break
            await asyncio.sleep(0.1)
        else:
            raise RuntimeError(f"Service never became ready: {ready.text}")

        seed = await client.post(
            "/v1/admin/seed",
            headers={**headers, "Content-Type": "application/x-yaml"},
            content=SEED_FILE.read_bytes(),
        )
        if seed.status_code >= 400:
            raise RuntimeError(f"Seed failed ({seed.status_code}): {seed.text}")

        # Clear any prior mock store residue across process reuse.
        clear = await client.post(
            "/v1/simulation/notifications/clear",
            headers={**headers, "Content-Type": "application/json"},
            content=b"{}",
        )
        if clear.status_code >= 400:
            raise RuntimeError(f"Clear simulation failed: {clear.text}")

    await _drain_jobs(fake_redis, ctx)


def _start_server(
    app, settings: Settings
) -> tuple[str, uvicorn.Server, asyncio.Task[None]]:
    """Bind and start a loopback-only server for the isolated capture run."""
    listener = _bound_loopback_socket()
    host, port = listener.getsockname()
    base_url = f"http://{host}:{port}"
    # base_url is used in ACK links rendered into notifications.
    settings.base_url = base_url
    config = uvicorn.Config(
        app, host=host, port=port, lifespan="on", log_level="warning"
    )
    server = uvicorn.Server(config)
    return base_url, server, asyncio.create_task(server.serve(sockets=[listener]))


async def _drain_in_background(
    fake_redis: FakeRedis, worker_ctx: dict, stop: asyncio.Event
) -> None:
    """Drain FakeRedis while the synchronous browser capture triggers alarms."""
    while not stop.is_set():
        await _drain_jobs(fake_redis, worker_ctx)
        await asyncio.sleep(0.05)


def _capture_gallery(base_url: str) -> list[Path]:
    """Run the seeded gallery capture with the isolated app's connection details."""
    return run_capture(
        CaptureConfig(
            base_url=base_url,
            admin_key=ADMIN_KEY,
            output_dir=OUTPUT_DIR,
            seed_file=SEED_FILE,
            timeout_seconds=20.0,
            wait_seconds=30.0,
            headless=True,
            skip_prepare=True,  # already seeded
            mock_screens=False,
        )
    )


async def _capture_while_draining(
    base_url: str, fake_redis: FakeRedis, worker_ctx: dict
) -> list[Path]:
    """Capture browser views while processing each alarm-triggered worker job."""
    stop = asyncio.Event()
    drainer = asyncio.create_task(_drain_in_background(fake_redis, worker_ctx, stop))
    try:
        return await asyncio.to_thread(_capture_gallery, base_url)
    finally:
        stop.set()
        await drainer


def _promote_curated(created: list[Path]) -> None:
    """Copy the four reviewed gallery slots from generated to public assets."""
    public = REPO_ROOT / "docs" / "assets" / "screenshots"
    for name in CURATED:
        source = OUTPUT_DIR / name
        if not source.is_file() or source.stat().st_size < 1000:
            raise RuntimeError(f"Missing or empty capture: {source}")
        target = public / name
        target.write_bytes(source.read_bytes())
        print(f"[capture] promoted {name} ({source.stat().st_size} bytes)")

    print("[capture] All screenshots:")
    for path in created:
        print(f"  - {path} ({path.stat().st_size if path.is_file() else 0} bytes)")


async def _run() -> int:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="escalane-screens-") as tmp:
        db_path = Path(tmp) / "screens.sqlite"
        engine = await initialized_sqlite_engine(db_path)
        sessionmaker = create_sessionmaker(engine)
        fake_redis = FakeRedis()
        settings = _screenshot_settings(db_path)
        # Ensure device token env does not override seed tokens.
        os.environ.pop("YEALINK_DEVICE_TOKEN", None)
        get_mock_store().clear()

        app = create_app(
            settings=settings,
            injected_engine=engine,
            injected_redis=fake_redis,
        )
        worker_ctx = _worker_context(sessionmaker, settings, fake_redis)
        base_url, server, task = _start_server(app, settings)

        try:
            await _wait_for_server(server, task)
            await _seed_and_prime(base_url, fake_redis, worker_ctx)

            created = await _capture_while_draining(base_url, fake_redis, worker_ctx)
            _promote_curated(created)
            return 0
        except DemoCaptureError as exc:
            print(f"[capture] ERROR: {exc}", file=sys.stderr)
            return 1
        finally:
            server.should_exit = True
            await asyncio.wait_for(task, timeout=10)
            await fake_redis.close()
            await engine.dispose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run()))
