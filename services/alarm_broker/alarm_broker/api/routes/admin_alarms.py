"""Alarm detail, lifecycle, bulk-action, and export console routes."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Cookie, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from alarm_broker.api.admin_session import set_flash
from alarm_broker.api.deps import get_app_settings, get_redis, get_session
from alarm_broker.api.routes.admin_ui import (
    _action_session,
    _html,
    _requested_locale,
    _session_from_request,
)
from alarm_broker.db.models import (
    Alarm,
    AlarmNote,
    AlarmNotification,
    AlarmStatus,
    Person,
    Room,
)
from alarm_broker.services.alarm_service import (
    acknowledge_alarm,
    get_alarm_or_404,
    transition_alarm,
)
from alarm_broker.services.event_service import (
    enqueue_alarm_acked_event,
    enqueue_alarm_state_changed_event,
)
from alarm_broker.settings import Settings

router = APIRouter()
logger = logging.getLogger("alarm_broker")


async def _detail_context(session: AsyncSession, alarm: Alarm, locale: str) -> dict[str, Any]:
    person = await _alarm_person(session, alarm)
    room = await _alarm_room(session, alarm)
    notes, notifications = await _alarm_history(session, alarm.id)
    return {
        "alarm": _alarm_detail_view(alarm, person, room),
        "events": _alarm_timeline(alarm, locale, notes, notifications),
    }


async def _alarm_person(session: AsyncSession, alarm: Alarm) -> Person | None:
    return await session.get(Person, alarm.person_id) if alarm.person_id else None


async def _alarm_room(session: AsyncSession, alarm: Alarm) -> Room | None:
    return await session.get(Room, alarm.room_id) if alarm.room_id else None


async def _alarm_history(
    session: AsyncSession, alarm_id: uuid.UUID
) -> tuple[list[AlarmNote], list[AlarmNotification]]:
    notes = list(
        (
            await session.scalars(
                select(AlarmNote)
                .where(AlarmNote.alarm_id == alarm_id)
                .order_by(AlarmNote.created_at.asc())
            )
        ).all()
    )
    notifications = list(
        (
            await session.scalars(
                select(AlarmNotification)
                .where(AlarmNotification.alarm_id == alarm_id)
                .order_by(AlarmNotification.created_at.asc())
            )
        ).all()
    )
    return notes, notifications


def _alarm_timeline(
    alarm: Alarm,
    locale: str,
    notes: list[AlarmNote],
    notifications: list[AlarmNotification],
) -> list[dict[str, str]]:
    events = [_created_event(alarm, locale)]
    events.extend(_note_event(note) for note in notes)
    events.extend(_notification_event(item) for item in notifications)
    return sorted(events, key=lambda item: item["at_iso"])


def _created_event(alarm: Alarm, locale: str) -> dict[str, str]:
    return {
        "at": alarm.created_at.isoformat(timespec="minutes"),
        "at_iso": alarm.created_at.isoformat(),
        "description": "Alarm created" if locale == "en" else "Alarm erstellt",
    }


def _note_event(note: AlarmNote) -> dict[str, str]:
    return {
        "at": note.created_at.isoformat(timespec="minutes"),
        "at_iso": note.created_at.isoformat(),
        "description": f"{note.created_by or 'System'}: {note.note}",
    }


def _notification_event(item: AlarmNotification) -> dict[str, str]:
    return {
        "at": item.created_at.isoformat(timespec="minutes"),
        "at_iso": item.created_at.isoformat(),
        "description": f"{item.channel}: {item.result or 'pending'}",
    }


def _alarm_detail_view(alarm: Alarm, person: Person | None, room: Room | None) -> dict[str, Any]:
    return {
        "id": str(alarm.id),
        "short_id": str(alarm.id)[:8],
        "status": alarm.status.value,
        "created_at": alarm.created_at.isoformat(timespec="minutes"),
        "person": person.display_name if person else alarm.person_id or "—",
        "room": room.label if room else alarm.room_id or "—",
        "source": alarm.source,
        "severity": alarm.severity,
        "can_ack": alarm.status == AlarmStatus.TRIGGERED,
        "can_close": alarm.status in {AlarmStatus.TRIGGERED, AlarmStatus.ACKNOWLEDGED},
    }


@router.get("/admin/alarms/{alarm_id}", response_class=HTMLResponse)
async def admin_alarm_detail(
    alarm_id: uuid.UUID,
    request: Request,
    lang: str | None = Query(default=None),
    admin_session: str | None = Cookie(default=None),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_app_settings),
) -> HTMLResponse:
    locale = _requested_locale(request, lang)
    browser_session = await _session_from_request(request, settings, admin_session, extend=True)
    alarm = await get_alarm_or_404(session, alarm_id)
    detail = await _detail_context(session, alarm, locale)
    return _html(
        request,
        "admin_detail.html",
        locale,
        **detail,
        ack_action=f"/admin/alarms/{alarm_id}/ack?lang={locale}",
        resolve_action=f"/admin/alarms/{alarm_id}/resolve?lang={locale}",
        cancel_action=f"/admin/alarms/{alarm_id}/cancel?lang={locale}",
        delete_action=f"/admin/alarms/{alarm_id}/delete?lang={locale}",
        note_action=f"/admin/alarms/{alarm_id}/notes?lang={locale}",
        csrf_token=browser_session.csrf_token,
        operator_name=browser_session.operator_name,
        logout_action="/admin/logout",
    )


async def _enqueue_state(
    request: Request,
    alarm: Alarm,
    *,
    ack: bool,
    actor: str,
    note: str | None,
) -> bool:
    redis = get_redis(request)
    success = True
    if ack:
        ack_result = await enqueue_alarm_acked_event(
            redis, alarm_id=alarm.id, acked_by=actor, note=note, logger=logger
        )
        success = ack_result.success
    state_result = await enqueue_alarm_state_changed_event(
        redis, alarm_id=alarm.id, state=alarm.status.value, logger=logger
    )
    return success and state_result.success


@router.post("/admin/alarms/{alarm_id}/ack")
async def admin_ack_alarm(
    alarm_id: uuid.UUID,
    request: Request,
    csrf_token: str | None = Form(default=None),
    note: str | None = Form(default=None, max_length=2000),
    admin_session: str | None = Cookie(default=None),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_app_settings),
) -> RedirectResponse:
    browser_session = await _action_session(request, settings, admin_session, csrf_token)
    alarm = await get_alarm_or_404(session, alarm_id)
    changed = await acknowledge_alarm(
        session, alarm, acked_by=browser_session.operator_name, note=note
    )
    delivery_ok = (
        await _enqueue_state(
            request, alarm, ack=True, actor=browser_session.operator_name, note=note
        )
        if changed
        else True
    )
    await set_flash(
        get_redis(request),
        browser_session,
        "success" if delivery_ok else "warning",
        "alarm_acknowledged" if delivery_ok else "alarm_acknowledged_delivery_pending",
    )
    return RedirectResponse(f"/admin/alarms/{alarm_id}", status_code=303)


async def _transition_from_form(
    alarm_id: uuid.UUID,
    request: Request,
    csrf_token: str | None,
    note: str | None,
    target: AlarmStatus,
    admin_session: str | None,
    session: AsyncSession,
    settings: Settings,
) -> RedirectResponse:
    browser_session = await _action_session(request, settings, admin_session, csrf_token)
    if target == AlarmStatus.CANCELLED and not (note or "").strip():
        raise HTTPException(status_code=422, detail="reason_required")
    alarm = await get_alarm_or_404(session, alarm_id)
    changed = await transition_alarm(
        session,
        alarm,
        target_status=target,
        actor=browser_session.operator_name,
        note=(note or "").strip() or None,
    )
    delivery_ok = (
        await _enqueue_state(
            request, alarm, ack=False, actor=browser_session.operator_name, note=note
        )
        if changed
        else True
    )
    await set_flash(
        get_redis(request), browser_session, "success" if delivery_ok else "warning", target.value
    )
    return RedirectResponse(f"/admin/alarms/{alarm_id}", status_code=303)


@router.post("/admin/alarms/{alarm_id}/resolve")
async def admin_resolve_alarm(
    alarm_id: uuid.UUID,
    request: Request,
    csrf_token: str | None = Form(default=None),
    note: str | None = Form(default=None, max_length=2000),
    admin_session: str | None = Cookie(default=None),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_app_settings),
) -> RedirectResponse:
    return await _transition_from_form(
        alarm_id, request, csrf_token, note, AlarmStatus.RESOLVED, admin_session, session, settings
    )


@router.post("/admin/alarms/{alarm_id}/cancel")
async def admin_cancel_alarm(
    alarm_id: uuid.UUID,
    request: Request,
    csrf_token: str | None = Form(default=None),
    reason: str = Form(..., min_length=1, max_length=2000),
    admin_session: str | None = Cookie(default=None),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_app_settings),
) -> RedirectResponse:
    return await _transition_from_form(
        alarm_id,
        request,
        csrf_token,
        reason,
        AlarmStatus.CANCELLED,
        admin_session,
        session,
        settings,
    )


@router.post("/admin/alarms/{alarm_id}/notes")
async def admin_add_note(
    alarm_id: uuid.UUID,
    request: Request,
    csrf_token: str | None = Form(default=None),
    note: str = Form(..., min_length=1, max_length=5000),
    admin_session: str | None = Cookie(default=None),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_app_settings),
) -> RedirectResponse:
    browser_session = await _action_session(request, settings, admin_session, csrf_token)
    alarm = await get_alarm_or_404(session, alarm_id)
    session.add(
        AlarmNote(
            alarm_id=alarm.id,
            note=note.strip(),
            created_by=browser_session.operator_name,
            note_type="manual",
        )
    )
    await session.commit()
    await set_flash(get_redis(request), browser_session, "success", "note_added")
    return RedirectResponse(f"/admin/alarms/{alarm_id}", status_code=303)


@router.post("/admin/alarms/{alarm_id}/delete")
async def admin_delete_alarm(
    alarm_id: uuid.UUID,
    request: Request,
    csrf_token: str | None = Form(default=None),
    reason: str = Form(..., min_length=1, max_length=2000),
    admin_session: str | None = Cookie(default=None),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_app_settings),
) -> RedirectResponse:
    browser_session = await _action_session(request, settings, admin_session, csrf_token)
    alarm = await get_alarm_or_404(session, alarm_id)
    alarm.deleted_at = datetime.now(UTC)
    alarm.deleted_by = browser_session.operator_name
    session.add(
        AlarmNote(
            alarm_id=alarm.id,
            note=reason.strip(),
            created_by=browser_session.operator_name,
            note_type="delete",
        )
    )
    await session.commit()
    await set_flash(get_redis(request), browser_session, "success", "alarm_deleted")
    return RedirectResponse("/admin", status_code=303)


@router.post("/admin/alarms/bulk")
async def admin_bulk_action(
    request: Request,
    action: str = Form(..., pattern="^(ack|resolve|cancel)$"),
    csrf_token: str | None = Form(default=None),
    reason: str | None = Form(default=None, max_length=2000),
    admin_session: str | None = Cookie(default=None),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_app_settings),
) -> RedirectResponse:
    browser_session = await _action_session(request, settings, admin_session, csrf_token)
    raw_ids = (await request.form()).getlist("alarm_id")
    _validate_bulk_request(action, reason, raw_ids)
    alarm_ids, missing = _parse_alarm_ids(raw_ids)
    changed, unchanged, newly_missing = await _apply_bulk_actions(
        session,
        alarm_ids,
        action=action,
        actor=browser_session.operator_name,
        reason=reason,
    )
    missing += newly_missing
    await set_flash(
        get_redis(request), browser_session, "success", f"bulk_{changed}_{unchanged}_{missing}"
    )
    return RedirectResponse("/admin", status_code=303)


def _validate_bulk_request(action: str, reason: str | None, raw_ids: list[Any]) -> None:
    if not raw_ids:
        raise HTTPException(status_code=422, detail="selection_required")
    if action == "cancel" and not (reason or "").strip():
        raise HTTPException(status_code=422, detail="reason_required")


def _parse_alarm_ids(raw_ids: list[Any]) -> tuple[list[uuid.UUID], int]:
    alarm_ids: list[uuid.UUID] = []
    invalid = 0
    for raw_id in raw_ids[:500]:
        try:
            alarm_ids.append(uuid.UUID(str(raw_id)))
        except ValueError:
            invalid += 1
    return alarm_ids, invalid


async def _apply_bulk_actions(
    session: AsyncSession,
    alarm_ids: list[uuid.UUID],
    *,
    action: str,
    actor: str,
    reason: str | None,
) -> tuple[int, int, int]:
    changed = unchanged = missing = 0
    for alarm_id in alarm_ids:
        alarm = await session.get(Alarm, alarm_id)
        if alarm is None or alarm.deleted_at is not None:
            missing += 1
            continue
        try:
            did_change = await _apply_bulk_action(
                session, alarm, action=action, actor=actor, reason=reason
            )
        except Exception as exc:
            if _is_conflict(exc):
                unchanged += 1
                continue
            raise
        changed += int(did_change)
        unchanged += int(not did_change)
    return changed, unchanged, missing


async def _apply_bulk_action(
    session: AsyncSession,
    alarm: Alarm,
    *,
    action: str,
    actor: str,
    reason: str | None,
) -> bool:
    if action == "ack":
        return await acknowledge_alarm(session, alarm, acked_by=actor, note=reason)
    target_status = AlarmStatus.RESOLVED if action == "resolve" else AlarmStatus.CANCELLED
    return await transition_alarm(
        session,
        alarm,
        target_status=target_status,
        actor=actor,
        note=reason,
    )


def _is_conflict(exc: Exception) -> bool:
    return getattr(exc, "status_code", None) == 409 or exc.__class__.__name__ == "ConflictError"


@router.get("/admin/export")
async def admin_export(
    request: Request,
    export_format: str = Query(default="csv", alias="format", pattern="^(csv|json)$"),
    status_filter: str | None = Query(default=None, alias="status"),
    admin_session: str | None = Cookie(default=None),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_app_settings),
):
    await _session_from_request(request, settings, admin_session, extend=True)
    from alarm_broker.api.routes.alarms import AlarmExportQuery, export_alarms
    from alarm_broker.api.schemas import ExportFormat

    return await export_alarms(
        AlarmExportQuery(
            status=AlarmStatus(status_filter) if status_filter else None,
            format=ExportFormat(export_format),
            limit=2000,
        ),
        session,
    )
