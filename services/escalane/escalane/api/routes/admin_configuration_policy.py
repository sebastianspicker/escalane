"""Escalation-policy editor routes for the administrative configuration console."""

from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from escalane.api.deps import get_app_settings, get_session
from escalane.api.routes.admin_console import UiPageContext, _action_session, _html
from escalane.api.schemas import EscalationPolicyIn
from escalane.db.models import EscalationPolicy, EscalationStep, EscalationTarget
from escalane.services.admin_audit import add_admin_audit_event
from escalane.services.policy_service import apply_escalation_policy
from escalane.settings import Settings

router = APIRouter()
ConfigurationSessionCookie = Annotated[str | None, Cookie()]
ConfigurationCsrfToken = Annotated[str | None, Form()]
ConfigurationVersion = Annotated[int, Form()]
ConfigurationPolicyJson = Annotated[str, Form(max_length=100_000)]


def _policy_payload(
    policy: EscalationPolicy | None,
    targets: list[EscalationTarget],
    steps: list[EscalationStep],
) -> dict[str, object]:
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
