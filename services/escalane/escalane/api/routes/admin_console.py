"""Shared utilities for the session-authenticated operator console."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import Cookie, Depends, Query, Request
from fastapi.responses import HTMLResponse

from escalane.api.admin_session import AdminSession, require_admin_session, validate_admin_csrf
from escalane.api.deps import get_app_settings, get_redis
from escalane.api.i18n import SUPPORTED_LOCALES, normalise_locale, translation_context
from escalane.api.templating import render_template
from escalane.settings import Settings

UiLanguage = Annotated[str | None, Query()]
UiSessionCookie = Annotated[str | None, Cookie()]


def _requested_locale(request: Request, explicit: str | None) -> str:
    if explicit in SUPPORTED_LOCALES:
        return explicit
    persisted = request.cookies.get("ui_locale")
    if persisted in SUPPORTED_LOCALES:
        return persisted
    return normalise_locale(request.headers.get("accept-language"))


def _base_context(request: Request, locale: str, **values: Any) -> dict[str, Any]:
    settings = getattr(request.app.state, "settings", None)
    return {
        **translation_context(locale),
        "current_path": request.url.path,
        "worklist_url": f"/admin?lang={locale}",
        "asset_url": "/admin/assets/ui.css",
        "script_url": "/admin/assets/ui.js",
        "simulation_enabled": bool(settings and settings.simulation_enabled),
        **values,
    }


def _html(
    request: Request,
    template: str,
    locale: str,
    *,
    status_code: int = 200,
    persist_locale: bool = False,
    **context: Any,
) -> HTMLResponse:
    response = HTMLResponse(
        render_template(template, **_base_context(request, locale, **context)),
        status_code=status_code,
    )
    if persist_locale:
        response.set_cookie("ui_locale", locale, max_age=31_536_000, samesite="lax")
    return response


async def _session_from_request(
    request: Request,
    settings: Settings,
    token: str | None,
    *,
    extend: bool,
) -> AdminSession:
    return await require_admin_session(get_redis(request), settings, token, extend=extend)


async def _action_session(
    request: Request,
    settings: Settings,
    token: str | None,
    csrf_token: str | None,
) -> AdminSession:
    browser_session = await _session_from_request(request, settings, token, extend=True)
    validate_admin_csrf(browser_session, csrf_token)
    return browser_session


async def _page_session(
    request: Request,
    lang: UiLanguage = None,
    admin_session: UiSessionCookie = None,
    settings: Settings = Depends(get_app_settings),
) -> tuple[str, AdminSession]:
    """Resolve page locale and the extended operator session together."""
    locale = _requested_locale(request, lang)
    return locale, await _session_from_request(request, settings, admin_session, extend=True)


UiPageContext = Annotated[tuple[str, AdminSession], Depends(_page_session)]
