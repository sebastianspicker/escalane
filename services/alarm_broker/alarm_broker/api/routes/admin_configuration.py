"""Versioned master-data, escalation-policy, and seed-import console routes."""

from __future__ import annotations

import hashlib
import json
import secrets
from typing import Any

from fastapi import APIRouter, Cookie, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from alarm_broker.api.deps import get_app_settings, get_redis, get_session
from alarm_broker.api.routes.admin_ui import (
    _action_session,
    _html,
    _requested_locale,
    _session_from_request,
)
from alarm_broker.api.schemas import EscalationPolicyIn
from alarm_broker.db.models import (
    Alarm,
    Device,
    EscalationPolicy,
    EscalationStep,
    EscalationTarget,
    Person,
    Room,
    Site,
)
from alarm_broker.services.admin_audit import add_admin_audit_event
from alarm_broker.services.master_data_lifecycle import require_current_version
from alarm_broker.services.policy_service import apply_escalation_policy
from alarm_broker.services.seed_service import apply_seed_payload, parse_seed_payload
from alarm_broker.settings import Settings

router = APIRouter()

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
_REQUIRED_FIELDS = frozenset({"name", "label", "display_name", "vendor", "model_family"})


def _resource_model(resource_name: str) -> Any:
    model = _RESOURCE_MODELS.get(resource_name)
    if model is None:
        raise HTTPException(status_code=404, detail="configuration_page_not_found")
    return model


def _resource_row(resource_name: str, item: Any) -> dict[str, Any]:
    values: dict[str, Any] = {}
    masked: dict[str, str] = {}
    for field in _RESOURCE_FIELDS[resource_name]:
        raw = getattr(item, field)
        if field == "device_token":
            values[field] = ""
            masked[field] = "••••" + raw[-4:] if raw else "—"
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


@router.get("/admin/configuration/escalation", response_class=HTMLResponse)
async def admin_escalation_page(
    request: Request,
    lang: str | None = Query(default=None),
    admin_session: str | None = Cookie(default=None),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_app_settings),
) -> HTMLResponse:
    locale = _requested_locale(request, lang)
    browser_session = await _session_from_request(request, settings, admin_session, extend=True)
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
    for target in body.targets:
        if target.address:
            continue
        existing_target = await session.get(EscalationTarget, target.id)
        if existing_target is None:
            raise HTTPException(status_code=422, detail="new_target_address_required")
        target.address = existing_target.address


def _validate_policy_version(current: EscalationPolicy | None, version: int) -> None:
    if current is not None:
        require_current_version(current, version)
        current.version += 1
        return
    if version != 0:
        raise HTTPException(status_code=409, detail="policy_version_conflict")


@router.post("/admin/configuration/escalation")
async def admin_escalation_save(
    request: Request,
    policy_json: str = Form(..., max_length=100_000),
    version: int = Form(...),
    csrf_token: str | None = Form(default=None),
    admin_session: str | None = Cookie(default=None),
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
    _validate_policy_version(await session.get(EscalationPolicy, "default"), version)
    add_admin_audit_event(
        session,
        operator_name=browser_session.operator_name,
        action="update",
        resource_type="escalation_policy",
        resource_id="default",
        changed_fields={"policy": body.model_dump(mode="json")},
        request_id=getattr(request.state, "request_id", None),
    )
    await apply_escalation_policy(session, body)
    return RedirectResponse("/admin/configuration/escalation", status_code=303)


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


@router.get("/admin/configuration/{resource_name}", response_class=HTMLResponse)
async def admin_configuration_list(
    resource_name: str,
    request: Request,
    lang: str | None = Query(default=None),
    admin_session: str | None = Cookie(default=None),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_app_settings),
) -> HTMLResponse:
    locale = _requested_locale(request, lang)
    browser_session = await _session_from_request(request, settings, admin_session, extend=True)
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
    )


async def _load_edit_item(
    session: AsyncSession,
    model: Any,
    resource_id: str,
    version: int | None,
) -> tuple[Any, bool]:
    item = await session.get(model, resource_id)
    if item is None:
        item = model(id=resource_id)
        session.add(item)
        return item, True
    if version is None:
        raise HTTPException(status_code=409, detail="version_required")
    require_current_version(item, version)
    return item, False


def _apply_resource_form(
    item: Any, resource_name: str, form: Any, *, creating: bool
) -> dict[str, Any]:
    changed: dict[str, Any] = {}
    for field in _RESOURCE_FIELDS[resource_name]:
        submitted = str(form.get(field, "")).strip()
        value = _resource_field_value(field, submitted, creating=creating)
        if value is _RETAIN_EXISTING:
            continue
        setattr(item, field, value)
        changed[field] = value
    item.active = str(form.get("active", "")).lower() in {"1", "true", "on", "yes"}
    item.version = 1 if creating else item.version + 1
    return {**changed, "active": item.active}


_RETAIN_EXISTING = object()


def _resource_field_value(field: str, submitted: str, *, creating: bool) -> str | None | object:
    if field == "device_token":
        return _device_token_value(submitted, creating=creating)
    value = submitted or None
    if field in _REQUIRED_FIELDS and value is None:
        raise HTTPException(status_code=422, detail=f"{field}_required")
    return value


def _device_token_value(submitted: str, *, creating: bool) -> str | object:
    if submitted:
        return submitted
    if not creating:
        return _RETAIN_EXISTING
    raise HTTPException(status_code=422, detail="device_token_required")


@router.post("/admin/configuration/{resource_name}/save")
async def admin_configuration_save(
    resource_name: str,
    request: Request,
    csrf_token: str | None = Form(default=None),
    resource_id: str = Form(..., min_length=1, max_length=200),
    version: int | None = Form(default=None),
    admin_session: str | None = Cookie(default=None),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_app_settings),
) -> RedirectResponse:
    browser_session = await _action_session(request, settings, admin_session, csrf_token)
    item, creating = await _load_edit_item(
        session, _resource_model(resource_name), resource_id, version
    )
    changed_fields = _apply_resource_form(
        item, resource_name, await request.form(), creating=creating
    )
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
    from alarm_broker.api.admin_session import set_flash

    await set_flash(get_redis(request), browser_session, "success", "saved")


async def _active_dependency_counts(
    session: AsyncSession, resource_name: str, resource_id: str
) -> dict[str, int]:
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


@router.post("/admin/configuration/{resource_name}/{resource_id}/deactivate")
async def admin_configuration_deactivate(
    resource_name: str,
    resource_id: str,
    request: Request,
    csrf_token: str | None = Form(default=None),
    version: int = Form(...),
    admin_session: str | None = Cookie(default=None),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_app_settings),
) -> RedirectResponse:
    browser_session = await _action_session(request, settings, admin_session, csrf_token)
    item = await session.get(_resource_model(resource_name), resource_id)
    if item is None:
        raise HTTPException(status_code=404, detail="resource_not_found")
    require_current_version(item, version)
    blockers = await _active_dependency_counts(session, resource_name, resource_id)
    if any(blockers.values()):
        raise HTTPException(status_code=409, detail=blockers)
    item.active = False
    item.version += 1
    add_admin_audit_event(
        session,
        operator_name=browser_session.operator_name,
        action="deactivate",
        resource_type=resource_name,
        resource_id=resource_id,
        changed_fields={"active": False},
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    return RedirectResponse(f"/admin/configuration/{resource_name}", status_code=303)


async def _historical_dependency_count(
    session: AsyncSession, resource_name: str, resource_id: str
) -> int:
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


@router.post("/admin/configuration/{resource_name}/{resource_id}/delete")
async def admin_configuration_delete(
    resource_name: str,
    resource_id: str,
    request: Request,
    csrf_token: str | None = Form(default=None),
    version: int = Form(...),
    admin_session: str | None = Cookie(default=None),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_app_settings),
) -> RedirectResponse:
    browser_session = await _action_session(request, settings, admin_session, csrf_token)
    item = await session.get(_resource_model(resource_name), resource_id)
    if item is None:
        raise HTTPException(status_code=404, detail="resource_not_found")
    require_current_version(item, version)
    if await _historical_dependency_count(session, resource_name, resource_id):
        raise HTTPException(status_code=409, detail="resource_is_referenced_deactivate_instead")
    if item.active:
        raise HTTPException(status_code=409, detail="deactivate_before_delete")
    await session.delete(item)
    add_admin_audit_event(
        session,
        operator_name=browser_session.operator_name,
        action="delete",
        resource_type=resource_name,
        resource_id=resource_id,
        request_id=getattr(request.state, "request_id", None),
    )
    await session.commit()
    return RedirectResponse(f"/admin/configuration/{resource_name}", status_code=303)
