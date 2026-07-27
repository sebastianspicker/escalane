"""Configure ARQ worker resources, task registration, and recovery scheduling."""

from __future__ import annotations

import logging

import httpx
from arq.connections import RedisSettings
from arq.cron import cron

from escalane.connectors.mock import MockSendXmsClient, MockSignalClient, MockZammadClient
from escalane.connectors.sendxms import SendXmsClient, SendXmsConfig
from escalane.connectors.signal import SignalClient, SignalConfig
from escalane.connectors.zammad import ZammadClient, ZammadConfig
from escalane.db.engine import create_async_engine_from_settings
from escalane.db.session import create_sessionmaker
from escalane.settings import get_settings
from escalane.worker.tasks import (
    MAX_DELIVERY_ATTEMPTS,
    alarm_acked,
    alarm_created,
    alarm_state_changed,
    escalate,
    process_alarm_event,
    recover_incomplete_alarm_events,
)

logger = logging.getLogger("escalane")


async def startup(ctx: dict) -> None:
    """Validate configuration and create worker-owned database/HTTP/connectors."""
    settings = get_settings()
    settings.validate_runtime_configuration()
    ctx["settings"] = settings

    try:
        engine = create_async_engine_from_settings(settings)
        ctx["engine"] = engine
        ctx["sessionmaker"] = create_sessionmaker(engine)

        http = httpx.AsyncClient(timeout=httpx.Timeout(10.0), trust_env=False)
        ctx["http"] = http

        # Use mock connectors in simulation mode
        if settings.simulation_enabled:
            ctx["zammad"] = MockZammadClient()
            ctx["sendxms"] = MockSendXmsClient()
            ctx["signal"] = MockSignalClient()
        else:
            ctx["zammad"] = ZammadClient(
                http=http,
                config=ZammadConfig(
                    base_url=str(settings.zammad_base_url),
                    api_token=settings.zammad_api_token,
                    group=settings.zammad_group,
                    priority_id_p0=settings.zammad_priority_id_p0,
                    state_id_new=settings.zammad_state_id_new,
                    customer=settings.zammad_customer,
                ),
            )
            ctx["sendxms"] = SendXmsClient(
                http=http,
                config=SendXmsConfig(
                    enabled=settings.sendxms_enabled,
                    base_url=str(settings.sendxms_base_url),
                    api_key=settings.sendxms_api_key,
                    from_name=settings.sendxms_from,
                    send_path=settings.sendxms_send_path,
                ),
            )
            ctx["signal"] = SignalClient(
                http=http,
                config=SignalConfig(
                    enabled=settings.signal_enabled,
                    endpoint=str(settings.signal_cli_endpoint),
                    target_group_id=settings.signal_target_group_id,
                    send_path=settings.signal_send_path,
                ),
            )
    except Exception:
        await _close_worker_resources(ctx)
        raise


async def shutdown(ctx: dict) -> None:
    """Close worker-owned resources independently during graceful shutdown."""
    await _close_worker_resources(ctx)


async def _close_worker_resources(ctx: dict) -> None:
    """Close independently so one cleanup failure cannot leak the other resource."""
    http = ctx.get("http")
    if isinstance(http, httpx.AsyncClient):
        try:
            await http.aclose()
        except Exception:
            logger.exception("worker_http_close_failed")
    engine = ctx.get("engine")
    if engine:
        try:
            await engine.dispose()
        except Exception:
            logger.exception("worker_engine_dispose_failed")


class _LazyRedisSettings:
    """Descriptor that defers RedisSettings creation until first access."""

    def __set_name__(self, owner: type, name: str) -> None:
        self._attr = f"_lazy_{name}"

    def __get__(self, obj: object, objtype: type | None = None) -> RedisSettings:
        owner = objtype or type(obj)
        cached = getattr(owner, self._attr, None)
        if cached is None:
            cached = RedisSettings.from_dsn(str(get_settings().redis_url))
            setattr(owner, self._attr, cached)
        return cached


class WorkerSettings:
    """Declare ARQ lifecycle hooks, retry policy, tasks, and recovery cron job."""

    redis_settings = _LazyRedisSettings()
    on_startup = startup
    on_shutdown = shutdown
    max_tries = MAX_DELIVERY_ATTEMPTS
    functions = [
        alarm_created,
        escalate,
        alarm_acked,
        alarm_state_changed,
        process_alarm_event,
        recover_incomplete_alarm_events,
    ]
    cron_jobs = [
        cron(
            recover_incomplete_alarm_events,
            minute=set(range(60)),
            second=0,
            run_at_startup=True,
            job_id="recover-incomplete-alarm-events",
        )
    ]
