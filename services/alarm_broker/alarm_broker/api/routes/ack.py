from __future__ import annotations

import logging
import secrets

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from alarm_broker.api.deps import (
    get_app_settings,
    get_client_ip,
    get_redis,
    get_session,
    is_secure_request,
)
from alarm_broker.api.schemas import AckIn
from alarm_broker.core.rate_limit import minute_bucket, rate_limit_key
from alarm_broker.services.ack_ui import render_ack_page
from alarm_broker.services.alarm_service import acknowledge_alarm, get_alarm_by_ack_token
from alarm_broker.services.enrichment_service import enrich_alarm_context
from alarm_broker.services.event_service import (
    enqueue_alarm_acked_event,
    enqueue_alarm_state_changed_event,
)
from alarm_broker.settings import Settings

router = APIRouter()
logger = logging.getLogger("alarm_broker")

_CSRF_COOKIE_NAME = "csrf_token"

_ACK_RATE_MAX = 10
_ACK_RATE_WINDOW = 60  # seconds


def _ack_rate_limit_key(client_ip: str) -> str:
    return rate_limit_key(f"ack:{client_ip}", minute_bucket())


async def _check_ack_rate_limit(
    request: Request,
    redis,
    settings: Settings | None = None,
) -> None:
    """Enforce per-IP rate limit on ACK endpoints (10 req/min)."""
    client_ip = get_client_ip(request, settings)
    rl_key = _ack_rate_limit_key(client_ip)
    rl_val = await redis.incr(rl_key)
    if rl_val == 1:
        await redis.expire(rl_key, _ACK_RATE_WINDOW + 10)
    if rl_val > _ACK_RATE_MAX:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"Too many requests. Limit: {_ACK_RATE_MAX} per {_ACK_RATE_WINDOW}s. "
                "Please wait and try again."
            ),
        )


@router.get("/a/{ack_token}", response_class=HTMLResponse)
async def ack_page(
    request: Request,
    ack_token: str,
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
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
    html = render_ack_page(alarm, enriched, csrf_token=csrf_token)
    response = HTMLResponse(html)
    response.set_cookie(
        key=_CSRF_COOKIE_NAME,
        value=csrf_token,
        httponly=True,
        secure=is_secure_request(request, settings),
        samesite="strict",
        max_age=3600,
    )
    return response


@router.post("/a/{ack_token}", response_class=HTMLResponse)
async def ack_submit(
    request: Request,
    ack_token: str,
    csrf_token: str | None = Cookie(default=None),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
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

    # CSRF validation: compare cookie value with hidden form field
    form_csrf = str(form.get("csrf_token", ""))
    if not csrf_token or not form_csrf or not secrets.compare_digest(csrf_token, form_csrf):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Security validation failed. Please reload the page and try again.",
        )
    raw_acked_by = form.get("acked_by")
    raw_note = form.get("note")
    acked_by = (str(raw_acked_by).strip() if raw_acked_by else None) or None
    note = (str(raw_note).strip() if raw_note else None) or None
    try:
        payload = AckIn(acked_by=acked_by, note=note)
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=exc.errors(),
        ) from exc

    changed = await acknowledge_alarm(
        session,
        alarm,
        acked_by=payload.acked_by,
        note=payload.note,
    )
    if changed:
        request.state.alarm_id = str(alarm.id)
        ack_result = await enqueue_alarm_acked_event(
            redis,
            alarm_id=alarm.id,
            acked_by=payload.acked_by,
            note=payload.note,
            logger=logger,
        )
        if not ack_result.success:
            logger.warning(
                "ack_event_enqueue_failed",
                extra={"alarm_id": str(alarm.id), "error": ack_result.error},
            )
        state_result = await enqueue_alarm_state_changed_event(
            redis,
            alarm_id=alarm.id,
            state=alarm.status.value,
            logger=logger,
        )
        if not state_result.success:
            logger.warning(
                "state_event_enqueue_failed",
                extra={"alarm_id": str(alarm.id), "error": state_result.error},
            )

    enriched = await enrich_alarm_context(session, alarm)
    return HTMLResponse(render_ack_page(alarm, enriched))
