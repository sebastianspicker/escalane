"""Versioned master-data configuration routes and mutation helpers."""

from __future__ import annotations

from typing import Annotated, Any
from urllib.parse import urlencode

from fastapi import APIRouter, Cookie, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from escalane.config.settings import Settings
from escalane.configuration.master_data import (
    REQUIRED_FIELDS,
    RESOURCE_FIELDS,
    ResourceNotFoundError,
    ResourceStateConflict,
    UnknownResourceError,
    deactivate_resource,
    delete_resource,
    is_resource_name,
    list_resources,
    save_resource,
)
from escalane.web.admin_session import AdminSession, pop_flash, set_flash
from escalane.web.console import (
    UiPageContext,
    action_session,
    local_redirect,
    render_page,
    requested_locale,
)
from escalane.web.deps import get_app_settings, get_redis, get_session

router = APIRouter()
ConfigurationSessionCookie = Annotated[str | None, Cookie()]
ConfigurationCsrfToken = Annotated[str | None, Form()]
ConfigurationVersion = Annotated[int, Form()]
ConfigurationOptionalVersion = Annotated[int | None, Form()]

_RETAIN_EXISTING = object()


def _page_not_found() -> HTTPException:
    """Report an unknown configuration resource type."""
    return HTTPException(status_code=404, detail="configuration_page_not_found")


def _mutation_http_error(
    exc: UnknownResourceError | ResourceNotFoundError | ResourceStateConflict,
) -> HTTPException:
    """Map feature-level mutation failures to the console's HTTP details."""
    if isinstance(exc, UnknownResourceError):
        return _page_not_found()
    if isinstance(exc, ResourceNotFoundError):
        return HTTPException(status_code=404, detail="resource_not_found")
    return HTTPException(status_code=409, detail=exc.public_detail)


def _resource_row(resource_name: str, item: Any) -> dict[str, Any]:
    """Build editable display data while keeping device tokens out of rendered forms."""
    values: dict[str, Any] = {}
    masked: dict[str, str] = {}
    for field in RESOURCE_FIELDS[resource_name]:
        raw = getattr(item, field)
        if field == "device_token":
            values[field] = ""
            masked[field] = "••••" + raw[-4:] if raw else "-"
        else:
            values[field] = raw or ""
    return {
        "id": item.id,
        "version": item.version,
        "active": item.active,
        "values": values,
        "masked": masked,
    }


@router.get("/admin/configuration/{resource_name}", response_class=HTMLResponse)
async def admin_configuration_list(
    resource_name: str,
    request: Request,
    *,
    page: UiPageContext,
    after: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=50, ge=1, le=100),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    locale, browser_session = page
    try:
        resource_page = await list_resources(session, resource_name, after=after, limit=limit)
    except UnknownResourceError as exc:
        raise _page_not_found() from exc
    items = resource_page.items
    page_url = f"/admin/configuration/{resource_name}"
    params = {"lang": locale, "limit": str(limit)}
    next_url = (
        f"{page_url}?{urlencode({**params, 'after': items[-1].id})}"
        if resource_page.has_next
        else None
    )
    return render_page(
        request,
        "admin_resources.html",
        locale,
        resource_name=resource_name,
        next_page_url=next_url,
        first_page_url=f"{page_url}?{urlencode(params)}" if after is not None else None,
        fields=RESOURCE_FIELDS[resource_name],
        resources=[_resource_row(resource_name, item) for item in items],
        save_action=f"/admin/configuration/{resource_name}/save?lang={locale}",
        csrf_token=browser_session.csrf_token,
        operator_name=browser_session.operator_name,
        logout_action="/admin/logout",
        flash=await pop_flash(get_redis(request), browser_session),
    )


def _resource_form_values(resource_name: str, form: Any, *, creating: bool) -> dict[str, Any]:
    """Extract configured fields while distinguishing omitted values from retained secrets."""
    changed: dict[str, Any] = {}
    for field in RESOURCE_FIELDS[resource_name]:
        submitted = str(form.get(field, "")).strip()
        value = _resource_field_value(field, submitted, creating=creating)
        if value is _RETAIN_EXISTING:
            continue
        changed[field] = value
    changed["active"] = str(form.get("active", "")).lower() in {"1", "true", "on", "yes"}
    return changed


def _resource_field_value(field: str, submitted: str, *, creating: bool) -> str | None | object:
    """Normalize optional values and enforce fields required for a resource type."""
    if field == "device_token":
        return _device_token_value(submitted, creating=creating)
    value = submitted or None
    if field in REQUIRED_FIELDS and value is None:
        raise HTTPException(status_code=422, detail=f"{field}_required")
    return value


def _device_token_value(submitted: str, *, creating: bool) -> str | object:
    """Require a token on create but retain the stored secret when an edit leaves it blank."""
    if submitted:
        return submitted
    if not creating:
        return _RETAIN_EXISTING
    raise HTTPException(status_code=422, detail="device_token_required")


@router.post("/admin/configuration/{resource_name}/save")
async def admin_configuration_save(
    resource_name: str,
    request: Request,
    csrf_token: ConfigurationCsrfToken = None,
    resource_id: str = Form(..., min_length=1, max_length=200),
    version: ConfigurationOptionalVersion = None,
    admin_session: ConfigurationSessionCookie = None,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_app_settings),
) -> RedirectResponse:
    browser_session = await action_session(request, settings, admin_session, csrf_token)
    if not is_resource_name(resource_name):
        raise _page_not_found()
    form = await request.form()
    changed_fields = _resource_form_values(resource_name, form, creating=version is None)
    await save_resource(
        session,
        resource_name=resource_name,
        resource_id=resource_id,
        version=version,
        values=changed_fields,
        operator_name=browser_session.operator_name,
        request_id=getattr(request.state, "request_id", None),
    )
    await set_saved_flash(request, browser_session)
    locale = requested_locale(request, request.query_params.get("lang"))
    return local_redirect(f"/admin/configuration/{resource_name}?lang={locale}")


async def set_saved_flash(request: Request, browser_session: Any) -> None:
    """Store a concise post-redirect confirmation after a resource mutation commits."""
    await set_flash(get_redis(request), browser_session, "success", "saved")


async def _configuration_mutation_context(
    request: Request,
    version: ConfigurationVersion,
    csrf_token: ConfigurationCsrfToken = None,
    admin_session: ConfigurationSessionCookie = None,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_app_settings),
) -> tuple[AdminSession, AsyncSession, int]:
    """Authenticate the operator before a versioned destructive mutation."""
    browser_session = await action_session(request, settings, admin_session, csrf_token)
    return browser_session, session, version


ConfigurationMutationContext = Annotated[
    tuple[AdminSession, AsyncSession, int],
    Depends(_configuration_mutation_context),
]


@router.post("/admin/configuration/{resource_name}/{resource_id}/deactivate")
async def admin_configuration_deactivate(
    resource_name: str,
    resource_id: str,
    request: Request,
    mutation: ConfigurationMutationContext,
) -> RedirectResponse:
    browser_session, session, version = mutation
    try:
        await deactivate_resource(
            session,
            resource_name=resource_name,
            resource_id=resource_id,
            version=version,
            operator_name=browser_session.operator_name,
            request_id=getattr(request.state, "request_id", None),
        )
    except (UnknownResourceError, ResourceNotFoundError, ResourceStateConflict) as exc:
        raise _mutation_http_error(exc) from exc
    locale = requested_locale(request, request.query_params.get("lang"))
    return local_redirect(f"/admin/configuration/{resource_name}?lang={locale}")


@router.post("/admin/configuration/{resource_name}/{resource_id}/delete")
async def admin_configuration_delete(
    resource_name: str,
    resource_id: str,
    request: Request,
    mutation: ConfigurationMutationContext,
) -> RedirectResponse:
    browser_session, session, version = mutation
    try:
        await delete_resource(
            session,
            resource_name=resource_name,
            resource_id=resource_id,
            version=version,
            operator_name=browser_session.operator_name,
            request_id=getattr(request.state, "request_id", None),
        )
    except (UnknownResourceError, ResourceNotFoundError, ResourceStateConflict) as exc:
        raise _mutation_http_error(exc) from exc
    locale = requested_locale(request, request.query_params.get("lang"))
    return local_redirect(f"/admin/configuration/{resource_name}?lang={locale}")
