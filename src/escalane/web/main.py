"""Construct the FastAPI application and enforce its cross-cutting HTTP contracts."""

from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from arq.connections import RedisSettings, create_pool
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from sqlalchemy.ext.asyncio import AsyncEngine

from escalane import __version__
from escalane.config.settings import Settings, get_settings
from escalane.persistence.engine import create_async_engine_from_settings
from escalane.persistence.session import create_sessionmaker
from escalane.telemetry.metrics import record_http_request
from escalane.web.deps import is_secure_request
from escalane.web.errors import install_exception_handlers
from escalane.web.routes import ALL_ROUTERS

logger = logging.getLogger("escalane")


def _build_engine(
    resolved_settings: Settings,
    injected_engine: AsyncEngine | None,
) -> AsyncEngine:
    """Use an injected test engine or create the production engine from validated settings."""
    if injected_engine is not None:
        return injected_engine
    return create_async_engine_from_settings(resolved_settings)


async def _build_redis(resolved_settings: Settings, injected_redis: Any | None) -> Any:
    """Use an injected test pool or create the Redis pool used by API and worker handoff."""
    if injected_redis is not None:
        return injected_redis
    return await create_pool(RedisSettings.from_dsn(str(resolved_settings.redis_url)))


async def _close_lifespan_resources(
    *,
    engine: AsyncEngine | None,
    redis: Any | None,
    injected_engine: AsyncEngine | None,
    injected_redis: Any | None,
) -> None:
    """Close only resources owned by this lifespan, preserving injected test fixtures."""
    if injected_redis is None and redis is not None:
        try:
            await redis.close()
        except Exception:
            logger.exception("api_redis_close_failed")
    if injected_engine is None and engine is not None:
        try:
            await engine.dispose()
        except Exception:
            logger.exception("api_engine_dispose_failed")


def _lifespan(
    *,
    settings: Settings | None = None,
    injected_engine: AsyncEngine | None = None,
    injected_redis: Any | None = None,
):
    """Initialize shared infrastructure once and tear down partial startup safely."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """Attach validated settings, database, and Redis resources to application state."""
        resolved_settings = settings or get_settings()
        resolved_settings.validate_runtime_configuration()
        app.state.settings = resolved_settings

        engine: AsyncEngine | None = None
        redis: Any | None = None

        try:
            engine = _build_engine(resolved_settings, injected_engine)
            app.state.engine = engine
            app.state.sessionmaker = create_sessionmaker(engine)
            redis = await _build_redis(resolved_settings, injected_redis)
            app.state.redis = redis
            yield
        finally:
            await _close_lifespan_resources(
                engine=engine,
                redis=redis,
                injected_engine=injected_engine,
                injected_redis=injected_redis,
            )

    return lifespan


def _safe_log_path(path: str) -> str:
    """Return a log-safe route path with sensitive path segments masked."""
    if path.startswith("/a/"):
        return "/a/{ack_token}"
    return path


def _metric_route(request: Request) -> str:
    """Read the matched template only after routing; bound assets and misses."""
    route = request.scope.get("route")
    if request.scope.get("endpoint") is not None and request.url.path.startswith("/admin/assets/"):
        return "/admin/assets/{path}"
    return getattr(route, "path", None) or "unmatched"


def _install_observability_middleware(app: FastAPI) -> None:
    """Install request correlation, structured logging, and latency metrics for every route."""

    @app.middleware("http")
    async def request_middleware(request: Request, call_next):
        """Log and measure completed or failed requests without leaking ACK capabilities."""
        start = time.perf_counter()
        raw_id = request.headers.get("x-request-id", "")
        # Sanitize: accept only the first 128 printable ASCII chars; generate if empty/invalid.
        request_id = raw_id[:128] if raw_id and raw_id.isprintable() else str(uuid.uuid4())
        request.state.request_id = request_id
        log_route = _safe_log_path(request.url.path)

        try:
            response = await call_next(request)
        except Exception:
            duration_ms = int((time.perf_counter() - start) * 1000)
            logger.exception(
                "request_failed",
                extra={
                    "request_id": request_id,
                    "route": log_route,
                    "status_code": 500,
                    "latency_ms": duration_ms,
                    "alarm_id": getattr(request.state, "alarm_id", None),
                },
            )
            record_http_request(
                method=request.method,
                route=_metric_route(request),
                status_code=500,
                duration_ms=duration_ms,
            )
            raise

        duration_ms = int((time.perf_counter() - start) * 1000)
        response.headers["X-Request-ID"] = request_id

        logger.info(
            "request_completed",
            extra={
                "request_id": request_id,
                "route": log_route,
                "status_code": response.status_code,
                "latency_ms": duration_ms,
                "alarm_id": getattr(request.state, "alarm_id", None),
            },
        )
        record_http_request(
            method=request.method,
            route=_metric_route(request),
            status_code=response.status_code,
            duration_ms=duration_ms,
        )

        return response


def _install_security_headers_middleware(app: FastAPI) -> None:
    """Install response headers that constrain browser execution and caching."""

    @app.middleware("http")
    async def security_headers_middleware(request: Request, call_next):
        """Apply browser hardening after routes produce their response."""
        response = await call_next(request)

        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault(
            "Permissions-Policy",
            "camera=(), geolocation=(), microphone=()",
        )

        # HSTS header on direct HTTPS or trusted TLS-terminating proxy requests.
        if is_secure_request(request, request.app.state.settings):
            response.headers.setdefault(
                "Strict-Transport-Security",
                "max-age=31536000; includeSubDomains",
            )

        csp_policy = (
            "default-src 'self'; "
            "script-src 'self'; "
            "style-src 'self'; "
            "connect-src 'self'; "
            "object-src 'none'; "
            "base-uri 'self'; "
            "form-action 'self'; "
            "frame-ancestors 'none'"
        )
        response.headers.setdefault("Content-Security-Policy", csp_policy)

        # ACK URLs contain bearer capabilities and must not be cached.
        if request.url.path.startswith("/a/"):
            response.headers["Cache-Control"] = "no-store"
            response.headers["Pragma"] = "no-cache"

        return response


def create_app(
    *,
    settings: Settings | None = None,
    injected_engine: AsyncEngine | None = None,
    injected_redis: Any | None = None,
) -> FastAPI:
    """Build a configurable application instance for production and isolated integration tests."""
    resolved_settings = settings or get_settings()

    app = FastAPI(
        title="Escalane",
        version=__version__,
        docs_url="/docs" if resolved_settings.enable_api_docs else None,
        redoc_url="/redoc" if resolved_settings.enable_api_docs else None,
        openapi_url="/openapi.json" if resolved_settings.enable_api_docs else None,
        lifespan=_lifespan(
            settings=resolved_settings,
            injected_engine=injected_engine,
            injected_redis=injected_redis,
        ),
    )

    _install_security_headers_middleware(app)
    _install_observability_middleware(app)
    install_exception_handlers(app)

    assets_dir = Path(__file__).with_name("assets")
    app.mount(
        "/admin/assets",
        StaticFiles(directory=assets_dir, check_dir=False),
        name="admin-assets",
    )

    for router in ALL_ROUTERS:
        app.include_router(router)

    return app


app = create_app()
