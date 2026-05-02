"""Targeted tests for API and UI behavior outside the main alarm trigger flow.

Covers:
- admin dashboard ACK/resolve capabilities and simulation panel states
- single alarm transitions, get/patch/delete, and bulk edge cases
- alarm list filters, export formats, stats, and cursor pagination
- health/readiness endpoints
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient

from alarm_broker.api.main import create_app
from alarm_broker.db.models import Alarm, AlarmStatus

try:
    from tests.helpers import admin_login
except ModuleNotFoundError:
    from helpers import admin_login

pytestmark = [pytest.mark.integration]

ADMIN_KEY = "dev-admin-key"
HEADERS = {"X-Admin-Key": ADMIN_KEY}


def _make_alarm(
    *,
    alarm_id: uuid.UUID | None = None,
    status: AlarmStatus = AlarmStatus.TRIGGERED,
    ack_token: str | None = None,
    created_at: datetime | None = None,
    source: str = "test",
    severity: str = "P0",
    person_id: str = "ma-012",
    room_id: str = "bg-1.23",
    resolved_at: datetime | None = None,
    resolved_by: str | None = None,
    acked_at: datetime | None = None,
    acked_by: str | None = None,
) -> Alarm:
    return Alarm(
        id=alarm_id or uuid.uuid4(),
        status=status,
        source=source,
        event="alarm.trigger",
        person_id=person_id,
        room_id=room_id,
        site_id="bg",
        device_id="ylk-t5-10023",
        severity=severity,
        silent=True,
        ack_token=ack_token or f"tok-{uuid.uuid4().hex[:8]}",
        created_at=created_at or datetime.now(UTC),
        meta={},
        resolved_at=resolved_at,
        resolved_by=resolved_by,
        acked_at=acked_at,
        acked_by=acked_by,
    )


# ---------------------------------------------------------------------------
# admin_ui.py - dashboard ACK/resolve capabilities
# ---------------------------------------------------------------------------


async def test_admin_dashboard_ack_resolve_capabilities(
    engine, sessionmaker, seeded_db, fake_redis, settings
):
    """Dashboard renders alarms with can_ack/can_resolve data attributes."""
    settings.admin_api_key = ADMIN_KEY
    now = datetime.now(UTC)

    triggered_id = uuid.uuid4()
    acked_id = uuid.uuid4()
    resolved_id = uuid.uuid4()

    async with sessionmaker() as session:
        session.add(
            _make_alarm(
                alarm_id=triggered_id,
                status=AlarmStatus.TRIGGERED,
                created_at=now,
            )
        )
        session.add(
            _make_alarm(
                alarm_id=acked_id,
                status=AlarmStatus.ACKNOWLEDGED,
                created_at=now - timedelta(minutes=1),
                acked_at=now - timedelta(minutes=1),
                acked_by="Ops",
            )
        )
        session.add(
            _make_alarm(
                alarm_id=resolved_id,
                status=AlarmStatus.RESOLVED,
                created_at=now - timedelta(minutes=2),
                resolved_at=now - timedelta(minutes=2),
                resolved_by="Ops",
            )
        )
        await session.commit()

    app = create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            await admin_login(client, ADMIN_KEY)
            resp = await client.get("/admin")

    assert resp.status_code == 200
    html = resp.text
    # Triggered alarm: can_ack=true, can_resolve=true
    assert f"data-alarm-id='{triggered_id}'" in html
    assert "data-can-ack='true'" in html
    assert "data-can-resolve='true'" in html
    # Resolved alarm: both disabled
    assert "data-can-ack='false'" in html


async def test_admin_dashboard_time_display_hours(
    engine, sessionmaker, seeded_db, fake_redis, settings
):
    """Dashboard displays hours for alarms older than 60 minutes."""
    settings.admin_api_key = ADMIN_KEY

    old_alarm_id = uuid.uuid4()
    async with sessionmaker() as session:
        session.add(
            _make_alarm(
                alarm_id=old_alarm_id,
                created_at=datetime.now(UTC) - timedelta(hours=2, minutes=30),
            )
        )
        await session.commit()

    app = create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            await admin_login(client, ADMIN_KEY)
            resp = await client.get("/admin")

    assert resp.status_code == 200
    assert "2h " in resp.text  # "2h 30m ago"


async def test_admin_dashboard_simulation_enabled(
    engine, sessionmaker, seeded_db, fake_redis, settings
):
    """Dashboard renders simulation panel when simulation_enabled=True."""
    settings.admin_api_key = ADMIN_KEY
    settings.simulation_enabled = True

    app = create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            await admin_login(client, ADMIN_KEY)
            resp = await client.get("/admin")

    assert resp.status_code == 200
    assert "data-enabled='true'" in resp.text
    assert "sim-refresh-btn" in resp.text


async def test_admin_dashboard_simulation_disabled(engine, seeded_db, fake_redis, settings):
    """Dashboard renders disabled simulation panel."""
    settings.admin_api_key = ADMIN_KEY
    settings.simulation_enabled = False

    app = create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            await admin_login(client, ADMIN_KEY)
            resp = await client.get("/admin")

    assert resp.status_code == 200
    assert "data-enabled='false'" in resp.text
    assert "currently disabled" in resp.text


# ---------------------------------------------------------------------------
# alarm_operations.py - single state transitions
# ---------------------------------------------------------------------------


async def test_single_alarm_get(engine, sessionmaker, seeded_db, fake_redis, settings):
    """GET /v1/alarms/{id} returns alarm details."""
    settings.admin_api_key = ADMIN_KEY
    alarm_id = uuid.uuid4()

    async with sessionmaker() as session:
        session.add(_make_alarm(alarm_id=alarm_id))
        await session.commit()

    app = create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(f"/v1/alarms/{alarm_id}", headers=HEADERS)

    assert resp.status_code == 200
    assert resp.json()["id"] == str(alarm_id)


async def test_single_alarm_get_nonexistent(engine, seeded_db, fake_redis, settings):
    """GET /v1/alarms/{id} returns 404 for non-existent alarm."""
    settings.admin_api_key = ADMIN_KEY
    missing_id = uuid.uuid4()

    app = create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(f"/v1/alarms/{missing_id}", headers=HEADERS)

    assert resp.status_code == 404


async def test_patch_alarm_severity(engine, sessionmaker, seeded_db, fake_redis, settings):
    """PATCH /v1/alarms/{id} updates severity."""
    settings.admin_api_key = ADMIN_KEY
    alarm_id = uuid.uuid4()

    async with sessionmaker() as session:
        session.add(_make_alarm(alarm_id=alarm_id, severity="P0"))
        await session.commit()

    app = create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.patch(
                f"/v1/alarms/{alarm_id}",
                headers=HEADERS,
                json={"severity": "P1"},
            )

    assert resp.status_code == 200
    assert resp.json()["severity"] == "P1"


async def test_patch_alarm_title_description_tags(
    engine, sessionmaker, seeded_db, fake_redis, settings
):
    """PATCH /v1/alarms/{id} updates title, description, tags in meta."""
    settings.admin_api_key = ADMIN_KEY
    alarm_id = uuid.uuid4()

    async with sessionmaker() as session:
        session.add(_make_alarm(alarm_id=alarm_id))
        await session.commit()

    app = create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.patch(
                f"/v1/alarms/{alarm_id}",
                headers=HEADERS,
                json={"title": "Fire alarm", "description": "Floor 3", "tags": ["fire"]},
            )

    assert resp.status_code == 200
    meta = resp.json()["meta"]
    assert meta["title"] == "Fire alarm"
    assert meta["description"] == "Floor 3"
    assert meta["tags"] == ["fire"]


async def test_delete_alarm(engine, sessionmaker, seeded_db, fake_redis, settings):
    """DELETE /v1/alarms/{id} soft-deletes the alarm."""
    settings.admin_api_key = ADMIN_KEY
    alarm_id = uuid.uuid4()

    async with sessionmaker() as session:
        session.add(_make_alarm(alarm_id=alarm_id))
        await session.commit()

    app = create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.delete(f"/v1/alarms/{alarm_id}", headers=HEADERS)

    assert resp.status_code == 204

    async with sessionmaker() as session:
        alarm = await session.get(Alarm, alarm_id)
        assert alarm is not None
        assert alarm.deleted_at is not None


async def test_delete_alarm_nonexistent(engine, seeded_db, fake_redis, settings):
    """DELETE /v1/alarms/{id} returns 404 for non-existent alarm."""
    settings.admin_api_key = ADMIN_KEY

    app = create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.delete(f"/v1/alarms/{uuid.uuid4()}", headers=HEADERS)

    assert resp.status_code == 404


async def test_delete_alarm_already_deleted(engine, sessionmaker, seeded_db, fake_redis, settings):
    """DELETE /v1/alarms/{id} returns 404 if alarm already deleted.

    The new security model makes get_alarm_or_404 return 404 for soft-deleted
    alarms, so the 409 Conflict branch is no longer reachable via the API.
    """
    settings.admin_api_key = ADMIN_KEY
    alarm_id = uuid.uuid4()

    async with sessionmaker() as session:
        a = _make_alarm(alarm_id=alarm_id)
        a.deleted_at = datetime.now(UTC)
        session.add(a)
        await session.commit()

    app = create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.delete(f"/v1/alarms/{alarm_id}", headers=HEADERS)

    assert resp.status_code == 404


async def test_single_ack_transition(engine, sessionmaker, seeded_db, fake_redis, settings):
    """POST /v1/alarms/{id}/ack transitions triggered -> acknowledged."""
    settings.admin_api_key = ADMIN_KEY
    alarm_id = uuid.uuid4()

    async with sessionmaker() as session:
        session.add(_make_alarm(alarm_id=alarm_id))
        await session.commit()

    app = create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                f"/v1/alarms/{alarm_id}/ack",
                headers=HEADERS,
                json={"acked_by": "Tester", "note": "seen"},
            )

    assert resp.status_code == 204


async def test_single_cancel_transition(engine, sessionmaker, seeded_db, fake_redis, settings):
    """POST /v1/alarms/{id}/cancel transitions triggered -> cancelled."""
    settings.admin_api_key = ADMIN_KEY
    alarm_id = uuid.uuid4()

    async with sessionmaker() as session:
        session.add(_make_alarm(alarm_id=alarm_id))
        await session.commit()

    app = create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                f"/v1/alarms/{alarm_id}/cancel",
                headers=HEADERS,
                json={"actor": "Ops"},
            )

    assert resp.status_code == 204


async def test_bulk_resolve_all_already_resolved(
    engine, sessionmaker, seeded_db, fake_redis, settings
):
    """Bulk resolve with all alarms already resolved reports 0 changed."""
    settings.admin_api_key = ADMIN_KEY
    now = datetime.now(UTC)

    ids = [uuid.uuid4() for _ in range(2)]
    async with sessionmaker() as session:
        for aid in ids:
            session.add(
                _make_alarm(
                    alarm_id=aid,
                    status=AlarmStatus.RESOLVED,
                    resolved_at=now,
                    resolved_by="Ops",
                )
            )
        await session.commit()

    app = create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/v1/alarms/bulk/resolve",
                headers=HEADERS,
                json={"alarm_ids": [str(a) for a in ids], "actor": "Ops"},
            )

    assert resp.status_code == 200
    body = resp.json()
    assert body["changed"] == 0
    assert body["unchanged"] == 2


# ---------------------------------------------------------------------------
# alarms.py - filters and export behavior
# ---------------------------------------------------------------------------


async def test_list_alarms_created_after(engine, sessionmaker, seeded_db, fake_redis, settings):
    """GET /v1/alarms?created_after=... filters alarms."""
    settings.admin_api_key = ADMIN_KEY
    now = datetime.now(UTC)

    old_id = uuid.uuid4()
    new_id = uuid.uuid4()

    async with sessionmaker() as session:
        session.add(_make_alarm(alarm_id=old_id, created_at=now - timedelta(days=5)))
        session.add(_make_alarm(alarm_id=new_id, created_at=now))
        await session.commit()

    app = create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            cutoff = (now - timedelta(days=1)).isoformat()
            resp = await client.get(
                "/v1/alarms",
                params={"created_after": cutoff},
                headers=HEADERS,
            )

    assert resp.status_code == 200
    ids = [a["id"] for a in resp.json()]
    assert str(new_id) in ids
    assert str(old_id) not in ids


async def test_list_alarms_created_before(engine, sessionmaker, seeded_db, fake_redis, settings):
    """GET /v1/alarms?created_before=... filters alarms."""
    settings.admin_api_key = ADMIN_KEY
    now = datetime.now(UTC)

    old_id = uuid.uuid4()
    new_id = uuid.uuid4()

    async with sessionmaker() as session:
        session.add(_make_alarm(alarm_id=old_id, created_at=now - timedelta(days=5)))
        session.add(_make_alarm(alarm_id=new_id, created_at=now))
        await session.commit()

    app = create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            cutoff = (now - timedelta(days=1)).isoformat()
            resp = await client.get(
                "/v1/alarms",
                params={"created_before": cutoff},
                headers=HEADERS,
            )

    assert resp.status_code == 200
    ids = [a["id"] for a in resp.json()]
    assert str(old_id) in ids
    assert str(new_id) not in ids


async def test_list_alarms_person_id_filter(engine, sessionmaker, seeded_db, fake_redis, settings):
    """GET /v1/alarms?person_id=... filters by person."""
    settings.admin_api_key = ADMIN_KEY

    target_id = uuid.uuid4()
    other_id = uuid.uuid4()

    async with sessionmaker() as session:
        session.add(_make_alarm(alarm_id=target_id, person_id="ma-012"))
        session.add(_make_alarm(alarm_id=other_id, person_id="ma-999"))
        await session.commit()

    app = create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/v1/alarms",
                params={"person_id": "ma-012"},
                headers=HEADERS,
            )

    assert resp.status_code == 200
    ids = [a["id"] for a in resp.json()]
    assert str(target_id) in ids
    assert str(other_id) not in ids


async def test_list_alarms_severity_filter(engine, sessionmaker, seeded_db, fake_redis, settings):
    """GET /v1/alarms?severity=... filters by severity."""
    settings.admin_api_key = ADMIN_KEY

    p0_id = uuid.uuid4()
    p1_id = uuid.uuid4()

    async with sessionmaker() as session:
        session.add(_make_alarm(alarm_id=p0_id, severity="P0"))
        session.add(_make_alarm(alarm_id=p1_id, severity="P1"))
        await session.commit()

    app = create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/v1/alarms",
                params={"severity": "P0"},
                headers=HEADERS,
            )

    assert resp.status_code == 200
    ids = [a["id"] for a in resp.json()]
    assert str(p0_id) in ids
    assert str(p1_id) not in ids


async def test_list_alarms_room_id_filter(engine, sessionmaker, seeded_db, fake_redis, settings):
    """GET /v1/alarms?room_id=... filters by room."""
    settings.admin_api_key = ADMIN_KEY

    target_id = uuid.uuid4()
    other_id = uuid.uuid4()

    async with sessionmaker() as session:
        session.add(_make_alarm(alarm_id=target_id, room_id="bg-1.23"))
        session.add(_make_alarm(alarm_id=other_id, room_id="bg-2.01"))
        await session.commit()

    app = create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/v1/alarms",
                params={"room_id": "bg-1.23"},
                headers=HEADERS,
            )

    assert resp.status_code == 200
    ids = [a["id"] for a in resp.json()]
    assert str(target_id) in ids
    assert str(other_id) not in ids


async def test_list_alarms_source_filter(engine, sessionmaker, seeded_db, fake_redis, settings):
    """GET /v1/alarms?source=... filters by source."""
    settings.admin_api_key = ADMIN_KEY

    target_id = uuid.uuid4()
    other_id = uuid.uuid4()

    async with sessionmaker() as session:
        session.add(_make_alarm(alarm_id=target_id, source="yealink"))
        session.add(_make_alarm(alarm_id=other_id, source="manual"))
        await session.commit()

    app = create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/v1/alarms",
                params={"source": "yealink"},
                headers=HEADERS,
            )

    assert resp.status_code == 200
    ids = [a["id"] for a in resp.json()]
    assert str(target_id) in ids
    assert str(other_id) not in ids


async def test_export_csv_empty(engine, seeded_db, fake_redis, settings):
    """CSV export with no matching alarms returns empty CSV."""
    settings.admin_api_key = ADMIN_KEY

    app = create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/v1/alarms/export",
                params={"format": "csv", "person_id": "nonexistent"},
                headers=HEADERS,
            )

    assert resp.status_code == 200
    assert "text/csv" in resp.headers["content-type"]
    # Empty CSV has no rows
    assert resp.text.strip() == ""


async def test_export_json(engine, sessionmaker, seeded_db, fake_redis, settings):
    """JSON export returns valid JSON array."""
    settings.admin_api_key = ADMIN_KEY

    async with sessionmaker() as session:
        session.add(_make_alarm())
        await session.commit()

    app = create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/v1/alarms/export",
                params={"format": "json"},
                headers=HEADERS,
            )

    assert resp.status_code == 200
    assert "application/json" in resp.headers["content-type"]
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) >= 1


async def test_export_csv_with_alarms(engine, sessionmaker, seeded_db, fake_redis, settings):
    """CSV export with alarms returns header + data rows."""
    settings.admin_api_key = ADMIN_KEY

    async with sessionmaker() as session:
        session.add(_make_alarm())
        await session.commit()

    app = create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/v1/alarms/export",
                params={"format": "csv"},
                headers=HEADERS,
            )

    assert resp.status_code == 200
    lines = resp.text.strip().split("\n")
    assert len(lines) >= 2  # header + at least one data row
    assert "id" in lines[0]
    assert "status" in lines[0]


async def test_alarm_stats(engine, sessionmaker, seeded_db, fake_redis, settings):
    """GET /v1/alarms/stats returns counts by status and severity."""
    settings.admin_api_key = ADMIN_KEY

    async with sessionmaker() as session:
        session.add(_make_alarm(severity="P0"))
        session.add(_make_alarm(severity="P1"))
        await session.commit()

    app = create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/v1/alarms/stats", headers=HEADERS)

    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] >= 2
    assert "by_status" in body
    assert "by_severity" in body


async def test_list_alarms_sort_asc(engine, sessionmaker, seeded_db, fake_redis, settings):
    """GET /v1/alarms?sort_order=asc exercises ascending sort."""
    settings.admin_api_key = ADMIN_KEY
    now = datetime.now(UTC)

    async with sessionmaker() as session:
        session.add(_make_alarm(created_at=now - timedelta(minutes=2)))
        session.add(_make_alarm(created_at=now))
        await session.commit()

    app = create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/v1/alarms",
                params={"sort_order": "asc"},
                headers=HEADERS,
            )

    assert resp.status_code == 200
    data = resp.json()
    assert len(data) >= 2
    # Oldest should come first in ASC order
    assert data[0]["created_at"] <= data[1]["created_at"]


async def test_cursor_pagination_asc(engine, sessionmaker, seeded_db, fake_redis, settings):
    """Cursor pagination works with ascending sort."""
    settings.admin_api_key = ADMIN_KEY
    now = datetime.now(UTC)

    ids = [uuid.uuid4() for _ in range(3)]
    async with sessionmaker() as session:
        for i, aid in enumerate(ids):
            session.add(_make_alarm(alarm_id=aid, created_at=now + timedelta(seconds=i)))
        await session.commit()

    app = create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/v1/alarms",
                params={"limit": 2, "sort_order": "asc"},
                headers=HEADERS,
            )
            assert resp.status_code == 200
            cursor = resp.headers.get("X-Next-Cursor")
            if cursor:
                resp2 = await client.get(
                    "/v1/alarms",
                    params={"limit": 2, "sort_order": "asc", "cursor": cursor},
                    headers=HEADERS,
                )
                assert resp2.status_code == 200


# ---------------------------------------------------------------------------
# health.py - liveness and readiness
# ---------------------------------------------------------------------------


async def test_healthz_liveness(engine, seeded_db, fake_redis, settings):
    """GET /healthz returns basic liveness."""
    app = create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/healthz")

    assert resp.status_code == 200
    assert resp.json() == {"ok": "true"}


async def test_readyz_with_redis_ping(engine, seeded_db, settings):
    """Readyz uses redis.ping() when available."""

    class PingableRedis:
        async def ping(self):
            return True

    app = create_app(settings=settings, injected_engine=engine, injected_redis=PingableRedis())
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/readyz")

    assert resp.status_code == 200
    assert resp.json()["ok"] == "true"
    assert resp.json()["redis"] == "ok"
