"""Login, logout, and session lifecycle for the operator console."""

from __future__ import annotations

import secrets

from fastapi import APIRouter, Cookie, Depends, Form, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from escalane.api.admin_session import (
    SESSION_COOKIE,
    SESSION_TTL_SECONDS,
    create_admin_session,
    destroy_admin_session,
    validate_admin_csrf,
)
from escalane.api.deps import (
    get_app_settings,
    get_client_ip,
    get_redis,
    is_secure_request,
)
from escalane.api.i18n import SUPPORTED_LOCALES
from escalane.api.routes.admin_console import (
    _html,
    _requested_locale,
    _session_from_request,
)
from escalane.api.routes.admin_worklist import _render_alarm_row as _worklist_render_alarm_row
from escalane.core.rate_limit import rate_limit_key
from escalane.core.redis_atomic import increment_with_expiry, redis_text
from escalane.settings import Settings

router = APIRouter()
# Compatibility facade consumed by the admin integration suite.
_render_alarm_row = _worklist_render_alarm_row
_FAILED_LOGIN_LIMIT = 5
_FAILED_LOGIN_WINDOW_SECONDS = 60


def _failed_login_key(request: Request, settings: Settings) -> str:
    return rate_limit_key(
        f"admin-login:{get_client_ip(request, settings) or 'unknown'}",
        0,
    )


def _login_error(locale: str, kind: str) -> str:
    messages = {
        "en": {
            "invalid": "The admin key is not valid.",
            "rate": "Too many failed attempts. Wait one minute and try again.",
            "config": "Administrator login is not configured.",
        },
        "de": {
            "invalid": "Der Admin-Schlüssel ist nicht gültig.",
            "rate": "Zu viele fehlgeschlagene Versuche. Bitte warten Sie eine Minute.",
            "config": "Die Administrator-Anmeldung ist nicht konfiguriert.",
        },
    }
    return messages[locale][kind]


def _login_error_response(
    request: Request, locale: str, kind: str, status_code: int
) -> HTMLResponse:
    return _html(
        request,
        "admin_login.html",
        locale,
        status_code=status_code,
        login_action=f"/admin/login?lang={locale}",
        error=_login_error(locale, kind),
    )


async def _record_login_failure(redis, failure_key: str) -> int:
    return await increment_with_expiry(redis, failure_key, _FAILED_LOGIN_WINDOW_SECONDS)


async def _login_failure_limit_reached(redis, failure_key: str) -> bool:
    raw_count = await redis.get(failure_key)
    if raw_count is None:
        return False
    count = redis_text(raw_count)
    if count is None:
        return True
    try:
        return int(count) >= _FAILED_LOGIN_LIMIT
    except ValueError:
        return True


def _login_success_response(
    request: Request, locale: str, session_token: str, settings: Settings
) -> Response:
    response = RedirectResponse(f"/admin?lang={locale}", status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(
        SESSION_COOKIE,
        session_token,
        httponly=True,
        secure=is_secure_request(request, settings),
        samesite="strict",
        max_age=SESSION_TTL_SECONDS,
    )
    response.set_cookie("ui_locale", locale, max_age=31_536_000, samesite="lax")
    return response


# Render login without touching session state so failed attempts remain isolated.
@router.get("/admin/login", response_class=HTMLResponse)
async def admin_login_page(
    request: Request, lang: str | None = Query(default=None)
) -> HTMLResponse:
    locale = _requested_locale(request, lang)
    return _html(
        request,
        "admin_login.html",
        locale,
        persist_locale=lang in SUPPORTED_LOCALES,
        login_action=f"/admin/login?lang={locale}",
        error=None,
    )


# Exchange the static admin key for a named, rate-limited Redis browser session.
@router.post("/admin/login", response_class=HTMLResponse)
async def admin_login_submit(
    request: Request,
    admin_key: str = Form(...),
    operator_name: str = Form(default="Admin", max_length=120),
    lang: str | None = Query(default=None),
    settings: Settings = Depends(get_app_settings),
) -> Response:
    locale = _requested_locale(request, lang)
    if not settings.admin_api_key:
        return _login_error_response(request, locale, "config", 500)

    redis = get_redis(request)
    failure_key = _failed_login_key(request, settings)
    if await _login_failure_limit_reached(redis, failure_key):
        return _login_error_response(request, locale, "rate", 429)
    if not secrets.compare_digest(admin_key, settings.admin_api_key):
        failed_attempts = await _record_login_failure(redis, failure_key)
        if failed_attempts > _FAILED_LOGIN_LIMIT:
            return _login_error_response(request, locale, "rate", 429)
        return _login_error_response(request, locale, "invalid", 401)

    await redis.delete(failure_key)
    named = operator_name.strip() or "Admin"
    browser_session = await create_admin_session(redis, settings, named)
    return _login_success_response(request, locale, browser_session.token, settings)


# Destroy the server-side session only after validating its separate CSRF token.
@router.post("/admin/logout")
async def admin_logout(
    request: Request,
    csrf_token: str | None = Form(default=None),
    admin_session: str | None = Cookie(default=None),
    settings: Settings = Depends(get_app_settings),
) -> Response:
    browser_session = await _session_from_request(request, settings, admin_session, extend=False)
    validate_admin_csrf(browser_session, csrf_token)
    await destroy_admin_session(get_redis(request), admin_session)
    response = RedirectResponse("/admin/login", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie(SESSION_COOKIE)
    return response


# Refresh the bounded Redis/session-cookie lifetime after an explicit operator action.
@router.post("/admin/session/extend")
async def admin_extend_session(
    request: Request,
    csrf_token: str | None = Form(default=None),
    admin_session: str | None = Cookie(default=None),
    settings: Settings = Depends(get_app_settings),
) -> RedirectResponse:
    browser_session = await _session_from_request(request, settings, admin_session, extend=True)
    validate_admin_csrf(browser_session, csrf_token)
    response = RedirectResponse("/admin", status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(
        SESSION_COOKIE,
        browser_session.token,
        httponly=True,
        secure=is_secure_request(request, settings),
        samesite="strict",
        max_age=SESSION_TTL_SECONDS,
    )
    return response
