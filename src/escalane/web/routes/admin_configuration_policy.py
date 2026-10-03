"""Escalation-policy editor routes for the administrative configuration console."""

from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from escalane.config.settings import Settings
from escalane.configuration.policy import (
    MissingTargetAddressError,
    PolicyConsoleView,
    load_policy_console,
    save_policy_from_console,
)
from escalane.web.console import UiPageContext, action_session, render_page
from escalane.web.deps import get_app_settings, get_session
from escalane.web.schemas import EscalationPolicyIn, to_escalation_policy_command

router = APIRouter()
ConfigurationSessionCookie = Annotated[str | None, Cookie()]
ConfigurationCsrfToken = Annotated[str | None, Form()]
ConfigurationVersion = Annotated[int, Form()]
ConfigurationPolicyJson = Annotated[str, Form(max_length=100_000)]


def _policy_payload(view: PolicyConsoleView) -> dict[str, object]:
    """Serialize policy data for the editor while intentionally omitting target addresses."""
    return {
        "policy_id": "default",
        "name": view.name,
        "targets": [
            {
                "id": item.id,
                "label": item.label,
                "channel": item.channel,
                "address": "",
                "enabled": item.enabled,
            }
            for item in view.targets
        ],
        "steps": [
            {
                "step_no": step.step_no,
                "after_seconds": step.after_seconds,
                "target_ids": list(step.target_ids),
            }
            for step in view.steps
        ],
    }


@router.get("/admin/configuration/escalation", response_class=HTMLResponse)
async def admin_escalation_page(
    request: Request,
    page: UiPageContext,
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    locale, browser_session = page
    view = await load_policy_console(session)
    return render_page(
        request,
        "admin_policy.html",
        locale,
        policy_json=json.dumps(_policy_payload(view), indent=2),
        policy_version=view.version,
        csrf_token=browser_session.csrf_token,
        operator_name=browser_session.operator_name,
        logout_action="/admin/logout",
    )


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
    browser_session = await action_session(request, settings, admin_session, csrf_token)
    try:
        body = EscalationPolicyIn.model_validate_json(policy_json)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="invalid_policy") from exc
    if body.policy_id != "default":
        raise HTTPException(status_code=422, detail="only_default_policy_is_editable")
    command = to_escalation_policy_command(body)
    try:
        await save_policy_from_console(
            session,
            command,
            expected_version=version,
            operator_name=browser_session.operator_name,
            request_id=getattr(request.state, "request_id", None),
            audited_policy=body.model_dump(mode="json"),
        )
    except MissingTargetAddressError as exc:
        raise HTTPException(status_code=422, detail="new_target_address_required") from exc
    return RedirectResponse("/admin/configuration/escalation", status_code=303)
