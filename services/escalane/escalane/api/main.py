"""Construct the FastAPI application and enforce its cross-cutting HTTP contracts."""

from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, cast

from arq.connections import RedisSettings, create_pool
from fastapi import FastAPI, Request, status
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.ext.asyncio import AsyncEngine
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ExceptionHandler

from escalane import __version__
from escalane.api.deps import is_secure_request
from escalane.api.i18n import normalise_locale, translation_context
from escalane.api.routes import ALL_ROUTERS
from escalane.api.templating import render_template
from escalane.core.errors import (
    AuthenticationError,
    AuthorizationError,
    ConfigurationError,
    ConflictError,
    ConnectorError,
    EscalaneError,
    NotFoundError,
    RateLimitError,
    ValidationError,
)
from escalane.core.metrics import record_http_request
from escalane.db.engine import create_async_engine_from_settings
from escalane.db.session import create_sessionmaker
from escalane.settings import Settings, get_settings

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
    """Install response headers that constrain browser execution and caching."""

    @app.middleware("http")
    async def security_headers_middleware(request: Request, call_next):
        """Apply browser hardening after routes produce their response."""
        response = await call_next(request)

        # Basic security headers
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

        # Content Security Policy (CSP)
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

        # Anti-caching for ACK pages (contains token in URL)
        if request.url.path.startswith("/a/"):
            response.headers["Cache-Control"] = "no-store"
            response.headers["Pragma"] = "no-cache"

        return response


async def browser_http_error_handler(request: Request, exc: StarletteHTTPException):
    """Render localized HTML failures for browser routes while keeping API errors JSON."""
    if not (request.url.path.startswith("/admin") or request.url.path.startswith("/a/")):
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
    locale = request.query_params.get("lang") or request.cookies.get("ui_locale")
    if not locale:
        locale = request.headers.get("accept-language")
    locale = normalise_locale(locale)
    known_messages = {
        "csrf_invalid": {
            "en": "Security validation failed. Reload the page and try again.",
            "de": "Die Sicherheitsprüfung ist fehlgeschlagen. Laden Sie die Seite neu.",
        },
        "session_expired": {
            "en": "Your session has expired. Sign in again.",
            "de": "Ihre Sitzung ist abgelaufen. Melden Sie sich erneut an.",
        },
        "login_required": {
            "en": "Sign in to use the operator console.",
            "de": "Melden Sie sich an, um die Alarmübersicht zu verwenden.",
        },
    }
    message = known_messages.get(str(exc.detail), {}).get(locale)
    if message is None:
        message = str(exc.detail) if isinstance(exc.detail, str) else "Request failed"
    context = {
        **translation_context(locale),
        "asset_url": "/admin/assets/ui.css",
        "script_url": "/admin/assets/ui.js",
        "worklist_url": "/admin",
        "error": {
            "message": message,
            "reference": getattr(request.state, "request_id", None),
            "return_url": "/admin/login" if exc.status_code == 401 else request.url.path,
        },
    }
    return HTMLResponse(render_template("error.html", **context), status_code=exc.status_code)


async def validation_error_handler(request: Request, exc: ValidationError):
    """Expose domain validation failures as structured 400 responses with diagnostic logs."""
    logger.warning(
        "validation_error",
        extra={"error": exc.message, "field": exc.field, "details": exc.details},
    )
    return JSONResponse(status_code=status.HTTP_400_BAD_REQUEST, content=exc.to_dict())


async def not_found_error_handler(request: Request, exc: NotFoundError):
    """Convert missing domain resources to an auditable 404 response."""
    logger.info(
        "resource_not_found",
        extra={"resource_type": exc.resource_type, "resource_id": exc.resource_id},
    )
    return JSONResponse(status_code=status.HTTP_404_NOT_FOUND, content=exc.to_dict())


async def conflict_error_handler(request: Request, exc: ConflictError):
    """Convert optimistic-concurrency or state conflicts to an explicit 409 response."""
    logger.warning("conflict_error", extra={"error": exc.message, "details": exc.details})
    return JSONResponse(status_code=status.HTTP_409_CONFLICT, content=exc.to_dict())


async def authentication_error_handler(request: Request, exc: AuthenticationError):
    """Return domain authentication failures without exposing credential details."""
    logger.warning("authentication_error", extra={"error": exc.message})
    return JSONResponse(status_code=status.HTTP_401_UNAUTHORIZED, content=exc.to_dict())


async def authorization_error_handler(request: Request, exc: AuthorizationError):
    """Return domain authorization failures while preserving the stable API error shape."""
    logger.warning("authorization_error", extra={"error": exc.message})
    return JSONResponse(status_code=status.HTTP_403_FORBIDDEN, content=exc.to_dict())


async def rate_limit_error_handler(request: Request, exc: RateLimitError):
    """Return rate-limit metadata so clients can back off predictably."""
    logger.warning(
        "rate_limit_exceeded",
        extra={"limit": exc.limit, "window_seconds": exc.window_seconds},
    )
    return JSONResponse(status_code=status.HTTP_429_TOO_MANY_REQUESTS, content=exc.to_dict())


async def configuration_error_handler(request: Request, exc: ConfigurationError):
    """Log misconfiguration internally and avoid leaking deployment details to callers."""
    logger.error("configuration_error", extra={"error": exc.message, "details": exc.details})
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"error": "Internal configuration error"},
    )


async def connector_error_handler(request: Request, exc: ConnectorError):
    """Map upstream connector failures to a retryable gateway error without secret details."""
    logger.error(
        "connector_error",
        extra={
            "connector": exc.connector,
            "operation": exc.operation,
            "error": str(exc.original_error) if exc.original_error else None,
        },
    )
    return JSONResponse(
        status_code=status.HTTP_502_BAD_GATEWAY,
        content={"error": "External service error"},
    )


async def generic_error_handler(request: Request, exc: EscalaneError):
    """Provide a safe fallback for domain errors not covered by a specific handler."""
    logger.error(
        "unhandled_escalane_error",
        extra={"error": exc.message, "details": exc.details},
    )
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"error": "Internal server error"},
    )


def _install_exception_handlers(app: FastAPI) -> None:
    """Install standardized error handlers without nesting their implementations."""
    handlers = {
        StarletteHTTPException: browser_http_error_handler,
        ValidationError: validation_error_handler,
        NotFoundError: not_found_error_handler,
        ConflictError: conflict_error_handler,
        AuthenticationError: authentication_error_handler,
        AuthorizationError: authorization_error_handler,
        RateLimitError: rate_limit_error_handler,
        ConfigurationError: configuration_error_handler,
        ConnectorError: connector_error_handler,
        EscalaneError: generic_error_handler,
    }
    for exception_type, handler in handlers.items():
        app.add_exception_handler(exception_type, cast(ExceptionHandler, handler))


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
    _install_exception_handlers(app)

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
