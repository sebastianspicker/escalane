"""Health check endpoints: /healthz, /readyz, /healthz/details, /metrics."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from escalane import __version__
from escalane.config.settings import Settings
from escalane.operations.queries import historical_metrics, outbox_gauges
from escalane.operations.readiness import DatabaseProbe, ping_redis, probe_database
from escalane.operations.worker_snapshot import pool_gauges, read_worker_snapshot
from escalane.telemetry.metrics import render_prometheus_metrics
from escalane.web.deps import get_app_settings, get_redis, get_sessionmaker, require_admin

router = APIRouter()

_start_time = time.time()


def _get_uptime() -> float:
    """Get application uptime in seconds."""
    return time.time() - _start_time


@router.get("/healthz")
async def healthz() -> dict[str, str]:
    """Basic liveness check.

    Returns 200 if the application is running.
    This endpoint is lightweight and doesn't check dependencies.
    """
    return {"ok": "true"}


@router.get("/readyz")
async def readyz(
    request: Request,
    sessionmaker: async_sessionmaker[AsyncSession] = Depends(get_sessionmaker),
) -> JSONResponse:
    """Readiness check with dependency status.

    Returns 200 if all dependencies are available.
    Returns 503 if any dependency is unavailable.
    """
    redis_ok = False
    details: dict[str, Any] = {"db": "down", "redis": "down", "schema": "down"}

    database = await probe_database(sessionmaker)
    db_ok = database.reachable
    if database.schema is not None:
        details["db"] = "ok"
        details["schema"] = database.schema["status"]

    try:
        await ping_redis(get_redis(request), "__readyz__")
        redis_ok = True
        details["redis"] = "ok"
    except Exception:
        redis_ok = False

    if db_ok and redis_ok and details["schema"] == "ok":
        return JSONResponse(status_code=status.HTTP_200_OK, content={"ok": "true", **details})

    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content={"ok": "false", **details},
    )


@router.get("/healthz/details", dependencies=[Depends(require_admin)])
async def healthz_details(
    request: Request,
    sessionmaker: async_sessionmaker[AsyncSession] = Depends(get_sessionmaker),
    settings: Settings = Depends(get_app_settings),
) -> JSONResponse:
    """Detailed health information: version, uptime, DB + Redis status, connector config."""
    details: dict[str, Any] = {
        "application": {
            "name": "escalane",
            "version": __version__,
            "uptime_seconds": round(_get_uptime(), 2),
            "timestamp": datetime.now(UTC).isoformat(),
        },
        "dependencies": {},
        "connectors": {},
    }

    db_status = _database_status(await probe_database(sessionmaker))
    details["dependencies"]["database"] = db_status

    redis_status = await _check_redis(request)
    details["dependencies"]["redis"] = redis_status

    details["connectors"]["zammad"] = {
        "enabled": bool(settings.zammad_api_token),
        "base_url": str(settings.zammad_base_url) if settings.zammad_api_token else None,
    }
    details["connectors"]["sms"] = {
        "enabled": settings.is_sms_enabled(),
        "provider": "sendxms" if settings.is_sms_enabled() else None,
    }
    details["connectors"]["signal"] = {
        "enabled": settings.is_signal_enabled(),
    }

    all_healthy = db_status["status"] == "ok" and redis_status["status"] == "ok"

    status_code = status.HTTP_200_OK if all_healthy else status.HTTP_503_SERVICE_UNAVAILABLE
    details["status"] = "healthy" if all_healthy else "unhealthy"

    return JSONResponse(status_code=status_code, content=details)


@router.get("/metrics", dependencies=[Depends(require_admin)])
async def metrics(
    request: Request,
    sessionmaker: async_sessionmaker[AsyncSession] = Depends(get_sessionmaker),
) -> PlainTextResponse:
    async with sessionmaker() as session:
        alarm_counts, notification_counts = await historical_metrics(session, get_redis(request))
        gauges = await outbox_gauges(session)
    gauges.update(pool_gauges(request.app.state.engine))
    worker_gauges, worker_histograms = await read_worker_snapshot(get_redis(request))
    gauges.update(worker_gauges)

    content = render_prometheus_metrics(
        alarm_counts=alarm_counts,
        notification_counts=notification_counts,
        gauges=gauges,
        worker_histograms=worker_histograms,
    )
    return PlainTextResponse(content=content, media_type="text/plain")


def _database_status(database: DatabaseProbe) -> dict[str, Any]:
    """Shape a database probe for the authenticated health details response."""
    if database.schema is None:
        return {"status": "error", "error": database.error}
    return {"status": "ok" if database.healthy else "error", "schema": database.schema}


async def _check_redis(request: Request) -> dict[str, Any]:
    """Probe Redis and report its round-trip latency for health details."""
    try:
        redis = get_redis(request)
        start = time.time()
        await ping_redis(redis, "__healthz__")
        latency_ms = round((time.time() - start) * 1000, 2)

        return {
            "status": "ok",
            "latency_ms": latency_ms,
        }
    except Exception as e:
        return {
            "status": "error",
            "error": str(e),
        }
