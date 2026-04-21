"""Simulation mode API routes (enabled only when SIMULATION_ENABLED=true)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status

from alarm_broker.api.deps import get_app_settings, require_admin
from alarm_broker.connectors.mock import MockNotification, get_mock_store
from alarm_broker.settings import Settings

router = APIRouter(prefix="/v1/simulation", tags=["simulation"])


# Bundled simulation seed data path – walk up from this file to find the
# repository-level "deploy" directory instead of relying on a hard-coded
# parent depth which breaks when the file is relocated.
def _find_deploy_dir() -> Path:
    current = Path(__file__).resolve().parent
    while current != current.parent:
        candidate = current / "deploy"
        if candidate.is_dir():
            return candidate
        current = current.parent
    # Fallback: return the old hard-coded location so the caller can
    # produce a clear "file not found" error instead of a crash.
    return Path(__file__).resolve().parent / "deploy"


_SIMULATION_SEED_PATH = _find_deploy_dir() / "simulation_seed.yaml"
_VALID_CHANNELS = frozenset({"zammad", "sms", "signal"})


def _ensure_simulation_enabled(settings: Settings) -> None:
    """Fail closed by returning 404 when simulation mode is disabled."""
    if not settings.simulation_enabled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Simulation endpoint not found",
        )


def _serialize_notifications(notifications: list[MockNotification]) -> list[dict[str, Any]]:
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
async def load_simulation_seed(
    settings: Settings = Depends(get_app_settings),
) -> dict[str, str]:
    """Load bundled simulation seed data.

    This endpoint loads the bundled demo seed data from simulation_seed.yaml.
    It requires simulation mode to be enabled.

    Returns:
        Success message with path to seed data

    Raises:
        HTTPException: If simulation mode is not enabled
    """
    _ensure_simulation_enabled(settings)

    if not _SIMULATION_SEED_PATH.exists():
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Simulation seed file not found",
        )

    # Return the path - actual loading should be done via /v1/admin/seed
    return {
        "status": "ok",
        "message": "Load simulation seed via POST /v1/admin/seed",
        "seed_file": str(_SIMULATION_SEED_PATH),
        "admin_seed_endpoint": "/v1/admin/seed",
    }
