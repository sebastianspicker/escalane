"""Seed import preview and apply routes for the administrative configuration console."""

from __future__ import annotations

import hashlib
import secrets

from fastapi import APIRouter, Cookie, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from escalane.api.deps import get_app_settings, get_session
from escalane.api.routes.admin_console import (
    _action_session,
    _html,
    _requested_locale,
    _session_from_request,
)
from escalane.services.admin_audit import add_admin_audit_event
from escalane.services.seed_service import apply_seed_payload, parse_seed_payload
from escalane.settings import Settings

router = APIRouter()


# Render the import form without embedding parsed seed content in the response.
@router.get("/admin/configuration/import", response_class=HTMLResponse)
async def admin_import_page(
    request: Request,
    lang: str | None = Query(default=None),
    admin_session: str | None = Cookie(default=None),
    settings: Settings = Depends(get_app_settings),
) -> HTMLResponse:
    locale = _requested_locale(request, lang)
    browser_session = await _session_from_request(request, settings, admin_session, extend=True)
    return _html(
        request,
        "admin_import.html",
        locale,
        csrf_token=browser_session.csrf_token,
        operator_name=browser_session.operator_name,
        logout_action="/admin/logout",
        preview=None,
        seed_text="",
    )


# Preview or apply a reviewed seed payload, using its hash to detect changes.
@router.post("/admin/configuration/import", response_class=HTMLResponse)
async def admin_import_submit(
    request: Request,
    seed_text: str = Form(..., max_length=1_048_576),
    action: str = Form(..., pattern="^(preview|apply)$"),
    content_hash: str | None = Form(default=None),
    csrf_token: str | None = Form(default=None),
    admin_session: str | None = Cookie(default=None),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_app_settings),
) -> Response:
    locale = _requested_locale(request, None)
    browser_session = await _action_session(request, settings, admin_session, csrf_token)
    raw = seed_text.encode()
    data = parse_seed_payload("application/x-yaml", raw)
    digest = hashlib.sha256(raw).hexdigest()
    if action == "preview":
        return _html(
            request,
            "admin_import.html",
            locale,
            csrf_token=browser_session.csrf_token,
            operator_name=browser_session.operator_name,
            logout_action="/admin/logout",
            preview={"hash": digest, "sections": sorted(data)},
            seed_text=seed_text,
        )
    if content_hash is None or not secrets.compare_digest(content_hash, digest):
        raise HTTPException(status_code=409, detail="import_preview_is_stale")
    add_admin_audit_event(
        session,
        operator_name=browser_session.operator_name,
        action="import",
        resource_type="configuration",
        resource_id=digest,
        changed_fields={"content_hash": digest, "sections": sorted(data)},
        request_id=getattr(request.state, "request_id", None),
    )
    await apply_seed_payload(session, data=data, settings=settings)
    return RedirectResponse("/admin/configuration/import", status_code=303)
