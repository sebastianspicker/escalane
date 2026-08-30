"""Worker resource ownership and task-registration contracts."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from escalane.config.settings import Settings
from escalane.worker import settings as worker_settings


def _settings(*, simulation: bool) -> Settings:
    return Settings(
        _env_file=None,
        simulation_enabled=simulation,
        database_url="sqlite+aiosqlite:///:memory:",
        admin_api_key="worker-test-key",
        yelk_ip_allowlist="127.0.0.1/32",
    )


@pytest.mark.asyncio
async def test_startup_owns_mock_connectors_in_simulation(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = _settings(simulation=True)
    engine = MagicMock()
    sessionmaker = MagicMock()
    monkeypatch.setattr(worker_settings, "get_settings", lambda: settings)
    monkeypatch.setattr(worker_settings, "create_async_engine_from_settings", lambda _: engine)
    monkeypatch.setattr(worker_settings, "create_sessionmaker", lambda _: sessionmaker)

    context: dict[str, object] = {}
    await worker_settings.startup(context)

    assert context["engine"] is engine
    assert context["sessionmaker"] is sessionmaker
    assert isinstance(context["http"], httpx.AsyncClient)
    assert isinstance(context["zammad"], worker_settings.MockZammadClient)
    await context["http"].aclose()  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_startup_cleans_created_resources_when_connector_setup_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(simulation=False)
    engine = MagicMock()
    engine.dispose = AsyncMock()
    monkeypatch.setattr(worker_settings, "get_settings", lambda: settings)
    monkeypatch.setattr(worker_settings, "create_async_engine_from_settings", lambda _: engine)
    monkeypatch.setattr(worker_settings, "create_sessionmaker", MagicMock())
    monkeypatch.setattr(
        worker_settings, "ZammadClient", MagicMock(side_effect=RuntimeError("bad client"))
    )

    with pytest.raises(RuntimeError, match="bad client"):
        await worker_settings.startup({})

    engine.dispose.assert_awaited_once()


@pytest.mark.asyncio
async def test_shutdown_attempts_both_resources_when_http_close_fails() -> None:
    http = MagicMock(spec=httpx.AsyncClient)
    http.aclose = AsyncMock(side_effect=RuntimeError("close failed"))
    engine = MagicMock()
    engine.dispose = AsyncMock()

    await worker_settings.shutdown({"http": http, "engine": engine})

    http.aclose.assert_awaited_once()
    engine.dispose.assert_awaited_once()


def test_worker_settings_registers_delivery_tasks_and_caches_redis_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(simulation=True)
    worker_settings.WorkerSettings._lazy_redis_settings = None
    monkeypatch.setattr(worker_settings, "get_settings", lambda: settings)

    first = worker_settings.WorkerSettings.redis_settings
    second = worker_settings.WorkerSettings.redis_settings

    assert first is second
    assert (
        worker_settings.recover_incomplete_alarm_events in worker_settings.WorkerSettings.functions
    )
    assert worker_settings.WorkerSettings.max_tries == worker_settings.MAX_DELIVERY_ATTEMPTS
