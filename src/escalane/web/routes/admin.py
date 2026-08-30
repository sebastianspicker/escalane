"""Privileged JSON endpoints for bootstrap devices, policy, and seed data."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from escalane.config.settings import Settings
from escalane.configuration.importer import (
    _MAX_SEED_BYTES,
    apply_seed_payload,
    parse_seed_payload,
)
from escalane.configuration.master_data import lock_active_referenced_parents
from escalane.configuration.policy import apply_escalation_policy
from escalane.persistence.models import Device
from escalane.web.deps import get_app_settings, get_session, require_admin
from escalane.web.schemas import (
    DeviceUpsertIn,
    EscalationPolicyIn,
    to_escalation_policy_command,
)

router = APIRouter(prefix="/v1/admin", dependencies=[Depends(require_admin)])


def _declared_seed_content_length(content_length: str | None) -> int | None:
    """Reject oversized seed uploads before buffering their request body."""
    if content_length is None:
        return None
    if not content_length.isascii() or not content_length.isdigit():
        raise HTTPException(status_code=400, detail="Invalid Content-Length header")
    normalized_length = content_length.lstrip("0") or "0"
    if len(normalized_length) > len(str(_MAX_SEED_BYTES)):
        declared_length = _MAX_SEED_BYTES + 1
    else:
        declared_length = int(normalized_length)
    if declared_length > _MAX_SEED_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=(
                f"Seed payload too large ({declared_length} bytes). "
                f"Maximum: {_MAX_SEED_BYTES} bytes"
            ),
        )
    return declared_length


@router.post("/devices", status_code=status.HTTP_201_CREATED)
async def admin_create_device(
    body: DeviceUpsertIn,
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    """Create or update a device. POST is used as it performs upsert."""
    values = {
        "vendor": body.vendor,
        "model_family": body.model_family,
        "mac": body.mac,
        "account_ext": body.account_ext,
        "person_id": body.person_id,
        "room_id": body.room_id,
    }
    await lock_active_referenced_parents(
        session,
        resource_name="devices",
        values={**values, "active": True},
    )
    device_id = await session.scalar(
        update(Device)
        .where(Device.device_token == body.device_token)
        .values(**values, version=Device.version + 1)
        .returning(Device.id)
    )
    if device_id is None:
        device_id = body.id or f"device:{uuid.uuid4()}"
        session.add(
            Device(
                id=device_id,
                device_token=body.device_token,
                last_seen_at=None,
                **values,
            )
        )
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status_code=409, detail="device_upsert_conflict") from exc
    return {"ok": "true", "device_id": device_id}


@router.post("/escalation-policy", status_code=status.HTTP_201_CREATED)
async def admin_set_escalation_policy(
    body: EscalationPolicyIn,
    session: AsyncSession = Depends(get_session),
) -> dict[str, str]:
    """Create or replace the escalation policy."""
    policy_id = await apply_escalation_policy(session, to_escalation_policy_command(body))
    return {"ok": "true", "policy_id": policy_id}


@router.post("/seed")
async def admin_seed(
    request: Request,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_app_settings),
) -> dict[str, str]:
    """Seed the database with devices, persons, rooms, and sites."""
    _declared_seed_content_length(request.headers.get("content-length"))
    raw = await request.body()
    if len(raw) > _MAX_SEED_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"Seed payload too large ({len(raw)} bytes). Maximum: {_MAX_SEED_BYTES} bytes",
        )
    content_type = request.headers.get("content-type", "application/json").split(";")[0].strip()

    data = parse_seed_payload(content_type, raw)
    await apply_seed_payload(session, data=data, settings=settings)
    return {"ok": "true"}
