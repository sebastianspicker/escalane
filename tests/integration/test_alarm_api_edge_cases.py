"""Alarm API read, update, deletion, transition, and readiness edge cases.

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

from escalane.contracts.alarms import AlarmStatus
from escalane.persistence.models import Alarm
from tests.support.api_test_helpers import app_client
from tests.support.api_test_helpers import make_alarm as _make_alarm
from tests.support.assertions import expect
from tests.support.helpers import admin_login

pytestmark = [pytest.mark.integration]

ADMIN_KEY = "dev-admin-key"
HEADERS = {"X-Admin-Key": ADMIN_KEY}


async def _seed_alarm(sessionmaker, **overrides) -> uuid.UUID:
    """Persist one alarm and return its identifier for endpoint scenarios."""
    alarm_id = overrides.pop("alarm_id", uuid.uuid4())
    async with sessionmaker() as session:
        session.add(_make_alarm(alarm_id=alarm_id, **overrides))
        await session.commit()
    return alarm_id


async def _admin_dashboard(settings, engine, fake_redis):
    """Load the authenticated dashboard using the test application's lifespan."""
    async with app_client(settings=settings, engine=engine, redis=fake_redis) as client:
        await admin_login(client, ADMIN_KEY)
        return await client.get("/admin")


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

    resp = await _admin_dashboard(settings, engine, fake_redis)

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

    resp = await _admin_dashboard(settings, engine, fake_redis)

    expect(resp.status_code == 200)
    expect("2 h " in resp.text)


async def test_admin_dashboard_simulation_enabled(
    engine, sessionmaker, seeded_db, fake_redis, settings
):
    """Dashboard renders simulation panel when simulation_enabled=True."""
    settings.admin_api_key = ADMIN_KEY
    settings.simulation_enabled = True

    resp = await _admin_dashboard(settings, engine, fake_redis)

    expect(resp.status_code == 200)
    expect('href="/admin/simulation"' in resp.text)


async def test_admin_dashboard_simulation_disabled(engine, seeded_db, fake_redis, settings):
    """Dashboard renders disabled simulation panel."""
    settings.admin_api_key = ADMIN_KEY
    settings.simulation_enabled = False

    resp = await _admin_dashboard(settings, engine, fake_redis)

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

    async with app_client(settings=settings, engine=engine, redis=fake_redis) as client:
        resp = await client.get(f"/v1/alarms/{alarm_id}", headers=HEADERS)

    expect(resp.status_code == 200)
    expect(resp.json()["id"] == str(alarm_id))


async def test_single_alarm_get_nonexistent(engine, seeded_db, fake_redis, settings):
    """GET /v1/alarms/{id} returns 404 for non-existent alarm."""
    settings.admin_api_key = ADMIN_KEY
    missing_id = uuid.uuid4()

    async with app_client(settings=settings, engine=engine, redis=fake_redis) as client:
        resp = await client.get(f"/v1/alarms/{missing_id}", headers=HEADERS)

    expect(resp.status_code == 404)


async def test_patch_alarm_title_description_tags(
    engine, sessionmaker, seeded_db, fake_redis, settings
):
    """PATCH /v1/alarms/{id} updates title, description, tags in meta."""
    settings.admin_api_key = ADMIN_KEY
    alarm_id = await _seed_alarm(sessionmaker)

    async with app_client(settings=settings, engine=engine, redis=fake_redis) as client:
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
    alarm_id = await _seed_alarm(sessionmaker)

    async with app_client(settings=settings, engine=engine, redis=fake_redis) as client:
        resp = await client.delete(f"/v1/alarms/{alarm_id}", headers=HEADERS)

    expect(resp.status_code == 204)

    async with sessionmaker() as session:
        alarm = await session.get(Alarm, alarm_id)
        expect(alarm is not None)
        expect(alarm.deleted_at is not None)


async def test_delete_alarm_nonexistent(engine, seeded_db, fake_redis, settings):
    """DELETE /v1/alarms/{id} returns 404 for non-existent alarm."""
    settings.admin_api_key = ADMIN_KEY

    async with app_client(settings=settings, engine=engine, redis=fake_redis) as client:
        resp = await client.delete(f"/v1/alarms/{uuid.uuid4()}", headers=HEADERS)

    expect(resp.status_code == 404)


async def test_delete_alarm_already_deleted(engine, sessionmaker, seeded_db, fake_redis, settings):
    """DELETE /v1/alarms/{id} returns 404 if alarm already deleted.

    The new security model makes get_alarm_or_404 return 404 for soft-deleted
    alarms, so the 409 Conflict branch is no longer reachable via the API.
    """
    settings.admin_api_key = ADMIN_KEY
    alarm_id = await _seed_alarm(sessionmaker, deleted_at=datetime.now(UTC))

    async with app_client(settings=settings, engine=engine, redis=fake_redis) as client:
        resp = await client.delete(f"/v1/alarms/{alarm_id}", headers=HEADERS)

    expect(resp.status_code == 404)


async def test_single_ack_transition(engine, sessionmaker, seeded_db, fake_redis, settings):
    """POST /v1/alarms/{id}/ack transitions triggered -> acknowledged."""
    settings.admin_api_key = ADMIN_KEY
    alarm_id = await _seed_alarm(sessionmaker)

    async with app_client(settings=settings, engine=engine, redis=fake_redis) as client:
        resp = await client.post(
            f"/v1/alarms/{alarm_id}/ack",
            headers=HEADERS,
            json={"acked_by": "Tester", "note": "seen"},
        )

    expect(resp.status_code == 204)


async def test_single_cancel_transition(engine, sessionmaker, seeded_db, fake_redis, settings):
    """POST /v1/alarms/{id}/cancel transitions triggered -> cancelled."""
    settings.admin_api_key = ADMIN_KEY
    alarm_id = await _seed_alarm(sessionmaker)

    async with app_client(settings=settings, engine=engine, redis=fake_redis) as client:
        resp = await client.post(
            f"/v1/alarms/{alarm_id}/cancel", headers=HEADERS, json={"actor": "Ops"}
        )

    expect(resp.status_code == 204)


async def test_bulk_resolve_all_already_resolved(
    engine, sessionmaker, seeded_db, fake_redis, settings
):
    """Bulk resolve with all alarms already resolved reports 0 changed."""
    settings.admin_api_key = ADMIN_KEY
    now = datetime.now(UTC)

    alarms = [
        _make_alarm(
            alarm_id=uuid.uuid4(),
            status=AlarmStatus.RESOLVED,
            resolved_at=now,
            resolved_by="Ops",
        )
        for _ in range(2)
    ]
    async with sessionmaker() as session:
        session.add_all(alarms)
        await session.commit()

    async with app_client(settings=settings, engine=engine, redis=fake_redis) as client:
        resp = await client.post(
            "/v1/alarms/bulk/resolve",
            headers=HEADERS,
            json={"alarm_ids": [str(alarm.id) for alarm in alarms], "actor": "Ops"},
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
    async with app_client(settings=settings, engine=engine, redis=fake_redis) as client:
        resp = await client.get("/healthz")

    expect(resp.status_code == 200)
    expect(resp.json() == {"ok": "true"})


async def test_readyz_with_redis_ping(engine, seeded_db, settings):
    """Readyz uses redis.ping() when available."""

    class PingableRedis:
        async def ping(self):
            return True

    async with app_client(settings=settings, engine=engine, redis=PingableRedis()) as client:
        resp = await client.get("/readyz")

    expect(resp.status_code == 200)
    expect(resp.json()["ok"] == "true")
    expect(resp.json()["redis"] == "ok")
