from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any

from arq.connections import RedisSettings, create_pool
from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncEngine

from alarm_broker.api.routes import ALL_ROUTERS
from alarm_broker.core.errors import (
    AlarmBrokerError,
    AuthenticationError,
    AuthorizationError,
    ConfigurationError,
    ConflictError,
    ConnectorError,
    NotFoundError,
    RateLimitError,
    ValidationError,
)
from alarm_broker.core.metrics import record_http_request
from alarm_broker.db.engine import create_async_engine_from_url
from alarm_broker.db.session import create_sessionmaker
from alarm_broker.settings import Settings, get_settings

logger = logging.getLogger("alarm_broker")


def _lifespan(
    *,
    settings: Settings | None = None,
    injected_engine: AsyncEngine | None = None,
    injected_redis: Any | None = None,
):
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        resolved_settings = settings or get_settings()
        app.state.settings = resolved_settings

        engine: AsyncEngine
        if injected_engine is not None:
            engine = injected_engine
        else:
            engine = create_async_engine_from_url(
                resolved_settings.database_url,
                pool_size=resolved_settings.db_pool_size,
                max_overflow=resolved_settings.db_max_overflow,
                pool_timeout=resolved_settings.db_pool_timeout,
                pool_recycle=resolved_settings.db_pool_recycle,
                slow_query_log_ms=resolved_settings.slow_query_log_ms,
            )
        app.state.engine = engine
        app.state.sessionmaker = create_sessionmaker(engine)

        if injected_redis is not None:
            app.state.redis = injected_redis
        else:
            app.state.redis = await create_pool(
                RedisSettings.from_dsn(str(resolved_settings.redis_url))
            )

        try:
            yield
        finally:
            redis = getattr(app.state, "redis", None)
            if injected_redis is None and redis is not None:
                await redis.close()
            if injected_engine is None and engine is not None:
                await engine.dispose()

    return lifespan


def _safe_log_path(path: str) -> str:
    """Return a log-safe route path with sensitive path segments masked."""
    if path.startswith("/a/"):
        return "/a/{ack_token}"
    return path


def _install_observability_middleware(app: FastAPI) -> None:
    @app.middleware("http")
    async def request_middleware(request: Request, call_next):
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
                route=log_route,
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
            route=log_route,
            status_code=response.status_code,
            duration_ms=duration_ms,
        )

        return response


def _install_security_headers_middleware(app: FastAPI) -> None:
    @app.middleware("http")
    async def security_headers_middleware(request: Request, call_next):
        response = await call_next(request)

        # Basic security headers
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault(
            "Permissions-Policy",
            "camera=(), geolocation=(), microphone=()",
        )

        # HSTS header (only on HTTPS)
        if request.url.scheme == "https":
            response.headers.setdefault(
                "Strict-Transport-Security",
                "max-age=31536000; includeSubDomains",
            )

        # Content Security Policy (CSP)
        csp_policy = (
            "default-src 'self'; "
            "script-src 'self'; "
            "style-src 'self' 'unsafe-inline'; "
            "object-src 'none'; "
            "base-uri 'self'; "
            "form-action 'self'; "
            "frame-ancestors 'none'"
        )
        response.headers.setdefault("Content-Security-Policy", csp_policy)

        # Anti-caching for ACK pages (contains token in URL)
        if request.url.path.startswith("/a/"):
            response.headers["Cache-Control"] = "no-store"
            response.headers["Pragma"] = "no-cache"

        return response


def _install_exception_handlers(app: FastAPI) -> None:
    """Install custom exception handlers for standardized error responses."""

    @app.exception_handler(ValidationError)
    async def validation_error_handler(request: Request, exc: ValidationError):
        logger.warning(
            "validation_error",
            extra={"error": exc.message, "field": exc.field, "details": exc.details},
        )
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content=exc.to_dict(),
        )

    @app.exception_handler(NotFoundError)
    async def not_found_error_handler(request: Request, exc: NotFoundError):
        logger.info(
            "resource_not_found",
            extra={
                "resource_type": exc.resource_type,
                "resource_id": exc.resource_id,
            },
        )
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content=exc.to_dict(),
        )

    @app.exception_handler(ConflictError)
    async def conflict_error_handler(request: Request, exc: ConflictError):
        logger.warning(
            "conflict_error",
            extra={"error": exc.message, "details": exc.details},
        )
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content=exc.to_dict(),
        )

    @app.exception_handler(AuthenticationError)
    async def authentication_error_handler(request: Request, exc: AuthenticationError):
        logger.warning(
            "authentication_error",
            extra={"error": exc.message},
        )
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content=exc.to_dict(),
        )

    @app.exception_handler(AuthorizationError)
    async def authorization_error_handler(request: Request, exc: AuthorizationError):
        logger.warning(
            "authorization_error",
            extra={"error": exc.message},
        )
        return JSONResponse(
            status_code=status.HTTP_403_FORBIDDEN,
            content=exc.to_dict(),
        )

    @app.exception_handler(RateLimitError)
    async def rate_limit_error_handler(request: Request, exc: RateLimitError):
        logger.warning(
            "rate_limit_exceeded",
            extra={"limit": exc.limit, "window_seconds": exc.window_seconds},
        )
        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            content=exc.to_dict(),
        )

    @app.exception_handler(ConfigurationError)
    async def configuration_error_handler(request: Request, exc: ConfigurationError):
        logger.error(
            "configuration_error",
            extra={"error": exc.message, "details": exc.details},
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": "Internal configuration error"},
        )

    @app.exception_handler(ConnectorError)
    async def connector_error_handler(request: Request, exc: ConnectorError):
        logger.error(
            "connector_error",
            extra={
                "connector": exc.connector,
                "operation": exc.operation,
                "error": str(exc.original_error) if exc.original_error else None,
            },
        )
        # Don't expose internal connector errors to clients
        return JSONResponse(
            status_code=status.HTTP_502_BAD_GATEWAY,
            content={"error": "External service error"},
        )

    @app.exception_handler(AlarmBrokerError)
    async def generic_error_handler(request: Request, exc: AlarmBrokerError):
        logger.error(
            "unhandled_alarm_broker_error",
            extra={"error": exc.message, "details": exc.details},
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"error": "Internal server error"},
        )


def create_app(
    *,
    settings: Settings | None = None,
    injected_engine: AsyncEngine | None = None,
    injected_redis: Any | None = None,
) -> FastAPI:
    resolved_settings = settings or get_settings()

    app = FastAPI(
        title="alarm-broker",
        version="0.1.0",
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
    _install_exception_handlers(app)

    for router in ALL_ROUTERS:
        app.include_router(router)

    return app


app = create_app()
