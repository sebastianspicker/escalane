"""Map domain and HTTP errors to stable JSON or localized HTML responses."""

from __future__ import annotations

import logging
from typing import cast

from fastapi import FastAPI, Request, status
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ExceptionHandler

from escalane.config.errors import (
    ConfigurationError,
    ConflictError,
    EscalaneError,
    NotFoundError,
    ValidationError,
)
from escalane.web.i18n import normalise_locale, translation_context
from escalane.web.templating import render_template

logger = logging.getLogger("escalane")


_BROWSER_MESSAGES = {
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
    "not_found": {
        "en": "This record does not exist or has been removed.",
        "de": "Dieser Eintrag existiert nicht oder wurde entfernt.",
    },
}


def _is_browser_path(path: str) -> bool:
    return path.startswith("/admin") or path.startswith("/a/")


def _return_url(path: str, status_code: int) -> str | None:
    """Offer a way out that leads somewhere that exists for this audience."""
    if status_code == status.HTTP_401_UNAUTHORIZED:
        return "/admin/login"
    if status_code == status.HTTP_404_NOT_FOUND:
        # Responders have no console session; a link back would be a dead end.
        return None if path.startswith("/a/") else "/admin"
    return path


def _browser_error_page(request: Request, status_code: int, detail: object) -> HTMLResponse:
    locale = request.query_params.get("lang") or request.cookies.get("ui_locale")
    if not locale:
        locale = request.headers.get("accept-language")
    locale = normalise_locale(locale)
    message = _BROWSER_MESSAGES.get(str(detail), {}).get(locale)
    if message is None:
        message = str(detail) if isinstance(detail, str) else "Request failed"
    context = {
        **translation_context(locale),
        "asset_url": "/admin/assets/ui.css",
        "script_url": "/admin/assets/ui.js",
        "worklist_url": "/admin",
        "error": {
            "message": message,
            "reference": getattr(request.state, "request_id", None),
            "return_url": _return_url(request.url.path, status_code),
        },
    }
    return HTMLResponse(render_template("error.html", **context), status_code=status_code)


async def browser_http_error_handler(request: Request, exc: StarletteHTTPException):
    """Render localized HTML failures for browser routes while keeping API errors JSON."""
    if not _is_browser_path(request.url.path):
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
    detail = "not_found" if exc.detail == "Not Found" else exc.detail
    return _browser_error_page(request, exc.status_code, detail)


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
    if _is_browser_path(request.url.path):
        return _browser_error_page(request, status.HTTP_404_NOT_FOUND, "not_found")
    return JSONResponse(status_code=status.HTTP_404_NOT_FOUND, content=exc.to_dict())


async def conflict_error_handler(request: Request, exc: ConflictError):
    """Convert optimistic-concurrency or state conflicts to an explicit 409 response."""
    logger.warning("conflict_error", extra={"error": exc.message, "details": exc.details})
    return JSONResponse(status_code=status.HTTP_409_CONFLICT, content=exc.to_dict())


async def configuration_error_handler(request: Request, exc: ConfigurationError):
    """Log misconfiguration internally and avoid leaking deployment details to callers."""
    logger.error("configuration_error", extra={"error": exc.message, "details": exc.details})
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"error": "Internal configuration error"},
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


def install_exception_handlers(app: FastAPI) -> None:
    """Install standardized error handlers without nesting their implementations."""
    handlers = {
        StarletteHTTPException: browser_http_error_handler,
        ValidationError: validation_error_handler,
        NotFoundError: not_found_error_handler,
        ConflictError: conflict_error_handler,
        ConfigurationError: configuration_error_handler,
        EscalaneError: generic_error_handler,
    }
    for exception_type, handler in handlers.items():
        app.add_exception_handler(exception_type, cast(ExceptionHandler, handler))
