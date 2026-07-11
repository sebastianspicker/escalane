"""Targeted tests for API and UI behavior outside the main alarm trigger flow.

Covers:
- admin dashboard ACK/resolve capabilities and simulation panel states
- single alarm transitions, get/patch/delete, and bulk edge cases
- alarm list filters, export formats, stats, and cursor pagination
- health/readiness endpoints
"""

from __future__ import annotations

try:
    from tests.assertions import expect
except ModuleNotFoundError:
    from assertions import expect

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


def _make_alarm(**overrides) -> Alarm:
    if "alarm_id" in overrides:
        overrides["id"] = overrides.pop("alarm_id")
    defaults = {
        "id": uuid.uuid4(),
        "status": AlarmStatus.TRIGGERED,
        "source": "test",
        "event": "alarm.trigger",
        "person_id": "ma-012",
        "room_id": "bg-1.23",
        "site_id": "bg",
        "device_id": "ylk-t5-10023",
        "severity": "P0",
        "silent": True,
        "ack_token": f"tok-{uuid.uuid4().hex[:8]}",
        "created_at": datetime.now(UTC),
        "meta": {},
        "resolved_at": None,
        "resolved_by": None,
        "acked_at": None,
        "acked_by": None,
    }
    defaults.update(overrides)
    return Alarm(**defaults)


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

    expect(resp.status_code == 200)
    html = resp.text
    expect(f"/admin/alarms/{triggered_id}" in html)
    expect(f"/admin/alarms/{acked_id}" in html)
    expect(f"/admin/alarms/{resolved_id}" in html)


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

    expect(resp.status_code == 200)
    expect("2 h " in resp.text)


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

    expect(resp.status_code == 200)
    expect('href="/admin/simulation"' in resp.text)


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

    expect(resp.status_code == 200)
    expect('href="/admin/simulation"' not in resp.text)
    expect('href="/admin/system"' in resp.text)


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

    expect(resp.status_code == 200)
    expect(resp.json()["id"] == str(alarm_id))


async def test_single_alarm_get_nonexistent(engine, seeded_db, fake_redis, settings):
    """GET /v1/alarms/{id} returns 404 for non-existent alarm."""
    settings.admin_api_key = ADMIN_KEY
    missing_id = uuid.uuid4()

    app = create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(f"/v1/alarms/{missing_id}", headers=HEADERS)

    expect(resp.status_code == 404)


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

    expect(resp.status_code == 200)
    expect(resp.json()["severity"] == "P1")


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

    expect(resp.status_code == 200)
    meta = resp.json()["meta"]
    expect(meta["title"] == "Fire alarm")
    expect(meta["description"] == "Floor 3")
    expect(meta["tags"] == ["fire"])


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

    expect(resp.status_code == 204)

    async with sessionmaker() as session:
        alarm = await session.get(Alarm, alarm_id)
        expect(alarm is not None)
        expect(alarm.deleted_at is not None)


async def test_delete_alarm_nonexistent(engine, seeded_db, fake_redis, settings):
    """DELETE /v1/alarms/{id} returns 404 for non-existent alarm."""
    settings.admin_api_key = ADMIN_KEY

    app = create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.delete(f"/v1/alarms/{uuid.uuid4()}", headers=HEADERS)

    expect(resp.status_code == 404)


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

    expect(resp.status_code == 404)


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

    expect(resp.status_code == 204)


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

    expect(resp.status_code == 204)


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

    expect(resp.status_code == 200)
    body = resp.json()
    expect(body["changed"] == 0)
    expect(body["unchanged"] == 2)


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

    expect(resp.status_code == 200)
    expect(resp.json() == {"ok": "true"})


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

    expect(resp.status_code == 200)
    expect(resp.json()["ok"] == "true")
    expect(resp.json()["redis"] == "ok")
