from __future__ import annotations

import hashlib
import html as _html
import secrets
from datetime import UTC, datetime
from string import Template

from fastapi import APIRouter, Cookie, Depends, Form, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from alarm_broker.api.deps import get_app_settings, get_redis, get_session, is_secure_request
from alarm_broker.api.template_loader import load_template
from alarm_broker.db.models import Alarm, AlarmStatus
from alarm_broker.settings import Settings


def escape(s: str) -> str:
    """HTML-escape a string, including single and double quotes."""
    return _html.escape(s, quote=True)


router = APIRouter()

_SESSION_TTL_SECONDS = 3600  # 1 hour


_TEMPLATE: Template = load_template("admin.html")


def _session_key(token: str) -> str:
    return f"admin_session:{token}"


def _admin_key_marker(settings: Settings) -> str:
    return hashlib.sha256(settings.admin_api_key.encode()).hexdigest()


async def _validate_session(settings: Settings, redis, session_token: str | None) -> None:
    """Validate that the session cookie maps to a valid admin key."""
    if not settings.admin_api_key:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="ADMIN_API_KEY is not configured. Set it in .env or environment variables.",
        )
    if not session_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated. Please log in at /admin/login.",
        )
    stored_marker = await redis.get(_session_key(session_token))
    if stored_marker is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expired. Please log in again at /admin/login.",
        )
    current_marker = _admin_key_marker(settings)
    if not secrets.compare_digest(stored_marker, current_marker):
        await redis.delete(_session_key(session_token))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid session. Please log in again at /admin/login.",
        )
    await redis.expire(_session_key(session_token), _SESSION_TTL_SECONDS)


@router.get("/admin/login", response_class=HTMLResponse)
async def admin_login_page() -> HTMLResponse:
    """Render a minimal login form for the admin dashboard."""
    html = """<!DOCTYPE html>
<html lang="en"><head><title>Admin Login - Alarm Broker</title>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="theme-color" content="#1a1a2e">
<meta name="color-scheme" content="dark">
<style>
*{box-sizing:border-box}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
background:#1a1a2e;color:#e0e0e0;display:flex;justify-content:center;align-items:center;
min-height:100vh;margin:0;padding:16px}
form{background:#16213e;padding:2rem;border-radius:12px;width:min(400px,100%);
border:1px solid rgba(255,255,255,0.08);box-shadow:0 20px 40px rgba(0,0,0,0.4)}
h2{margin:0 0 1.5rem;font-size:1.5rem;letter-spacing:-0.01em}
label{display:block;font-size:0.85rem;font-weight:600;color:#94a3b8;margin-bottom:0.5rem}
input{width:100%;padding:12px 14px;margin:0 0 1rem;border:1px solid #334155;
border-radius:8px;background:#0f3460;color:#e0e0e0;font-size:1rem;
transition:border-color 0.2s,box-shadow 0.2s}
input:focus{outline:none;border-color:#38bdf8;box-shadow:0 0 0 3px rgba(56,189,248,0.15)}
button{width:100%;padding:14px;background:#e94560;color:#fff;border:none;
border-radius:8px;cursor:pointer;font-size:1rem;font-weight:600;
transition:background 0.2s,transform 0.1s}
button:hover{background:#c73e54}
button:active{transform:translateY(1px)}
button:focus-visible{outline:2px solid #38bdf8;outline-offset:2px}
</style></head>
<body>
<form method="POST" action="/admin/login" aria-label="Admin login">
<h2>Admin Login</h2>
<label for="admin_key">Admin Key</label>
<input type="password" id="admin_key" name="admin_key" required autocomplete="off"
       placeholder="Enter admin API key" aria-required="true">
<button type="submit">Login</button>
</form>
</body></html>"""
    return HTMLResponse(content=html)


@router.post("/admin/login")
async def admin_login_submit(
    request: Request,
    admin_key: str = Form(...),
    settings: Settings = Depends(get_app_settings),
) -> RedirectResponse:
    """Validate admin key and set a session cookie."""
    if not settings.admin_api_key:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="ADMIN_API_KEY is not configured. Set it in .env or environment variables.",
        )
    if not secrets.compare_digest(admin_key, settings.admin_api_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid admin key. Please check your credentials and try again.",
        )

    token = secrets.token_urlsafe(32)
    redis = get_redis(request)
    await redis.set(
        _session_key(token),
        _admin_key_marker(settings),
        ex=_SESSION_TTL_SECONDS,
    )

    response = RedirectResponse(url="/admin", status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie(
        key="admin_session",
        value=token,
        httponly=True,
        secure=is_secure_request(request, settings),
        samesite="strict",
        max_age=_SESSION_TTL_SECONDS,
    )
    return response


@router.get("/admin", response_class=HTMLResponse)
async def admin_dashboard(
    request: Request,
    refresh: int = Query(default=10, ge=5, le=120),
    limit: int = Query(default=100, ge=1, le=500),
    status_filter: str | None = Query(default=None, alias="status"),
    admin_session: str | None = Cookie(default=None),
    session: AsyncSession = Depends(get_session),
    settings: Settings = Depends(get_app_settings),
) -> HTMLResponse:
    redis = get_redis(request)
    await _validate_session(settings, redis, admin_session)

    stmt = (
        select(Alarm)
        .where(Alarm.deleted_at.is_(None))
        .order_by(Alarm.created_at.desc(), Alarm.id.desc())
    )
    if status_filter and status_filter in [s.value for s in AlarmStatus]:
        stmt = stmt.where(Alarm.status == AlarmStatus(status_filter))
    stmt = stmt.limit(limit)
    alarms = (await session.scalars(stmt)).all()

    total_count = await session.scalar(
        select(func.count(Alarm.id)).where(Alarm.deleted_at.is_(None))
    )
    counts_rows = (
        await session.execute(
            select(Alarm.status, func.count(Alarm.id))
            .where(Alarm.deleted_at.is_(None))
            .group_by(Alarm.status)
        )
    ).all()
    counts = {status.value: 0 for status in AlarmStatus}
    for status_value, count in counts_rows:
        counts[status_value.value] = int(count)

    status_cards = []
    for state in AlarmStatus:
        active_class = "active" if state.value == status_filter else ""
        count = counts.get(state.value, 0)
        label = escape(state.value)
        status_cards.append(
            f"<article class='card {active_class}'><h3>{label}</h3><p>{count}</p></article>"
        )

    rows = []
    for alarm in alarms:
        alarm_state = escape(alarm.status.value)
        alarm_id = str(alarm.id)
        alarm_short_id = escape(alarm_id[:8])
        created_at = alarm.created_at
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=UTC)
        time_diff = datetime.now(UTC) - created_at
        minutes_ago = int(time_diff.total_seconds() / 60)
        if minutes_ago < 60:
            time_display = f"{minutes_ago}m ago"
        else:
            time_display = f"{minutes_ago // 60}h {minutes_ago % 60}m ago"
        created_iso = created_at.isoformat()
        person_display = str(alarm.person_id or "-")
        room_display = str(alarm.room_id or "-")
        source_display = alarm.source
        severity_display = alarm.severity
        acked_by_display = str(alarm.acked_by or "-")

        can_ack = alarm.status == AlarmStatus.TRIGGERED
        can_resolve = alarm.status in (AlarmStatus.TRIGGERED, AlarmStatus.ACKNOWLEDGED)
        ack_disabled_attr = " disabled" if not can_ack else ""
        resolve_disabled_attr = " disabled" if not can_resolve else ""
        search_blob = " ".join(
            [
                alarm_id,
                alarm_state,
                person_display,
                room_display,
                source_display,
                severity_display,
                acked_by_display,
            ]
        ).lower()

        rows.append(
            "<tr class='alarm-row'"
            f" data-alarm-id='{escape(alarm_id)}'"
            f" data-short-id='{alarm_short_id}'"
            f" data-status='{alarm_state}'"
            f" data-created='{escape(time_display)} ({escape(created_iso)})'"
            f" data-person='{escape(person_display)}'"
            f" data-room='{escape(room_display)}'"
            f" data-source='{escape(source_display)}'"
            f" data-severity='{escape(severity_display)}'"
            f" data-acked-by='{escape(acked_by_display)}'"
            f" data-can-ack='{'true' if can_ack else 'false'}'"
            f" data-can-resolve='{'true' if can_resolve else 'false'}'"
            f" data-search='{escape(search_blob)}'>"
            f"<td><span class='state {alarm_state}'>{alarm_state}</span></td>"
            f"<td class='mono'>{alarm_short_id}...</td>"
            f"<td class='muted'>{escape(time_display)}</td>"
            f"<td>{escape(person_display)}</td>"
            f"<td>{escape(room_display)}</td>"
            f"<td>{escape(source_display)}</td>"
            f"<td><span class='severity'>{escape(severity_display)}</span></td>"
            f"<td>{escape(acked_by_display)}</td>"
            "<td class='actions'>"
            "<button type='button' class='btn detail-btn'>Details</button>"
            "<button type='button' class='btn btn-ack quick-ack-btn'"
            f"{ack_disabled_attr}>Quick Ack</button>"
            "<button type='button' class='btn btn-resolve quick-resolve-btn'"
            f"{resolve_disabled_attr}>Quick Resolve</button>"
            "</td>"
            "</tr>"
        )

    filter_qs = f"status={status_filter}&" if status_filter else ""
    if settings.simulation_enabled:
        simulation_panel = (
            "<section id='simulation-panel' class='sim-panel' data-enabled='true'>"
            "<div class='sim-head'>"
            "<h2>Simulation Mode</h2>"
            "<p class='muted'>Monitor mock notifications and demo seed helpers.</p>"
            "</div>"
            "<p id='sim-status' class='muted'>Checking simulation status ...</p>"
            "<p class='muted'>Notifications: <strong id='sim-count'>-</strong></p>"
            "<div class='sim-actions'>"
            "<button id='sim-refresh-btn' type='button' class='btn'>Refresh</button>"
            "<button id='sim-clear-btn' type='button' class='btn'>Clear Notifications</button>"
            "<button id='sim-seed-btn' type='button' class='btn'>Load Seed Info</button>"
            "</div>"
            "</section>"
        )
    else:
        simulation_panel = (
            "<section id='simulation-panel' class='sim-panel' data-enabled='false'>"
            "<div class='sim-head'>"
            "<h2>Simulation Mode</h2>"
            "<p class='muted'>Simulation mode is currently disabled on this server.</p>"
            "</div>"
            "</section>"
        )

    page = _TEMPLATE.substitute(
        refresh_seconds=refresh,
        row_count=str(len(alarms)),
        total_count=str(total_count or 0),
        generated_at=escape(datetime.now(UTC).isoformat()),
        status_cards="\n".join(status_cards),
        filter_qs=filter_qs,
        simulation_panel=simulation_panel,
        admin_key_json='""',
        rows="\n".join(rows)
        if rows
        else "<tr><td colspan='9' class='muted'>No alarms found</td></tr>",
    )
    return HTMLResponse(content=page)
