"""Simulation mode API routes (enabled only when SIMULATION_ENABLED=true)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from escalane.config.settings import Settings
from escalane.providers.mock import MockNotification, get_mock_store
from escalane.web.deps import get_app_settings, require_admin

router = APIRouter(prefix="/v1/simulation", tags=["simulation"])


_SIMULATION_SEED_FILENAME = "simulation_seed.yaml"
_VALID_CHANNELS = frozenset({"zammad", "sms", "signal"})


def _ensure_simulation_enabled(settings: Settings) -> None:
    """Fail closed by returning 404 when simulation mode is disabled."""
    if not settings.simulation_enabled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Simulation endpoint not found",
        )


def _serialize_notifications(notifications: list[MockNotification]) -> list[dict[str, Any]]:
    """Convert in-memory mock delivery records into a stable API response shape."""
    return [
        {
            "id": item.id,
            "channel": item.channel,
            "timestamp": item.timestamp.isoformat(),
            "payload": item.payload,
            "result": item.result,
            "error": item.error,
        }
        for item in notifications
    ]


@router.get("/notifications", dependencies=[Depends(require_admin)])
async def get_simulation_notifications(
    channel: str | None = Query(default=None),
    settings: Settings = Depends(get_app_settings),
) -> dict[str, Any]:
    """Get all notifications sent during simulation mode.

    This endpoint is only available in simulation mode. It returns all
    notifications that were stored by mock connectors.

    Args:
        channel: Optional filter by channel (zammad, sms, signal)

    Returns:
        Dictionary containing simulation status and notifications

    Raises:
        HTTPException: If simulation mode is not enabled
    """
    _ensure_simulation_enabled(settings)

    store = get_mock_store()
    if channel and channel not in _VALID_CHANNELS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid channel. Must be one of: {sorted(_VALID_CHANNELS)}",
        )

    notifications = store.get_by_channel(channel) if channel else store.get_all()

    return {
        "simulation_enabled": True,
        "channel_filter": channel,
        "total": len(notifications),
        "notifications": _serialize_notifications(notifications),
    }


@router.post("/notifications/clear", dependencies=[Depends(require_admin)])
async def clear_simulation_notifications(
    response: Response,
    settings: Settings = Depends(get_app_settings),
) -> dict[str, str]:
    """Clear all stored simulation notifications.

    This endpoint is only available in simulation mode. It clears
    all notifications stored by mock connectors.

    Returns:
        Success message

    Raises:
        HTTPException: If simulation mode is not enabled
    """
    _ensure_simulation_enabled(settings)

    store = get_mock_store()
    store.clear()

    response.status_code = status.HTTP_200_OK
    return {"status": "ok", "message": "All simulation notifications cleared"}


@router.get("/status", dependencies=[Depends(require_admin)])
async def get_simulation_status(
    settings: Settings = Depends(get_app_settings),
) -> dict[str, Any]:
    """Get current simulation mode status.

    This endpoint returns whether simulation mode is enabled
    and the current state of the mock notification store.

    Returns:
        Dictionary containing simulation status
    """
    _ensure_simulation_enabled(settings)

    store = get_mock_store()
    notifications = store.get_all()

    return {
        "simulation_enabled": True,
        "total_notifications": len(notifications),
        "by_channel": {
            "zammad": len(store.get_by_channel("zammad")),
            "sms": len(store.get_by_channel("sms")),
            "signal": len(store.get_by_channel("signal")),
        },
    }


@router.post("/seed", dependencies=[Depends(require_admin)])
async def get_simulation_seed_metadata(
    settings: Settings = Depends(get_app_settings),
) -> dict[str, str]:
    """Return metadata for the repository's simulation seed workflow.

    The caller still submits the seed document to the administrative seed
    endpoint. This route does not read a repository or installation path.

    Returns:
        Success message with the logical seed filename

    Raises:
        HTTPException: If simulation mode is not enabled
    """
    _ensure_simulation_enabled(settings)

    return {
        "status": "ok",
        "message": "Load simulation seed via POST /v1/admin/seed",
        "seed_file": _SIMULATION_SEED_FILENAME,
        "admin_seed_endpoint": "/v1/admin/seed",
    }
