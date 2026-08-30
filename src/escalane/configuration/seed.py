"""Reconcile YAML seed data into versioned master-data and policy tables."""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from escalane.config.errors import ValidationError
from escalane.config.settings import Settings
from escalane.configuration.master_data import lock_active_referenced_parents
from escalane.persistence.models import (
    Device,
    EscalationPolicy,
    EscalationStep,
    EscalationTarget,
    Person,
    Room,
    Site,
)

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


def _placeholder_name(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    match = _ENV_PATTERN.match(value.strip())
    return match.group(1) if match else None


def _resolve_placeholder(value: Any, *, allowed_name: str, resolved: Any) -> Any:
    name = _placeholder_name(value)
    if name is None:
        return value
    if name != allowed_name or resolved in (None, ""):
        raise ValidationError("Seed contains an unknown or unresolved placeholder")
    return resolved


def _reject_unplaced_placeholders(value: Any) -> None:
    if isinstance(value, str) and _placeholder_name(value) is not None:
        raise ValidationError("Seed placeholder is not allowed at this field")
    if isinstance(value, list):
        for item in value:
            _reject_unplaced_placeholders(item)
    elif isinstance(value, dict):
        for item in value.values():
            _reject_unplaced_placeholders(item)


def _copy_seed_records(value: Any) -> list[dict[str, Any]]:
    return [dict(item) for item in value or []]


def _expand_device_tokens(devices: list[dict[str, Any]], settings: Settings) -> None:
    for device in devices:
        device["device_token"] = _resolve_placeholder(
            device.get("device_token"),
            allowed_name="YEALINK_DEVICE_TOKEN",
            resolved=settings.yealink_device_token,
        )


def _expand_signal_target_addresses(targets: list[dict[str, Any]], settings: Settings) -> None:
    for target in targets:
        address = target.get("address")
        if _placeholder_name(address) is not None and target.get("channel") != "signal":
            raise ValidationError("SIGNAL_TARGET_GROUP_ID is only allowed for Signal targets")
        target["address"] = _resolve_placeholder(
            address,
            allowed_name="SIGNAL_TARGET_GROUP_ID",
            resolved=settings.signal_target_group_id,
        )


def _step_placeholder_values(settings: Settings) -> dict[str, int]:
    return {
        "ESCALATE_T1": settings.escalate_t1,
        "ESCALATE_T2": settings.escalate_t2,
        "ESCALATE_T3": settings.escalate_t3,
    }


def _expand_step_delays(steps: list[dict[str, Any]], step_values: Mapping[str, int]) -> None:
    for step in steps:
        name = _placeholder_name(step.get("after_seconds"))
        if name is not None:
            if name not in step_values:
                raise ValidationError("Unknown escalation-step placeholder")
            step["after_seconds"] = step_values[name]


def _expand_seed_records[ExpansionValue](
    data: dict[str, Any],
    key: str,
    expand: Callable[[list[dict[str, Any]], ExpansionValue], None],
    expansion_value: ExpansionValue,
) -> None:
    records = _copy_seed_records(data.get(key))
    expand(records, expansion_value)
    data[key] = records


def _expand_env(raw: dict[str, Any], settings: Settings) -> dict[str, Any]:
    """Resolve only the documented, field-bound seed placeholders."""
    data = dict(raw)
    if "devices" in data:
        _expand_seed_records(data, "devices", _expand_device_tokens, settings)

    if "escalation_targets" in data:
        _expand_seed_records(data, "escalation_targets", _expand_signal_target_addresses, settings)

    step_values = _step_placeholder_values(settings)
    if "escalation_steps" in data:
        _expand_seed_records(data, "escalation_steps", _expand_step_delays, step_values)

    _reject_unplaced_placeholders(data)
    return data


async def _upsert_sites(session: AsyncSession, sites: list[dict[str, Any]]) -> None:
    for site_data in sites:
        site = await session.scalar(
            select(Site).where(Site.id == site_data["id"]).with_for_update()
        )
        if not site:
            session.add(
                Site(
                    id=site_data["id"],
                    name=site_data["name"],
                    active=_coerce_bool(site_data.get("active", True)),
                )
            )
        else:
            site.name = site_data["name"]
            site.active = _coerce_bool(site_data.get("active", True))
            site.version += 1


async def _upsert_rooms(session: AsyncSession, rooms: list[dict[str, Any]]) -> None:
    for room_data in rooms:
        active = _coerce_bool(room_data.get("active", True))
        await lock_active_referenced_parents(
            session,
            resource_name="rooms",
            values={"site_id": room_data["site_id"], "active": active},
        )
        room = await session.scalar(
            select(Room).where(Room.id == room_data["id"]).with_for_update()
        )
        if not room:
            session.add(
                Room(
                    id=room_data["id"],
                    site_id=room_data["site_id"],
                    label=room_data["label"],
                    floor=room_data.get("floor"),
                    notes=room_data.get("notes"),
                    active=active,
                )
            )
        else:
            room.site_id = room_data["site_id"]
            room.label = room_data["label"]
            room.floor = room_data.get("floor")
            room.notes = room_data.get("notes")
            room.active = active
            room.version += 1


async def _upsert_persons(session: AsyncSession, persons: list[dict[str, Any]]) -> None:
    for person_data in persons:
        person = await session.scalar(
            select(Person).where(Person.id == person_data["id"]).with_for_update()
        )
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
            person.version += 1


async def _upsert_devices(session: AsyncSession, devices: list[dict[str, Any]]) -> None:
    for device_data in devices:
        active = _coerce_bool(device_data.get("active", True))
        await lock_active_referenced_parents(
            session,
            resource_name="devices",
            values={
                "person_id": device_data.get("person_id"),
                "room_id": device_data.get("room_id"),
                "active": active,
            },
        )
        device = await session.scalar(
            select(Device)
            .where(Device.device_token == device_data["device_token"])
            .with_for_update()
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
                    active=active,
                )
            )
        else:
            device.vendor = device_data.get("vendor", device.vendor)
            device.model_family = device_data.get("model_family", device.model_family)
            device.mac = device_data.get("mac")
            device.account_ext = device_data.get("account_ext")
            device.person_id = device_data.get("person_id")
            device.room_id = device_data.get("room_id")
            device.active = active
            device.version += 1


async def _upsert_policy(session: AsyncSession, policy: dict[str, Any] | None) -> None:
    if policy:
        policy_id = policy.get("id", "default")
        esc_policy = await session.scalar(
            select(EscalationPolicy).where(EscalationPolicy.id == policy_id).with_for_update()
        )
        if not esc_policy:
            session.add(EscalationPolicy(id=policy_id, name=policy.get("name", "Default")))
        else:
            esc_policy.name = policy.get("name", esc_policy.name)
            esc_policy.version += 1


async def _upsert_targets(session: AsyncSession, targets: list[dict[str, Any]]) -> None:
    for target_data in targets:
        target = await session.scalar(
            select(EscalationTarget)
            .where(EscalationTarget.id == target_data["id"])
            .with_for_update()
        )
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


def _normalise_seed_step(step: dict[str, Any]) -> tuple[str, int, int, list[str]]:
    policy_id = str(step["policy_id"])
    step_no = int(step["step_no"])
    after_seconds = int(step["after_seconds"])
    target_ids = [str(target_id) for target_id in step.get("target_ids") or []]
    if not target_ids:
        raise ValidationError(
            f"Escalation policy {policy_id} step {step_no} must reference a target"
        )
    if step_no < 0 or after_seconds < 0:
        raise ValidationError("Escalation step numbers and delays must be non-negative")
    return policy_id, step_no, after_seconds, target_ids


def _validated_seed_steps(
    steps: list[dict[str, Any]],
) -> list[tuple[str, int, int, list[str]]]:
    delays_by_step: dict[tuple[str, int], int] = {}
    normalised: list[tuple[str, int, int, list[str]]] = []
    for step in steps:
        values = _normalise_seed_step(step)
        policy_id, step_no, after_seconds, _target_ids = values
        schedule_key = (policy_id, step_no)
        previous_delay = delays_by_step.setdefault(schedule_key, after_seconds)
        if previous_delay != after_seconds:
            raise ValidationError(
                f"Conflicting after_seconds values for policy {policy_id} step {step_no}"
            )
        normalised.append(values)
    return normalised


async def _replace_escalation_steps(session: AsyncSession, steps: list[dict[str, Any]]) -> None:
    normalised = _validated_seed_steps(steps)

    # Replace steps for policies included in the seed
    policy_ids = sorted({policy_id for policy_id, _step, _delay, _targets in normalised})
    for policy_id in policy_ids:
        await session.scalar(
            select(EscalationPolicy.id).where(EscalationPolicy.id == policy_id).with_for_update()
        )
        existing = await session.scalars(
            select(EscalationStep).where(EscalationStep.policy_id == policy_id).with_for_update()
        )
        for row in existing:
            await session.delete(row)

    for policy_id, step_no, after_seconds, target_ids in normalised:
        for target_id in target_ids:
            session.add(
                EscalationStep(
                    policy_id=policy_id,
                    step_no=step_no,
                    after_seconds=after_seconds,
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
    """Expand configured placeholders and atomically reconcile the supplied seed."""
    data = _expand_env(raw or {}, settings)

    await _upsert_sites(session, _seed_records(data, "sites"))
    await _upsert_persons(session, _seed_records(data, "persons"))
    await _upsert_rooms(session, _seed_records(data, "rooms"))
    await _upsert_devices(session, _seed_records(data, "devices"))
    await _upsert_policy(session, data.get("escalation_policy"))
    await _upsert_targets(session, _seed_records(data, "escalation_targets"))
    await _replace_seed_steps(session, data)

    await session.commit()
