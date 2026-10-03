"""Bootstrap device upsert by device token."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from escalane.config.errors import ConflictError
from escalane.configuration.master_data import lock_active_referenced_parents
from escalane.persistence.models import Device


class DeviceUpsertConflictError(ConflictError):
    """Raised when a concurrent or referential change blocks a device upsert."""


@dataclass(frozen=True, slots=True)
class DeviceUpsertCommand:
    """Describe a device upsert keyed by its secret device token."""

    device_token: str
    vendor: str
    model_family: str
    mac: str | None = None
    account_ext: str | None = None
    person_id: str | None = None
    room_id: str | None = None
    id: str | None = None


async def upsert_device(session: AsyncSession, command: DeviceUpsertCommand) -> str:
    """Update the device with this token or insert it, then commit and return its id."""
    values = {
        "vendor": command.vendor,
        "model_family": command.model_family,
        "mac": command.mac,
        "account_ext": command.account_ext,
        "person_id": command.person_id,
        "room_id": command.room_id,
    }
    await lock_active_referenced_parents(
        session,
        resource_name="devices",
        values={**values, "active": True},
    )
    device_id = await session.scalar(
        update(Device)
        .where(Device.device_token == command.device_token)
        .values(**values, version=Device.version + 1)
        .returning(Device.id)
    )
    if device_id is None:
        device_id = command.id or f"device:{uuid.uuid4()}"
        session.add(
            Device(
                id=device_id,
                device_token=command.device_token,
                last_seen_at=None,
                **values,
            )
        )
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise DeviceUpsertConflictError("Device upsert conflicts with existing data") from exc
    return device_id
