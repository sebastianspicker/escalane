from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from alarm_broker.api.deps import get_app_settings, get_session, require_admin
from alarm_broker.api.schemas import DeviceUpsertIn, EscalationPolicyIn
from alarm_broker.db.models import Device
from alarm_broker.services.policy_service import apply_escalation_policy
from alarm_broker.services.seed_service import (
    _MAX_SEED_BYTES,
    apply_seed_payload,
    parse_seed_payload,
)
from alarm_broker.settings import Settings

router = APIRouter(prefix="/v1/admin", dependencies=[Depends(require_admin)])


@router.post("/devices", status_code=status.HTTP_201_CREATED)
async def admin_create_device(
    body: DeviceUpsertIn,
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    """Create or update a device. POST is used as it performs upsert."""
    device = await session.scalar(select(Device).where(Device.device_token == body.device_token))
    if not device:
        device = Device(
            id=body.id or f"device:{body.device_token}",
            vendor=body.vendor,
            model_family=body.model_family,
            mac=body.mac,
            account_ext=body.account_ext,
            device_token=body.device_token,
            person_id=body.person_id,
            room_id=body.room_id,
            last_seen_at=None,
        )
        session.add(device)
    else:
        device.vendor = body.vendor
        device.model_family = body.model_family
        device.mac = body.mac
        device.account_ext = body.account_ext
        device.person_id = body.person_id
        device.room_id = body.room_id

    await session.commit()
    return {"ok": "true", "device_id": device.id}


@router.post("/escalation-policy", status_code=status.HTTP_201_CREATED)
async def admin_set_escalation_policy(
    body: EscalationPolicyIn,
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    """Create or replace the escalation policy."""
    policy_id = await apply_escalation_policy(session, body)
    return {"ok": "true", "policy_id": policy_id}


@router.post("/seed")
async def admin_seed(
    request: Request,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_app_settings),
) -> dict[str, str]:
    """Seed the database with devices, persons, rooms, and sites."""
    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > 10 * 1024 * 1024:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Request body too large. Maximum: 10 MB",
        )
    raw = await request.body()
    if len(raw) > _MAX_SEED_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Seed payload too large ({len(raw)} bytes). Maximum: {_MAX_SEED_BYTES} bytes",
        )
    content_type = request.headers.get("content-type", "application/json").split(";")[0].strip()

    data = parse_seed_payload(content_type, raw)
    await apply_seed_payload(session, data=data, settings=settings)
    return {"ok": "true"}
