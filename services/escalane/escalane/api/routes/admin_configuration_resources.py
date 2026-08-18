"""Versioned master-data configuration routes and mutation helpers."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Cookie, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from escalane.api.admin_session import AdminSession, pop_flash, set_flash
from escalane.api.deps import get_app_settings, get_redis, get_session
from escalane.api.routes.admin_console import UiPageContext, _action_session, _html
from escalane.core.errors import ConflictError
from escalane.db.models import Alarm, Device, Person, Room, Site
from escalane.services.admin_audit import add_admin_audit_event
from escalane.services.master_data_lifecycle import lock_active_referenced_parents
from escalane.settings import Settings

router = APIRouter()
ConfigurationSessionCookie = Annotated[str | None, Cookie()]
ConfigurationCsrfToken = Annotated[str | None, Form()]
ConfigurationVersion = Annotated[int, Form()]
ConfigurationOptionalVersion = Annotated[int | None, Form()]

_RESOURCE_MODELS: dict[str, Any] = {
    "sites": Site,
    "rooms": Room,
    "people": Person,
    "devices": Device,
}
_RESOURCE_FIELDS = {
    "sites": ("name",),
    "rooms": ("site_id", "label", "floor", "notes"),
    "people": ("display_name", "role", "phone_mobile", "phone_ext"),
    "devices": (
        "vendor",
        "model_family",
        "mac",
        "account_ext",
        "device_token",
        "person_id",
        "room_id",
    ),
}
_REQUIRED_FIELDS = frozenset({"name", "site_id", "label", "display_name", "vendor", "model_family"})
_RETAIN_EXISTING = object()


def _mutation_applied(result: Any) -> bool:
    """Normalize SQLAlchemy row counts for optimistic-concurrency checks."""
    return bool(result.rowcount)


def _raise_version_conflict(resource_id: str) -> None:
    """Report stale form submissions rather than overwrite another operator's edit."""
    raise ConflictError(
        "Resource has changed since it was loaded",
        details={"resource_id": resource_id},
    )


def _resource_model(resource_name: str) -> Any:
    """Resolve an allowlisted master-data type and reject unknown configuration pages."""
    model = _RESOURCE_MODELS.get(resource_name)
    if model is None:
        raise HTTPException(status_code=404, detail="configuration_page_not_found")
    return model


def _resource_row(resource_name: str, item: Any) -> dict[str, Any]:
    """Build editable display data while keeping device tokens out of rendered forms."""
    values: dict[str, Any] = {}
    masked: dict[str, str] = {}
    for field in _RESOURCE_FIELDS[resource_name]:
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


# List one allowlisted master-data resource type with version tokens for edits.
@router.get("/admin/configuration/{resource_name}", response_class=HTMLResponse)
async def admin_configuration_list(
    resource_name: str,
    request: Request,
    *,
    page: UiPageContext,
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    locale, browser_session = page
    model = _resource_model(resource_name)
    items = list((await session.scalars(select(model).order_by(model.id))).all())
    return _html(
        request,
        "admin_resources.html",
        locale,
        resource_name=resource_name,
        fields=_RESOURCE_FIELDS[resource_name],
        resources=[_resource_row(resource_name, item) for item in items],
        save_action=f"/admin/configuration/{resource_name}/save",
        csrf_token=browser_session.csrf_token,
        operator_name=browser_session.operator_name,
        logout_action="/admin/logout",
        flash=await pop_flash(get_redis(request), browser_session),
    )


def _resource_form_values(resource_name: str, form: Any, *, creating: bool) -> dict[str, Any]:
    """Extract configured fields while distinguishing omitted values from retained secrets."""
    changed: dict[str, Any] = {}
    for field in _RESOURCE_FIELDS[resource_name]:
        submitted = str(form.get(field, "")).strip()
        value = _resource_field_value(field, submitted, creating=creating)
        if value is _RETAIN_EXISTING:
            continue
        changed[field] = value
    changed["active"] = str(form.get("active", "")).lower() in {"1", "true", "on", "yes"}
    return changed


async def _create_resource(
    session: AsyncSession, model: Any, resource_id: str, values: dict[str, Any]
) -> None:
    """Insert a new master-data row after the route validates referenced parents."""
    session.add(model(id=resource_id, **values))
    try:
        await session.flush()
    except IntegrityError as exc:
        await session.rollback()
        raise ConflictError(
            "Resource has changed since it was loaded",
            details={"resource_id": resource_id},
        ) from exc


async def _update_resource_if_current(
    session: AsyncSession,
    model: Any,
    resource_id: str,
    version: int,
    values: dict[str, Any],
) -> None:
    """Update only the expected version to prevent lost operator edits."""
    result = await session.execute(
        update(model)
        .where(model.id == resource_id, model.version == version)
        .values(**values, version=model.version + 1)
    )
    if not _mutation_applied(result):
        _raise_version_conflict(resource_id)


async def _deactivate_resource_if_current(
    session: AsyncSession, model: Any, resource_id: str, version: int
) -> None:
    """Deactivate only the expected version to make stale forms fail predictably."""
    result = await session.execute(
        update(model)
        .where(model.id == resource_id, model.version == version)
        .values(active=False, version=model.version + 1)
    )
    if not _mutation_applied(result):
        _raise_version_conflict(resource_id)


async def _delete_resource_if_current(
    session: AsyncSession, model: Any, resource_id: str, version: int
) -> None:
    """Delete only the expected version after dependency checks permit removal."""
    result = await session.execute(
        delete(model).where(model.id == resource_id, model.version == version)
    )
    if not _mutation_applied(result):
        _raise_version_conflict(resource_id)


def _resource_field_value(field: str, submitted: str, *, creating: bool) -> str | None | object:
    """Normalize optional values and enforce fields required for a resource type."""
    if field == "device_token":
        return _device_token_value(submitted, creating=creating)
    value = submitted or None
    if field in _REQUIRED_FIELDS and value is None:
        raise HTTPException(status_code=422, detail=f"{field}_required")
    return value


def _device_token_value(submitted: str, *, creating: bool) -> str | object:
    """Require a token on create but retain the stored secret when an edit leaves it blank."""
    if submitted:
        return submitted
    if not creating:
        return _RETAIN_EXISTING
    raise HTTPException(status_code=422, detail="device_token_required")


# Create or update master data with an audited, version-safe mutation.
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
    browser_session = await _action_session(request, settings, admin_session, csrf_token)
    model = _resource_model(resource_name)
    form = await request.form()
    if version is None:
        creating = True
        changed_fields = _resource_form_values(resource_name, form, creating=True)
        await lock_active_referenced_parents(
            session, resource_name=resource_name, values=changed_fields
        )
        await _create_resource(session, model, resource_id, changed_fields)
    else:
        creating = False
        changed_fields = _resource_form_values(resource_name, form, creating=False)
        await lock_active_referenced_parents(
            session, resource_name=resource_name, values=changed_fields
        )
        await _update_resource_if_current(session, model, resource_id, version, changed_fields)
    add_admin_audit_event(
        session,
        operator_name=browser_session.operator_name,
        action="create" if creating else "update",
        resource_type=resource_name,
        resource_id=resource_id,
        changed_fields=changed_fields,
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    await set_saved_flash(request, browser_session)
    return RedirectResponse(f"/admin/configuration/{resource_name}", status_code=303)


async def set_saved_flash(request: Request, browser_session: Any) -> None:
    """Store a concise post-redirect confirmation after a resource mutation commits."""
    await set_flash(get_redis(request), browser_session, "success", "saved")


async def _active_dependency_counts(
    session: AsyncSession, resource_name: str, resource_id: str
) -> dict[str, int]:
    """Count active child records that would become unusable after deactivation."""
    filters: dict[str, tuple[Any, Any]] = {
        "sites": (Room.id, (Room.site_id == resource_id) & Room.active.is_(True)),
        "rooms": (Device.id, (Device.room_id == resource_id) & Device.active.is_(True)),
        "people": (Device.id, (Device.person_id == resource_id) & Device.active.is_(True)),
    }
    selected = filters.get(resource_name)
    if selected is None:
        return {}
    column, condition = selected
    count = int(await session.scalar(select(func.count(column)).where(condition)) or 0)
    return {"active_dependencies": count}


async def _lock_resource_for_mutation(
    session: AsyncSession, model: Any, resource_id: str
) -> Any | None:
    """Lock a parent row before checking/deleting dependent rows.

    PostgreSQL's ``FOR UPDATE`` also blocks concurrent child inserts which
    need a key-share lock for their foreign key. SQLite accepts this as a
    no-op, so its tests cover response semantics rather than lock behavior.
    """
    return await session.scalar(select(model).where(model.id == resource_id).with_for_update())


async def _commit_resource_mutation(session: AsyncSession, resource_id: str) -> None:
    """Commit a dependency-checked mutation and normalize concurrent reference failures."""
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ConflictError(
            "Resource is referenced by a concurrent change",
            details={"resource_id": resource_id},
        ) from exc


async def _configuration_mutation_context(
    request: Request,
    resource_name: str,
    resource_id: str,
    version: ConfigurationVersion,
    csrf_token: ConfigurationCsrfToken = None,
    admin_session: ConfigurationSessionCookie = None,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_app_settings),
) -> tuple[AdminSession, Any, Any, AsyncSession, int]:
    """Authenticate and lock a versioned resource before a destructive mutation."""
    browser_session = await _action_session(request, settings, admin_session, csrf_token)
    model = _resource_model(resource_name)
    item = await _lock_resource_for_mutation(session, model, resource_id)
    if item is None:
        raise HTTPException(status_code=404, detail="resource_not_found")
    if item.version != version:
        _raise_version_conflict(resource_id)
    return browser_session, model, item, session, version


ConfigurationMutationContext = Annotated[
    tuple[AdminSession, Any, Any, AsyncSession, int],
    Depends(_configuration_mutation_context),
]


# Deactivate only when active dependencies will not be orphaned.
@router.post("/admin/configuration/{resource_name}/{resource_id}/deactivate")
async def admin_configuration_deactivate(
    resource_name: str,
    resource_id: str,
    request: Request,
    mutation: ConfigurationMutationContext,
) -> RedirectResponse:
    browser_session, model, item, session, version = mutation
    blockers = await _active_dependency_counts(session, resource_name, resource_id)
    if any(blockers.values()):
        raise HTTPException(status_code=409, detail=blockers)
    await _deactivate_resource_if_current(session, model, resource_id, version)
    add_admin_audit_event(
        session,
        operator_name=browser_session.operator_name,
        action="deactivate",
        resource_type=resource_name,
        resource_id=resource_id,
        changed_fields={"active": False},
        request_id=getattr(request.state, "request_id", None),
    )
    await _commit_resource_mutation(session, resource_id)
    return RedirectResponse(f"/admin/configuration/{resource_name}", status_code=303)


async def _historical_dependency_count(
    session: AsyncSession, resource_name: str, resource_id: str
) -> int:
    """Count historical references that require the resource to remain audit-visible."""
    conditions: dict[str, list[tuple[Any, Any]]] = {
        "sites": [(Room.id, Room.site_id == resource_id), (Alarm.id, Alarm.site_id == resource_id)],
        "rooms": [
            (Device.id, Device.room_id == resource_id),
            (Alarm.id, Alarm.room_id == resource_id),
        ],
        "people": [
            (Device.id, Device.person_id == resource_id),
            (Alarm.id, Alarm.person_id == resource_id),
        ],
        "devices": [(Alarm.id, Alarm.device_id == resource_id)],
    }
    counts = [
        int(await session.scalar(select(func.count(column)).where(condition)) or 0)
        for column, condition in conditions[resource_name]
    ]
    return sum(counts)


# Delete only inactive configuration that has no live or historical references.
@router.post("/admin/configuration/{resource_name}/{resource_id}/delete")
async def admin_configuration_delete(
    resource_name: str,
    resource_id: str,
    request: Request,
    mutation: ConfigurationMutationContext,
) -> RedirectResponse:
    browser_session, model, item, session, version = mutation
    if await _historical_dependency_count(session, resource_name, resource_id):
        raise HTTPException(status_code=409, detail="resource_is_referenced_deactivate_instead")
    if item.active:
        raise HTTPException(status_code=409, detail="deactivate_before_delete")
    await _delete_resource_if_current(session, model, resource_id, version)
    add_admin_audit_event(
        session,
        operator_name=browser_session.operator_name,
        action="delete",
        resource_type=resource_name,
        resource_id=resource_id,
        request_id=getattr(request.state, "request_id", None),
    )
    await _commit_resource_mutation(session, resource_id)
    return RedirectResponse(f"/admin/configuration/{resource_name}", status_code=303)
