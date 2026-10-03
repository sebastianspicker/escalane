"""Seed import preview and apply routes for the administrative configuration console."""

from __future__ import annotations

from fastapi import APIRouter, Cookie, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from escalane.config.settings import Settings
from escalane.configuration.importer import (
    apply_seed_import,
    parse_seed_payload,
    seed_digest,
    seed_digest_matches,
    seed_sections,
)
from escalane.web.console import (
    action_session,
    render_page,
    requested_locale,
    session_from_request,
)
from escalane.web.deps import get_app_settings, get_session

router = APIRouter()


@router.get("/admin/configuration/import", response_class=HTMLResponse)
async def admin_import_page(
    request: Request,
    lang: str | None = Query(default=None),
    admin_session: str | None = Cookie(default=None),
    settings: Settings = Depends(get_app_settings),
) -> HTMLResponse:
    locale = requested_locale(request, lang)
    browser_session = await session_from_request(request, settings, admin_session, extend=True)
    return render_page(
        request,
        "admin_import.html",
        locale,
        csrf_token=browser_session.csrf_token,
        operator_name=browser_session.operator_name,
        logout_action="/admin/logout",
        preview=None,
        seed_text="",
    )


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
    locale = requested_locale(request, None)
    browser_session = await action_session(request, settings, admin_session, csrf_token)
    raw = seed_text.encode()
    data = parse_seed_payload("application/x-yaml", raw)
    digest = seed_digest(raw)
    if action == "preview":
        return render_page(
            request,
            "admin_import.html",
            locale,
            csrf_token=browser_session.csrf_token,
            operator_name=browser_session.operator_name,
            logout_action="/admin/logout",
            preview={"hash": digest, "sections": seed_sections(data)},
            seed_text=seed_text,
        )
    if not seed_digest_matches(content_hash, digest):
        raise HTTPException(status_code=409, detail="import_preview_is_stale")
    await apply_seed_import(
        session,
        data=data,
        digest=digest,
        settings=settings,
        operator_name=browser_session.operator_name,
        request_id=getattr(request.state, "request_id", None),
    )
    return RedirectResponse("/admin/configuration/import", status_code=303)
