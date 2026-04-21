from __future__ import annotations

import httpx
from arq.connections import RedisSettings
from arq.cron import cron

from alarm_broker.connectors.mock import MockSendXmsClient, MockSignalClient, MockZammadClient
from alarm_broker.connectors.sendxms import SendXmsClient, SendXmsConfig
from alarm_broker.connectors.signal import SignalClient, SignalConfig
from alarm_broker.connectors.zammad import ZammadClient, ZammadConfig
from alarm_broker.db.engine import create_async_engine_from_url
from alarm_broker.db.session import create_sessionmaker
from alarm_broker.settings import get_settings
from alarm_broker.worker.tasks import (
    alarm_acked,
    alarm_created,
    alarm_state_changed,
    escalate,
    process_alarm_event,
    recover_incomplete_alarm_events,
)


async def startup(ctx: dict) -> None:
    settings = get_settings()
    ctx["settings"] = settings

    engine = create_async_engine_from_url(settings.database_url)
    ctx["engine"] = engine
    ctx["sessionmaker"] = create_sessionmaker(engine)

    http = httpx.AsyncClient(timeout=httpx.Timeout(10.0))
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


async def shutdown(ctx: dict) -> None:
    http = ctx.get("http")
    if isinstance(http, httpx.AsyncClient):
        await http.aclose()
    engine = ctx.get("engine")
    if engine:
        await engine.dispose()


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
    redis_settings = _LazyRedisSettings()
    on_startup = startup
    on_shutdown = shutdown
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
