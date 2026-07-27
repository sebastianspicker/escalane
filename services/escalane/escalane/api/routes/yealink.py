"""Yealink alarm trigger routes."""

from __future__ import annotations

import logging
from typing import NoReturn

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from escalane.api.deps import get_app_settings, get_client_ip, get_redis, get_session
from escalane.api.schemas import TriggerResponse
from escalane.core.ip_allowlist import ip_allowed
from escalane.core.rate_limit import minute_bucket
from escalane.services.trigger_service import TriggerService
from escalane.settings import Settings

router = APIRouter()
logger = logging.getLogger("escalane")


def _reject_source_ip(request: Request, client_ip: str | None) -> NoReturn:
    """Log and reject a source that cannot pass the production allowlist."""
    logger.warning(
        "ip_not_allowed" if client_ip else "client_ip_unavailable",
        extra={"client_ip": client_ip, "path": request.url.path},
    )
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="IP not allowed. Check YELK_IP_ALLOWLIST in server configuration.",
    )


def _validate_source_ip(request: Request, settings: Settings) -> str:
    """Enforce the Yealink source allowlist while preserving explicit simulation diagnostics."""
    client_ip = get_client_ip(request, settings)
    if settings.simulation_enabled:
        simulation_ip = client_ip or "unknown"
        if (
            client_ip
            and settings.yelk_ip_allowlist
            and not ip_allowed(client_ip, settings.yelk_ip_allowlist)
        ):
            logger.warning(
                "ip_not_allowed_simulation",
                extra={"client_ip": simulation_ip, "path": request.url.path},
            )
        return simulation_ip

    if client_ip and ip_allowed(client_ip, settings.yelk_ip_allowlist):
        return client_ip
    _reject_source_ip(request, client_ip)


def _trigger_token(request: Request, settings: Settings) -> str:
    """Extract the configurable device token query parameter or fail before processing."""
    token = request.query_params.get(settings.yelk_token_query_param)
    if token:
        return token
    raise HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=f"Missing '{settings.yelk_token_query_param}' query parameter in trigger URL.",
    )


def _raise_trigger_failure(result) -> None:
    """Translate an unsuccessful domain result into the HTTP status chosen by the service."""
    if result.success:
        return
    raise HTTPException(
        status_code=result.error_code or 500,
        detail=(
            result.error_message or "Trigger processing failed. Check server logs for details."
        ),
    )


def _trigger_response(result) -> TriggerResponse:
    """Require a complete successful trigger result before constructing the public response."""
    if result.alarm_id is None or result.status is None:
        raise HTTPException(
            status_code=500,
            detail=(
                "Internal error: alarm was created but response data is incomplete. "
                "Check server logs."
            ),
        )
    return TriggerResponse(alarm_id=result.alarm_id, status=result.status)


@router.get("/v1/yealink/alarm", response_model=TriggerResponse)
async def yealink_alarm(
    request: Request,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_app_settings),
) -> TriggerResponse:
    """Handle Yealink alarm trigger."""
    client_ip = _validate_source_ip(request, settings)
    token = _trigger_token(request, settings)
    redis = get_redis(request)
    rate_bucket = None if settings.simulation_enabled else minute_bucket()
    trigger = TriggerService(
        session,
        redis,
        settings,
        rate_limit_bucket=rate_bucket,
    )
    result = await trigger.process_trigger(
        token=token,
        client_ip=client_ip,
        user_agent=request.headers.get("user-agent", ""),
        event=request.query_params.get("event"),
        request_id=getattr(request.state, "request_id", None),
    )

    _raise_trigger_failure(result)
    response = _trigger_response(result)
    request.state.alarm_id = str(result.alarm_id)
    return response
