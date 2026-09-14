"""Alarm detail, lifecycle, bulk-action, and export console routes."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Annotated, Any
from urllib.parse import urlencode

from fastapi import APIRouter, Cookie, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from escalane.alarms.lifecycle import (
    AlarmStateOutcome,
    apply_alarm_state_change,
    get_alarm_or_404,
    soft_delete_alarm,
)
from escalane.config.errors import ConflictError
from escalane.config.settings import Settings
from escalane.contracts.alarms import AlarmStatus
from escalane.persistence.models import (
    Alarm,
    AlarmNote,
    AlarmNotification,
    Person,
    Room,
)
from escalane.web.admin_session import AdminSession, set_flash
from escalane.web.alarm_history import history_page, parse_history_cursor
from escalane.web.deps import get_app_settings, get_redis, get_session
from escalane.web.routes.admin_console import (
    _action_session,
    _html,
    _requested_locale,
    _session_from_request,
)

router = APIRouter()
logger = logging.getLogger("escalane")
AlarmCsrfToken = Annotated[str | None, Form()]
OptionalAlarmNoteForm = Annotated[str | None, Form(max_length=2000)]
AlarmSessionCookie = Annotated[str | None, Cookie()]


@dataclass(frozen=True)
class _BulkTransition:
    """Keep the shared browser bulk-operation inputs explicit within this route module."""

    target_status: AlarmStatus
    actor: str
    reason: str | None
    redis: Any


async def _detail_context(
    session: AsyncSession, alarm: Alarm, locale: str, before: str | None = None
) -> dict[str, Any]:
    """Project display labels and one bounded page of activity."""
    cursor = parse_history_cursor(before)
    labels = (
        await session.execute(
            select(Person.display_name, Room.label)
            .select_from(Alarm)
            .outerjoin(Person, Person.id == Alarm.person_id)
            .outerjoin(Room, Room.id == Alarm.room_id)
            .where(Alarm.id == alarm.id)
        )
    ).one_or_none()
    view = _alarm_detail_view(alarm, None, None)
    if labels is not None:
        view["person"] = labels[0] if labels[0] is not None else view["person"]
        view["room"] = labels[1] if labels[1] is not None else view["room"]
    events, next_cursor, include_creation = await history_page(session, alarm.id, cursor)
    if include_creation:
        events.insert(0, {**_created_event(alarm, locale), "key": "created"})
    params = urlencode({"lang": locale, "before": next_cursor}) if next_cursor else ""
    path = f"/admin/alarms/{alarm.id}"
    return {
        "alarm": view,
        "events": events,
        "older_activity_url": f"{path}?{params}#activity-title" if params else None,
        "older_activity_fragment_url": f"{path}/history?{params}" if params else None,
        "latest_activity_url": f"{path}?lang={locale}#activity-title" if before else None,
    }


def _alarm_timeline(
    alarm: Alarm,
    locale: str,
    notes: list[AlarmNote],
    notifications: list[AlarmNotification],
) -> list[dict[str, str]]:
    """Merge creation, note, and delivery records into one timestamp-sorted operator timeline."""
    events = [_created_event(alarm, locale)]
    events.extend(_note_event(note) for note in notes)
    events.extend(_notification_event(item) for item in notifications)
    return sorted(events, key=lambda item: item["at_iso"])


def _created_event(alarm: Alarm, locale: str) -> dict[str, str]:
    """Represent the immutable creation event in the selected console language."""
    return {
        "at": alarm.created_at.isoformat(timespec="minutes"),
        "at_iso": alarm.created_at.isoformat(),
        "description": "Alarm created" if locale == "en" else "Alarm erstellt",
    }


def _note_event(note: AlarmNote) -> dict[str, str]:
    """Represent an operator or system note without changing its original content."""
    return {
        "at": note.created_at.isoformat(timespec="minutes"),
        "at_iso": note.created_at.isoformat(),
        "description": f"{note.created_by or 'System'}: {note.note}",
    }


def _notification_event(item: AlarmNotification) -> dict[str, str]:
    """Represent delivery state as timeline evidence instead of inferring success from alarms."""
    return {
        "at": item.created_at.isoformat(timespec="minutes"),
        "at_iso": item.created_at.isoformat(),
        "description": f"{item.channel}: {item.result or 'pending'}",
    }


def _alarm_detail_view(alarm: Alarm, person: Person | None, room: Room | None) -> dict[str, Any]:
    """Convert persistence fields into a template-safe detail view with lifecycle permissions."""
    return {
        "id": str(alarm.id),
        "short_id": str(alarm.id)[:8],
        "status": alarm.status.value,
        "created_at": alarm.created_at.isoformat(timespec="minutes"),
        "person": person.display_name if person else alarm.person_id or "-",
        "room": room.label if room else alarm.room_id or "-",
        "source": alarm.source,
        "severity": alarm.severity,
        "can_ack": alarm.status == AlarmStatus.TRIGGERED,
        "can_close": alarm.status in {AlarmStatus.TRIGGERED, AlarmStatus.ACKNOWLEDGED},
    }


async def _alarm_action_context(
    alarm_id: uuid.UUID,
    request: Request,
    settings: Settings,
    admin_session: str | None,
    csrf_token: str | None,
    session: AsyncSession,
) -> tuple[AdminSession, Alarm]:
    """Validate an alarm form action and load its target in a stable order."""
    browser_session = await _action_session(request, settings, admin_session, csrf_token)
    alarm = await get_alarm_or_404(session, alarm_id)
    return browser_session, alarm


# Render an authenticated operator view of one active or historical alarm.
@router.get("/admin/alarms/{alarm_id}", response_class=HTMLResponse)
async def admin_alarm_detail(
    alarm_id: uuid.UUID,
    request: Request,
    lang: str | None = Query(default=None),
    before: str | None = Query(default=None, max_length=300),
    admin_session: str | None = Cookie(default=None),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_app_settings),
) -> HTMLResponse:
    locale = _requested_locale(request, lang)
    browser_session = await _session_from_request(request, settings, admin_session, extend=True)
    alarm = await get_alarm_or_404(session, alarm_id)
    detail = await _detail_context(session, alarm, locale, before)
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


@router.get("/admin/alarms/{alarm_id}/drawer", response_class=HTMLResponse)
async def admin_alarm_drawer(
    alarm_id: uuid.UUID,
    request: Request,
    lang: str | None = Query(default=None),
    before: str | None = Query(default=None, max_length=300),
    admin_session: str | None = Cookie(default=None),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_app_settings),
) -> HTMLResponse:
    """Return current, authenticated alarm context for progressive drawer enhancement."""
    locale = _requested_locale(request, lang)
    browser_session = await _session_from_request(request, settings, admin_session, extend=True)
    alarm = await get_alarm_or_404(session, alarm_id)
    detail = await _detail_context(session, alarm, locale, before)
    return _html(
        request,
        "admin_detail_drawer.html",
        locale,
        **detail,
        ack_action=f"/admin/alarms/{alarm_id}/ack?lang={locale}",
        resolve_action=f"/admin/alarms/{alarm_id}/resolve?lang={locale}",
        cancel_action=f"/admin/alarms/{alarm_id}/cancel?lang={locale}",
        note_action=f"/admin/alarms/{alarm_id}/notes?lang={locale}",
        csrf_token=browser_session.csrf_token,
    )


@router.get("/admin/alarms/{alarm_id}/history", response_class=HTMLResponse)
async def admin_alarm_history(
    alarm_id: uuid.UUID,
    request: Request,
    lang: str | None = Query(default=None),
    before: str | None = Query(default=None, max_length=300),
    admin_session: str | None = Cookie(default=None),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_app_settings),
) -> HTMLResponse:
    """Serve an authenticated history fragment for progressive enhancement."""
    locale = _requested_locale(request, lang)
    await _session_from_request(request, settings, admin_session, extend=True)
    alarm = await get_alarm_or_404(session, alarm_id)
    detail = await _detail_context(session, alarm, locale, before)
    return _html(request, "admin_history.html", locale, **detail)


# Acknowledge one alarm and show whether downstream event delivery remains pending.
@router.post("/admin/alarms/{alarm_id}/ack")
async def admin_ack_alarm(
    alarm_id: uuid.UUID,
    request: Request,
    csrf_token: AlarmCsrfToken = None,
    note: OptionalAlarmNoteForm = None,
    admin_session: AlarmSessionCookie = None,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_app_settings),
) -> RedirectResponse:
    browser_session, alarm = await _alarm_action_context(
        alarm_id, request, settings, admin_session, csrf_token, session
    )
    outcome = await apply_alarm_state_change(
        session,
        get_redis(request),
        alarm,
        target_status=AlarmStatus.ACKNOWLEDGED,
        actor=browser_session.operator_name,
        note=note,
        logger=logger,
    )
    delivery_ok = not outcome.pending
    await set_flash(
        get_redis(request),
        browser_session,
        "success" if delivery_ok else "warning",
        "alarm_acknowledged" if delivery_ok else "alarm_acknowledged_delivery_pending",
    )
    return _detail_redirect(alarm_id, request)


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
    """Apply a validated console transition and preserve its actor and reason for auditability."""
    browser_session = await _action_session(request, settings, admin_session, csrf_token)
    if target == AlarmStatus.CANCELLED and not (note or "").strip():
        raise HTTPException(status_code=422, detail="reason_required")
    alarm = await get_alarm_or_404(session, alarm_id)
    outcome = await apply_alarm_state_change(
        session,
        get_redis(request),
        alarm,
        target_status=target,
        actor=browser_session.operator_name,
        note=(note or "").strip() or None,
        logger=logger,
    )
    delivery_ok = not outcome.pending
    await set_flash(
        get_redis(request), browser_session, "success" if delivery_ok else "warning", target.value
    )
    return _detail_redirect(alarm_id, request)


# Resolve from the detail view after session and CSRF validation.
@router.post("/admin/alarms/{alarm_id}/resolve")
async def admin_resolve_alarm(
    alarm_id: uuid.UUID,
    request: Request,
    csrf_token: AlarmCsrfToken = None,
    note: OptionalAlarmNoteForm = None,
    admin_session: AlarmSessionCookie = None,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_app_settings),
) -> RedirectResponse:
    return await _transition_from_form(
        alarm_id, request, csrf_token, note, AlarmStatus.RESOLVED, admin_session, session, settings
    )


# Cancellation requires an explicit reason for the terminal transition.
@router.post("/admin/alarms/{alarm_id}/cancel")
async def admin_cancel_alarm(
    alarm_id: uuid.UUID,
    request: Request,
    csrf_token: AlarmCsrfToken = None,
    reason: str = Form(..., min_length=1, max_length=2000),
    admin_session: AlarmSessionCookie = None,
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


# Append an attributed operator note without changing lifecycle state.
@router.post("/admin/alarms/{alarm_id}/notes")
async def admin_add_note(
    alarm_id: uuid.UUID,
    request: Request,
    csrf_token: AlarmCsrfToken = None,
    note: str = Form(..., min_length=1, max_length=5000),
    admin_session: AlarmSessionCookie = None,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_app_settings),
) -> RedirectResponse:
    browser_session, alarm = await _alarm_action_context(
        alarm_id, request, settings, admin_session, csrf_token, session
    )
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
    return _detail_redirect(alarm_id, request)


# Soft deletion retains the record for audit and recovery.
@router.post("/admin/alarms/{alarm_id}/delete")
async def admin_delete_alarm(
    alarm_id: uuid.UUID,
    request: Request,
    reason: str = Form(..., min_length=1, max_length=2000),
    csrf_token: AlarmCsrfToken = None,
    admin_session: AlarmSessionCookie = None,
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_app_settings),
) -> RedirectResponse:
    browser_session, alarm = await _alarm_action_context(
        alarm_id, request, settings, admin_session, csrf_token, session
    )
    await soft_delete_alarm(
        session,
        alarm,
        deleted_by=browser_session.operator_name,
        note=reason.strip(),
    )
    await set_flash(get_redis(request), browser_session, "success", "alarm_deleted")
    locale = _requested_locale(request, request.query_params.get("lang"))
    return RedirectResponse(f"/admin?lang={locale}", status_code=303)


# Apply a bounded selection while separately counting concurrent or missing records.
@router.post("/admin/alarms/bulk")
async def admin_bulk_action(
    request: Request,
    action: str = Form(..., pattern="^(ack|resolve|cancel)$"),
    csrf_token: AlarmCsrfToken = None,
    reason: OptionalAlarmNoteForm = None,
    admin_session: AlarmSessionCookie = None,
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
        _bulk_transition(
            action,
            actor=browser_session.operator_name,
            reason=reason,
            redis=get_redis(request),
        ),
    )
    missing += newly_missing
    await set_flash(
        get_redis(request), browser_session, "success", f"bulk_{changed}_{unchanged}_{missing}"
    )
    locale = _requested_locale(request, request.query_params.get("lang"))
    return RedirectResponse(f"/admin?lang={locale}", status_code=303)


def _detail_redirect(alarm_id: uuid.UUID, request: Request) -> RedirectResponse:
    locale = _requested_locale(request, request.query_params.get("lang"))
    return RedirectResponse(f"/admin/alarms/{alarm_id}?lang={locale}", status_code=303)


def _validate_bulk_request(action: str, reason: str | None, raw_ids: list[Any]) -> None:
    """Reject empty selections and cancellation requests that lack an audit reason."""
    if not raw_ids:
        raise HTTPException(status_code=422, detail="selection_required")
    if action == "cancel" and not (reason or "").strip():
        raise HTTPException(status_code=422, detail="reason_required")


def _parse_alarm_ids(raw_ids: list[Any]) -> tuple[list[uuid.UUID], int]:
    """Parse at most 500 IDs and count invalid values without failing the whole selection."""
    alarm_ids: list[uuid.UUID] = []
    invalid = 0
    for raw_id in raw_ids[:500]:
        try:
            alarm_ids.append(uuid.UUID(str(raw_id)))
        except ValueError:
            invalid += 1
    return alarm_ids, invalid


def _bulk_transition(action: str, *, actor: str, reason: str | None, redis: Any) -> _BulkTransition:
    """Translate the form action once before processing the ordered selection."""
    target_status = AlarmStatus.RESOLVED if action == "resolve" else AlarmStatus.CANCELLED
    if action == "ack":
        target_status = AlarmStatus.ACKNOWLEDGED
    return _BulkTransition(target_status, actor, reason, redis)


async def _apply_bulk_actions(
    session: AsyncSession,
    alarm_ids: list[uuid.UUID],
    transition: _BulkTransition,
) -> tuple[int, int, int]:
    """Process each selected alarm independently so concurrent changes do not abort the batch."""
    changed = unchanged = missing = 0
    for alarm_id in alarm_ids:
        alarm = await session.get(Alarm, alarm_id)
        if alarm is None or alarm.deleted_at is not None:
            missing += 1
            continue
        outcome = await _apply_bulk_transition(session, alarm, transition)
        if outcome is None:
            unchanged += 1
            continue
        if _bulk_action_changed(alarm, outcome):
            changed += 1
        else:
            unchanged += 1
    return changed, unchanged, missing


async def _apply_bulk_transition(
    session: AsyncSession,
    alarm: Alarm,
    transition: _BulkTransition,
) -> AlarmStateOutcome | None:
    """Apply one browser transition, normalizing only known concurrent-state conflicts."""
    try:
        return await apply_alarm_state_change(
            session,
            transition.redis,
            alarm,
            target_status=transition.target_status,
            actor=transition.actor,
            note=transition.reason,
            logger=logger,
        )
    except HTTPException as exc:
        if exc.status_code != 409:
            raise
    except ConflictError:
        pass
    return None


def _bulk_action_changed(alarm: Alarm, outcome: AlarmStateOutcome) -> bool:
    """Log deferred delivery while returning the state-change count contribution."""
    if outcome.pending:
        logger.warning(
            "bulk_event_delivery_pending",
            extra={
                "alarm_id": str(alarm.id),
                "published": outcome.published,
            },
        )
    return outcome.changed


# Reuse the canonical API serializer after authenticating the browser session.
@router.get("/admin/export")
async def admin_export(
    request: Request,
    export_format: str = Query(default="csv", alias="format", pattern="^(csv|json)$"),
    status_filter: AlarmStatus | None = Query(default=None, alias="status"),
    severity_filter: str | None = Query(default=None, alias="severity", pattern="^(P0|P1|P2)$"),
    admin_session: str | None = Cookie(default=None),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_app_settings),
):
    await _session_from_request(request, settings, admin_session, extend=True)
    from escalane.web.routes.alarms import AlarmExportQuery, export_alarms
    from escalane.web.schemas import ExportFormat

    return await export_alarms(
        AlarmExportQuery(
            status=status_filter,
            severity=severity_filter,
            format=ExportFormat(export_format),
            limit=2000,
        ),
        session,
    )
