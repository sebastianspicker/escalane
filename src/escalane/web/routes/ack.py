"""Capability-link ACK page.

The `ack_token` in the URL is the responder's authority to acknowledge the
alarm. The browser form still gets CSRF protection and a per-IP rate limit so a
captured page cannot be submitted blindly from another origin.
"""

from __future__ import annotations

import logging
import secrets

from fastapi import APIRouter, Cookie, Depends, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from escalane.alarms.enrichment import enrich_alarm_context
from escalane.alarms.lifecycle import apply_alarm_state_change, get_alarm_by_ack_token
from escalane.config.settings import Settings
from escalane.contracts.alarms import AlarmStatus
from escalane.persistence.models import Alarm
from escalane.runtime.rate_limit import minute_bucket, rate_limit_key
from escalane.runtime.redis_atomic import increment_with_expiry
from escalane.web.ack_presentation import render_ack_page
from escalane.web.deps import (
    get_app_settings,
    get_client_ip,
    get_redis,
    get_session,
    is_secure_request,
)
from escalane.web.i18n import SUPPORTED_LOCALES, normalise_locale
from escalane.web.schemas import AckIn

router = APIRouter()
logger = logging.getLogger("escalane")

_CSRF_COOKIE_NAME = "csrf_token"

_ACK_RATE_MAX = 10
_ACK_RATE_WINDOW = 60  # seconds


def _locale(request: Request, explicit: str | None) -> str:
    """Resolve ACK-page language from an explicit choice, cookie, then browser header."""
    if explicit in SUPPORTED_LOCALES:
        return explicit
    persisted = request.cookies.get("ui_locale")
    if persisted in SUPPORTED_LOCALES:
        return persisted
    return normalise_locale(request.headers.get("accept-language"))


def _ack_rate_limit_key(client_ip: str | None) -> str:
    """Bucket ACK form traffic by client IP and minute."""
    return rate_limit_key(f"ack:{client_ip or 'unknown'}", minute_bucket())


async def _check_ack_rate_limit(
    request: Request,
    redis,
    settings: Settings | None = None,
) -> None:
    """Enforce per-IP rate limit on ACK endpoints (10 req/min)."""
    client_ip = get_client_ip(request, settings)
    rl_key = _ack_rate_limit_key(client_ip)
    rl_val = await increment_with_expiry(redis, rl_key, _ACK_RATE_WINDOW + 10)
    if rl_val > _ACK_RATE_MAX:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"Too many requests. Limit: {_ACK_RATE_MAX} per {_ACK_RATE_WINDOW}s. "
                "Please wait and try again."
            ),
        )


def _validate_csrf(csrf_token: str | None, form_csrf: str) -> None:
    """Require the page-specific CSRF secret before a capability-link transition."""
    if csrf_token and form_csrf and secrets.compare_digest(csrf_token, form_csrf):
        return
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Security validation failed. Please reload the page and try again.",
    )


def _parse_ack_payload(form) -> AckIn:
    """Normalize form fields and surface schema errors as an HTTP validation response."""
    raw_acked_by = form.get("acked_by")
    raw_note = form.get("note")
    acked_by = (str(raw_acked_by).strip() if raw_acked_by else None) or None
    note = (str(raw_note).strip() if raw_note else None) or None
    try:
        return AckIn(acked_by=acked_by, note=note)
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=exc.errors(),
        ) from exc


async def _invalid_ack_response(
    session: AsyncSession,
    form,
    alarm,
    ack_token: str,
    locale: str,
    status_code: int,
) -> HTMLResponse:
    """Re-render invalid ACK input without exposing the acknowledgement capability elsewhere."""
    enriched = await enrich_alarm_context(session, alarm)
    message = (
        "Name or note is too long. Check the fields and try again."
        if locale == "en"
        else "Name oder Notiz ist zu lang. Prüfen Sie die Felder und versuchen Sie es erneut."
    )
    return HTMLResponse(
        render_ack_page(
            alarm,
            enriched,
            ack_action=f"/a/{ack_token}?lang={locale}",
            locale=locale,
            csrf_token=str(form.get("csrf_token", "")),
            error=message,
            values={
                "acked_by": str(form.get("acked_by", ""))[:120],
                "note": str(form.get("note", ""))[:2000],
            },
        ),
        status_code=status_code,
    )


async def _apply_acknowledgement(
    session: AsyncSession,
    redis,
    alarm: Alarm,
    payload: AckIn,
) -> None:
    """Apply the ACK transition and record delayed event delivery."""
    outcome = await apply_alarm_state_change(
        session,
        redis,
        alarm,
        target_status=AlarmStatus.ACKNOWLEDGED,
        actor=payload.acked_by,
        note=payload.note,
        logger=logger,
    )
    if outcome.pending:
        logger.warning(
            "ack_event_delivery_pending",
            extra={"alarm_id": str(alarm.id), "published": outcome.published},
        )


@router.get("/a/{ack_token}", response_class=HTMLResponse)
async def ack_page(
    request: Request,
    ack_token: str,
    lang: str | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    """Render the acknowledgement page and issue a one-hour CSRF cookie."""
    settings = get_app_settings(request)
    redis = get_redis(request)
    await _check_ack_rate_limit(request, redis, settings)
    alarm = await get_alarm_by_ack_token(session, ack_token)
    if not alarm:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Unknown or expired acknowledgement token. The link may be invalid.",
        )

    enriched = await enrich_alarm_context(session, alarm)
    csrf_token = secrets.token_hex(32)
    selected_locale = _locale(request, lang)
    html = render_ack_page(
        alarm,
        enriched,
        ack_action=f"/a/{ack_token}?lang={selected_locale}",
        locale=selected_locale,
        csrf_token=csrf_token,
    )
    response = HTMLResponse(html)
    response.set_cookie(
        key=_CSRF_COOKIE_NAME,
        value=csrf_token,
        httponly=True,
        secure=is_secure_request(request, settings),
        samesite="strict",
        max_age=3600,
    )
    if lang in SUPPORTED_LOCALES:
        response.set_cookie("ui_locale", selected_locale, max_age=31_536_000, samesite="lax")
    return response


@router.post("/a/{ack_token}", response_class=HTMLResponse)
async def ack_submit(
    request: Request,
    ack_token: str,
    lang: str | None = Query(default=None),
    csrf_token: str | None = Cookie(default=None),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    """Validate the ACK form, transition the alarm, and enqueue follow-up events."""
    settings = get_app_settings(request)
    redis = get_redis(request)
    await _check_ack_rate_limit(request, redis, settings)
    alarm = await get_alarm_by_ack_token(session, ack_token)
    if not alarm:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Unknown or expired acknowledgement token. The link may be invalid.",
        )

    form = await request.form()
    _validate_csrf(csrf_token, str(form.get("csrf_token", "")))
    selected_locale = _locale(request, lang)
    try:
        payload = _parse_ack_payload(form)
    except HTTPException as exc:
        return await _invalid_ack_response(
            session,
            form,
            alarm,
            ack_token,
            selected_locale,
            exc.status_code,
        )

    await _apply_acknowledgement(session, redis, alarm, payload)
    request.state.alarm_id = str(alarm.id)

    enriched = await enrich_alarm_context(session, alarm)
    return HTMLResponse(
        render_ack_page(
            alarm,
            enriched,
            ack_action=f"/a/{ack_token}?lang={selected_locale}",
            locale=selected_locale,
        )
    )
