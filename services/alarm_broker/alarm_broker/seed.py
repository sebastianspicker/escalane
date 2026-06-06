from __future__ import annotations

import os
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from alarm_broker.db.models import (
    Device,
    EscalationPolicy,
    EscalationStep,
    EscalationTarget,
    Person,
    Room,
    Site,
)
from alarm_broker.settings import Settings

_ENV_PATTERN = re.compile(r"^\$\{([A-Z0-9_]+)\}$")
_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off"}


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in _TRUE_VALUES:
            return True
        if lowered in _FALSE_VALUES:
            return False
    return bool(value)


def _settings_env_fallback(key: str, settings: Settings) -> str | None:
    settings_key = key.lower()
    value = str(getattr(settings, settings_key, ""))
    return value or None


def _coerce_env_scalar(value: str) -> Any:
    lowered = value.strip().lower()
    if lowered in _TRUE_VALUES:
        return True
    if lowered in _FALSE_VALUES:
        return False
    if value.isdigit():
        return int(value)
    return value


def _expand_env_string(value: str, settings: Settings) -> Any:
    match = _ENV_PATTERN.match(value.strip())
    if not match:
        return value
    key = match.group(1)
    env_val = os.getenv(key) or _settings_env_fallback(key, settings)
    if env_val is None:
        return None
    return _coerce_env_scalar(env_val)


def _expand_env_list(values: list[Any], settings: Settings) -> list[Any]:
    return [_expand_env(value, settings) for value in values]


def _expand_env_dict(values: dict[str, Any], settings: Settings) -> dict[str, Any]:
    return {key: _expand_env(value, settings) for key, value in values.items()}


def _expand_env(value: Any, settings: Settings) -> Any:
    if isinstance(value, str):
        return _expand_env_string(value, settings)
    if isinstance(value, list):
        return _expand_env_list(value, settings)
    if isinstance(value, dict):
        return _expand_env_dict(value, settings)
    return value


async def _upsert_sites(session: AsyncSession, sites: list[dict[str, Any]]) -> None:
    for site_data in sites:
        site = await session.get(Site, site_data["id"])
        if not site:
            session.add(Site(id=site_data["id"], name=site_data["name"]))
        else:
            site.name = site_data["name"]


async def _upsert_rooms(session: AsyncSession, rooms: list[dict[str, Any]]) -> None:
    for room_data in rooms:
        room = await session.get(Room, room_data["id"])
        if not room:
            session.add(
                Room(
                    id=room_data["id"],
                    site_id=room_data["site_id"],
                    label=room_data["label"],
                    floor=room_data.get("floor"),
                    notes=room_data.get("notes"),
                )
            )
        else:
            room.site_id = room_data["site_id"]
            room.label = room_data["label"]
            room.floor = room_data.get("floor")
            room.notes = room_data.get("notes")


async def _upsert_persons(session: AsyncSession, persons: list[dict[str, Any]]) -> None:
    for person_data in persons:
        person = await session.get(Person, person_data["id"])
        if not person:
            session.add(
                Person(
                    id=person_data["id"],
                    display_name=person_data["display_name"],
                    role=person_data.get("role"),
                    phone_mobile=person_data.get("phone_mobile"),
                    phone_ext=person_data.get("phone_ext"),
                    active=_coerce_bool(person_data.get("active", True)),
                )
            )
        else:
            person.display_name = person_data["display_name"]
            person.role = person_data.get("role")
            person.phone_mobile = person_data.get("phone_mobile")
            person.phone_ext = person_data.get("phone_ext")
            person.active = _coerce_bool(person_data.get("active", True))


async def _upsert_devices(session: AsyncSession, devices: list[dict[str, Any]]) -> None:
    for device_data in devices:
        device = await session.scalar(
            select(Device).where(Device.device_token == device_data["device_token"])
        )
        if not device:
            session.add(
                Device(
                    id=device_data["id"],
                    vendor=device_data.get("vendor", "yealink"),
                    model_family=device_data.get("model_family", "T5"),
                    mac=device_data.get("mac"),
                    account_ext=device_data.get("account_ext"),
                    device_token=device_data["device_token"],
                    person_id=device_data.get("person_id"),
                    room_id=device_data.get("room_id"),
                )
            )
        else:
            device.id = device_data.get("id", device.id)
            device.vendor = device_data.get("vendor", device.vendor)
            device.model_family = device_data.get("model_family", device.model_family)
            device.mac = device_data.get("mac")
            device.account_ext = device_data.get("account_ext")
            device.person_id = device_data.get("person_id")
            device.room_id = device_data.get("room_id")


async def _upsert_policy(session: AsyncSession, policy: dict[str, Any] | None) -> None:
    if policy:
        esc_policy = await session.get(EscalationPolicy, policy.get("id", "default"))
        if not esc_policy:
            session.add(
                EscalationPolicy(id=policy.get("id", "default"), name=policy.get("name", "Default"))
            )
        else:
            esc_policy.name = policy.get("name", esc_policy.name)


async def _upsert_targets(session: AsyncSession, targets: list[dict[str, Any]]) -> None:
    for target_data in targets:
        target = await session.get(EscalationTarget, target_data["id"])
        if not target:
            session.add(
                EscalationTarget(
                    id=target_data["id"],
                    label=target_data["label"],
                    channel=target_data["channel"],
                    address=target_data["address"],
                    enabled=_coerce_bool(target_data.get("enabled", True)),
                )
            )
        else:
            target.label = target_data["label"]
            target.channel = target_data["channel"]
            target.address = target_data["address"]
            target.enabled = _coerce_bool(target_data.get("enabled", True))


async def _replace_escalation_steps(session: AsyncSession, steps: list[dict[str, Any]]) -> None:
    # Replace steps for policies included in the seed
    policy_ids = sorted({step["policy_id"] for step in steps})
    for policy_id in policy_ids:
        existing = await session.scalars(
            select(EscalationStep).where(EscalationStep.policy_id == policy_id)
        )
        for row in existing:
            await session.delete(row)

    for step in steps:
        for target_id in step.get("target_ids") or []:
            session.add(
                EscalationStep(
                    policy_id=step["policy_id"],
                    step_no=int(step["step_no"]),
                    after_seconds=int(step["after_seconds"]),
                    target_id=target_id,
                )
            )


def _seed_records(data: dict[str, Any], key: str) -> list[dict[str, Any]]:
    return list(data.get(key) or [])


async def _replace_seed_steps(session: AsyncSession, data: dict[str, Any]) -> None:
    steps = _seed_records(data, "escalation_steps")
    if steps:
        await _replace_escalation_steps(session, steps)


async def apply_seed(session: AsyncSession, raw: dict[str, Any], settings: Settings) -> None:
    data = _expand_env(raw or {}, settings)

    await _upsert_sites(session, _seed_records(data, "sites"))
    await _upsert_rooms(session, _seed_records(data, "rooms"))
    await _upsert_persons(session, _seed_records(data, "persons"))
    await _upsert_devices(session, _seed_records(data, "devices"))
    await _upsert_policy(session, data.get("escalation_policy"))
    await _upsert_targets(session, _seed_records(data, "escalation_targets"))
    await _replace_seed_steps(session, data)

    await session.commit()
