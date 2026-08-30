"""Health check endpoints: /healthz, /readyz, /healthz/details, /metrics."""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from escalane import __version__
from escalane.config.settings import Settings
from escalane.operations.metrics import render_prometheus_metrics
from escalane.web.deps import get_app_settings, get_redis, get_sessionmaker, require_admin

router = APIRouter()

# Keep this value synchronized with the single Alembic head packaged in
# migrations/versions. The regression test verifies it.
EXPECTED_ALEMBIC_HEAD = "0007"

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
    db_ok = False
    redis_ok = False
    details: dict[str, Any] = {"db": "down", "redis": "down", "schema": "down"}

    try:
        async with sessionmaker() as session:
            await session.execute(text("SELECT 1"))
            schema_status = await _check_schema_version(session)
        db_ok = True
        details["db"] = "ok"
        details["schema"] = schema_status["status"]
    except Exception:
        db_ok = False

    try:
        redis = get_redis(request)
        if hasattr(redis, "ping"):
            await redis.ping()
        elif hasattr(redis, "get"):
            await redis.get("__readyz__")
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

    db_status = await _check_database(sessionmaker)
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
    sessionmaker: async_sessionmaker[AsyncSession] = Depends(get_sessionmaker),
) -> PlainTextResponse:
    from escalane.operations.queries import get_alarm_counts, get_notification_counts

    async with sessionmaker() as session:
        alarm_counts = await get_alarm_counts(session)
        notification_counts = await get_notification_counts(session)

    content = render_prometheus_metrics(
        alarm_counts=alarm_counts,
        notification_counts=notification_counts,
    )
    return PlainTextResponse(content=content, media_type="text/plain")


async def _check_database(sessionmaker: async_sessionmaker[AsyncSession]) -> dict[str, Any]:
    """Check database connectivity and get info.

    Args:
        sessionmaker: Database session factory

    Returns:
        Dictionary with database status information
    """
    try:
        async with sessionmaker() as session:
            await session.execute(text("SELECT 1"))

            schema_status = await _check_schema_version(session)

            return {
                "status": "ok" if schema_status["status"] == "ok" else "error",
                "schema": schema_status,
            }
    except Exception as e:
        return {
            "status": "error",
            "error": str(e),
        }


async def _check_schema_version(session: AsyncSession) -> dict[str, Any]:
    """Return a fail-closed status for the database's Alembic revision."""
    try:
        result = await session.execute(text("SELECT version_num FROM alembic_version"))
    except SQLAlchemyError:
        return {"status": "missing", "expected": EXPECTED_ALEMBIC_HEAD}

    versions = [str(version) for version in result.scalars().all()]
    if not versions:
        return {"status": "empty", "expected": EXPECTED_ALEMBIC_HEAD}
    if len(versions) != 1:
        return {
            "status": "multiple",
            "expected": EXPECTED_ALEMBIC_HEAD,
            "actual": versions,
        }
    if versions[0] != EXPECTED_ALEMBIC_HEAD:
        return {
            "status": "stale",
            "expected": EXPECTED_ALEMBIC_HEAD,
            "actual": versions[0],
        }
    return {"status": "ok", "expected": EXPECTED_ALEMBIC_HEAD, "actual": versions[0]}


async def _check_redis(request: Request) -> dict[str, Any]:
    """Check Redis connectivity.

    Args:
        request: FastAPI request to get Redis connection

    Returns:
        Dictionary with Redis status information
    """
    try:
        redis = get_redis(request)
        start = time.time()

        if hasattr(redis, "ping"):
            await redis.ping()
        elif hasattr(redis, "get"):
            await redis.get("__healthz__")

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
