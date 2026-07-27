"""Versioned master-data, escalation-policy, and seed-import console routes."""

from __future__ import annotations

import hashlib
import json
import secrets
from typing import Annotated, Any

from fastapi import APIRouter, Cookie, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from escalane.api.admin_session import AdminSession, pop_flash
from escalane.api.deps import get_app_settings, get_redis, get_session
from escalane.api.routes.admin_console import (
    UiPageContext,
    _action_session,
    _html,
    _requested_locale,
    _session_from_request,
)
from escalane.api.schemas import EscalationPolicyIn
from escalane.core.errors import ConflictError
from escalane.db.models import (
    Alarm,
    Device,
    EscalationPolicy,
    EscalationStep,
    EscalationTarget,
    Person,
    Room,
    Site,
)
from escalane.services.admin_audit import add_admin_audit_event
from escalane.services.master_data_lifecycle import lock_active_referenced_parents
from escalane.services.policy_service import apply_escalation_policy
from escalane.services.seed_service import apply_seed_payload, parse_seed_payload
from escalane.settings import Settings

router = APIRouter()
ConfigurationSessionCookie = Annotated[str | None, Cookie()]
ConfigurationCsrfToken = Annotated[str | None, Form()]
ConfigurationVersion = Annotated[int, Form()]
ConfigurationOptionalVersion = Annotated[int | None, Form()]
ConfigurationPolicyJson = Annotated[str, Form(max_length=100_000)]

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


def _policy_payload(
    policy: EscalationPolicy | None,
    targets: list[EscalationTarget],
    steps: list[EscalationStep],
) -> dict[str, Any]:
    """Serialize policy data for the editor while intentionally omitting target addresses."""
    return {
        "policy_id": "default",
        "name": policy.name if policy else "Default",
        "targets": [
            {
                "id": item.id,
                "label": item.label,
                "channel": item.channel,
                "address": "",
                "enabled": item.enabled,
            }
            for item in targets
        ],
        "steps": [
            {
                "step_no": step.step_no,
                "after_seconds": step.after_seconds,
                "target_ids": [step.target_id],
            }
            for step in steps
        ],
    }


# Render the versioned escalation-policy editor for an authenticated operator.
@router.get("/admin/configuration/escalation", response_class=HTMLResponse)
async def admin_escalation_page(
    request: Request,
    page: UiPageContext,
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    locale, browser_session = page
    policy = await session.get(EscalationPolicy, "default")
    targets = list((await session.scalars(select(EscalationTarget))).all())
    steps = list(
        (
            await session.scalars(
                select(EscalationStep)
                .where(EscalationStep.policy_id == "default")
                .order_by(EscalationStep.step_no)
            )
        ).all()
    )
    return _html(
        request,
        "admin_policy.html",
        locale,
        policy_json=json.dumps(_policy_payload(policy, targets, steps), indent=2),
        policy_version=policy.version if policy else 0,
        csrf_token=browser_session.csrf_token,
        operator_name=browser_session.operator_name,
        logout_action="/admin/logout",
    )


async def _retain_masked_target_addresses(session: AsyncSession, body: EscalationPolicyIn) -> None:
    """Keep stored addresses when the browser resubmits intentionally blank masked fields."""
    for target in body.targets:
        if target.address:
            continue
        existing_target = await session.get(EscalationTarget, target.id)
        if existing_target is None:
            raise HTTPException(status_code=422, detail="new_target_address_required")
        target.address = existing_target.address


# Apply a concurrency-protected policy update and record the actor's mutation.
@router.post("/admin/configuration/escalation")
async def admin_escalation_save(
    request: Request,
    policy_json: ConfigurationPolicyJson,
    version: ConfigurationVersion,
    csrf_token: ConfigurationCsrfToken = None,
    admin_session: ConfigurationSessionCookie = None,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_app_settings),
) -> RedirectResponse:
    browser_session = await _action_session(request, settings, admin_session, csrf_token)
    try:
        body = EscalationPolicyIn.model_validate_json(policy_json)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="invalid_policy") from exc
    if body.policy_id != "default":
        raise HTTPException(status_code=422, detail="only_default_policy_is_editable")
    await _retain_masked_target_addresses(session, body)
    add_admin_audit_event(
        session,
        operator_name=browser_session.operator_name,
        action="update",
        resource_type="escalation_policy",
        resource_id="default",
        changed_fields={"policy": body.model_dump(mode="json")},
        request_id=getattr(request.state, "request_id", None),
    )
    await apply_escalation_policy(session, body, expected_version=version)
    return RedirectResponse("/admin/configuration/escalation", status_code=303)


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


_RETAIN_EXISTING = object()


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
    from escalane.api.admin_session import set_flash

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
