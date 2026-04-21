"""Integration tests for alarm lifecycle, export, stats, notes, and deletion."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient

from alarm_broker.api.main import create_app
from alarm_broker.db.models import Alarm, AlarmStatus

try:
    from tests.helpers import trigger_alarm as _trigger_alarm
except ModuleNotFoundError:
    from helpers import trigger_alarm as _trigger_alarm

pytestmark = [pytest.mark.integration]


# ── Helper ───────────────────────────────────────────────────────────


def _admin_headers(key: str = "dev-admin-key") -> dict[str, str]:
    return {"X-Admin-Key": key}


# ── Full alarm lifecycle: trigger → ack → resolve ────────────────────


async def test_full_alarm_lifecycle_trigger_ack_resolve(
    engine, sessionmaker, seeded_db, fake_redis, settings
):
    """An alarm can be triggered, acknowledged, and then resolved in sequence."""
    settings.admin_api_key = "dev-admin-key"
    app = create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # 1. Trigger
            alarm_id = await _trigger_alarm(client)

            # Verify triggered state
            get_resp = await client.get(
                f"/v1/alarms/{alarm_id}",
                headers=_admin_headers(),
            )
            assert get_resp.status_code == 200
            assert get_resp.json()["status"] == "triggered"

            # 2. Acknowledge
            ack_resp = await client.post(
                f"/v1/alarms/{alarm_id}/ack",
                headers=_admin_headers(),
                json={"acked_by": "Nurse A", "note": "On my way"},
            )
            assert ack_resp.status_code == 204

            get_resp_ack = await client.get(
                f"/v1/alarms/{alarm_id}",
                headers=_admin_headers(),
            )
            assert get_resp_ack.status_code == 200
            data_ack = get_resp_ack.json()
            assert data_ack["status"] == "acknowledged"
            assert data_ack["acked_by"] == "Nurse A"
            assert data_ack["acked_at"] is not None

            # 3. Resolve
            resolve_resp = await client.post(
                f"/v1/alarms/{alarm_id}/resolve",
                headers=_admin_headers(),
                json={"actor": "Doctor B", "note": "Situation handled"},
            )
            assert resolve_resp.status_code == 204

            get_resp_resolved = await client.get(
                f"/v1/alarms/{alarm_id}",
                headers=_admin_headers(),
            )
            assert get_resp_resolved.status_code == 200
            data_resolved = get_resp_resolved.json()
            assert data_resolved["status"] == "resolved"
            assert data_resolved["resolved_by"] == "Doctor B"
            assert data_resolved["resolved_at"] is not None


# ── Alarm export endpoint ────────────────────────────────────────────


async def test_alarm_export_json_format(engine, sessionmaker, seeded_db, fake_redis, settings):
    """The export endpoint returns valid JSON with the expected fields."""
    settings.admin_api_key = "dev-admin-key"
    now = datetime.now(UTC)
    alarm_id = uuid.uuid4()

    async with sessionmaker() as session:
        session.add(
            Alarm(
                id=alarm_id,
                status=AlarmStatus.TRIGGERED,
                source="test",
                event="alarm.trigger",
                person_id="ma-012",
                room_id="bg-1.23",
                site_id="bg",
                device_id="ylk-t5-10023",
                severity="P0",
                silent=True,
                ack_token="export-test-token",
                created_at=now,
                meta={},
            )
        )
        await session.commit()

    app = create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                "/v1/alarms/export",
                params={"format": "json"},
                headers=_admin_headers(),
            )

    assert response.status_code == 200
    assert "application/json" in response.headers["content-type"]

    data = response.json()
    assert isinstance(data, list)
    assert len(data) >= 1

    # Check that the exported alarm has the expected structure
    exported = next((a for a in data if a["id"] == str(alarm_id)), None)
    assert exported is not None
    assert exported["status"] == "triggered"
    assert exported["source"] == "test"
    assert exported["severity"] == "P0"
    assert "created_at" in exported


async def test_alarm_export_csv_format(engine, sessionmaker, seeded_db, fake_redis, settings):
    """The export endpoint returns CSV content with a header row."""
    settings.admin_api_key = "dev-admin-key"
    now = datetime.now(UTC)

    async with sessionmaker() as session:
        session.add(
            Alarm(
                id=uuid.uuid4(),
                status=AlarmStatus.TRIGGERED,
                source="csv-test",
                event="alarm.trigger",
                person_id="ma-012",
                room_id="bg-1.23",
                site_id="bg",
                device_id="ylk-t5-10023",
                severity="P0",
                silent=True,
                ack_token="csv-export-token",
                created_at=now,
                meta={},
            )
        )
        await session.commit()

    app = create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                "/v1/alarms/export",
                params={"format": "csv"},
                headers=_admin_headers(),
            )

    assert response.status_code == 200
    assert "text/csv" in response.headers["content-type"]

    body = response.text
    lines = body.strip().split("\n")
    assert len(lines) >= 2  # header + at least one data row

    header = lines[0]
    assert "id" in header
    assert "status" in header
    assert "source" in header
    assert "severity" in header


# ── Alarm stats endpoint ─────────────────────────────────────────────


async def test_alarm_stats_returns_correct_structure(
    engine, sessionmaker, seeded_db, fake_redis, settings
):
    """The stats endpoint returns total, by_status, and by_severity."""
    settings.admin_api_key = "dev-admin-key"
    now = datetime.now(UTC)

    async with sessionmaker() as session:
        session.add(
            Alarm(
                id=uuid.uuid4(),
                status=AlarmStatus.TRIGGERED,
                source="stats-test",
                event="alarm.trigger",
                severity="P0",
                silent=True,
                ack_token="stats-1",
                created_at=now,
                meta={},
            )
        )
        session.add(
            Alarm(
                id=uuid.uuid4(),
                status=AlarmStatus.RESOLVED,
                source="stats-test",
                event="alarm.trigger",
                severity="P1",
                silent=True,
                ack_token="stats-2",
                created_at=now - timedelta(hours=1),
                resolved_at=now,
                resolved_by="auto",
                meta={},
            )
        )
        await session.commit()

    app = create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                "/v1/alarms/stats",
                headers=_admin_headers(),
            )

    assert response.status_code == 200
    data = response.json()

    assert "total" in data
    assert data["total"] >= 2

    assert "by_status" in data
    assert isinstance(data["by_status"], dict)
    assert data["by_status"].get("triggered", 0) >= 1
    assert data["by_status"].get("resolved", 0) >= 1

    assert "by_severity" in data
    assert isinstance(data["by_severity"], dict)
    assert data["by_severity"].get("P0", 0) >= 1
    assert data["by_severity"].get("P1", 0) >= 1


async def test_alarm_stats_empty_database(engine, seeded_db, fake_redis, settings):
    """Stats endpoint returns zero total when no alarms exist."""
    settings.admin_api_key = "dev-admin-key"
    app = create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                "/v1/alarms/stats",
                headers=_admin_headers(),
            )

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 0
    assert data["by_status"] == {}
    assert data["by_severity"] == {}


# ── Alarm notes creation and listing ─────────────────────────────────


async def test_alarm_notes_creation_and_listing(engine, seeded_db, fake_redis, settings):
    """Notes can be created and listed for an alarm."""
    settings.admin_api_key = "dev-admin-key"
    app = create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            alarm_id = await _trigger_alarm(client)

            # Create first note
            create_resp_1 = await client.post(
                f"/v1/alarms/{alarm_id}/notes",
                headers=_admin_headers(),
                json={"note": "First observation", "created_by": "Nurse A"},
            )
            assert create_resp_1.status_code == 201
            note_1 = create_resp_1.json()
            assert note_1["note"] == "First observation"
            assert note_1["created_by"] == "Nurse A"
            assert note_1["note_type"] == "manual"
            assert note_1["alarm_id"] == str(alarm_id)

            # Create second note
            create_resp_2 = await client.post(
                f"/v1/alarms/{alarm_id}/notes",
                headers=_admin_headers(),
                json={"note": "Second update", "created_by": "Doctor B"},
            )
            assert create_resp_2.status_code == 201

            # List notes
            list_resp = await client.get(
                f"/v1/alarms/{alarm_id}/notes",
                headers=_admin_headers(),
            )
            assert list_resp.status_code == 200
            notes = list_resp.json()
            assert len(notes) == 2
            assert notes[0]["note"] == "First observation"
            assert notes[1]["note"] == "Second update"


async def test_alarm_notes_for_nonexistent_alarm_returns_404(
    engine, seeded_db, fake_redis, settings
):
    """Creating a note for a nonexistent alarm returns 404."""
    settings.admin_api_key = "dev-admin-key"
    missing_id = uuid.uuid4()
    app = create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                f"/v1/alarms/{missing_id}/notes",
                headers=_admin_headers(),
                json={"note": "This should fail"},
            )

    assert response.status_code == 404


# ── Alarm deletion (soft delete) ─────────────────────────────────────


async def test_alarm_soft_delete(engine, sessionmaker, seeded_db, fake_redis, settings):
    """Deleting an alarm sets deleted_at and deleted_by without removing the record."""
    settings.admin_api_key = "dev-admin-key"
    app = create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            alarm_id = await _trigger_alarm(client)

            delete_resp = await client.delete(
                f"/v1/alarms/{alarm_id}",
                headers=_admin_headers(),
            )
            assert delete_resp.status_code == 204

    # Verify in DB: record still exists but deleted_at is set
    async with sessionmaker() as session:
        alarm = await session.get(Alarm, alarm_id)
        assert alarm is not None
        assert alarm.deleted_at is not None
        assert alarm.deleted_by is not None
        assert alarm.deleted_by == "admin"


async def test_alarm_soft_delete_idempotent_returns_not_found(
    engine, seeded_db, fake_redis, settings
):
    """Deleting an already-deleted alarm returns 404 Not Found.

    The new security model makes get_alarm_or_404 return 404 for soft-deleted
    alarms, so the second delete attempt is treated as if the alarm does not exist.
    """
    settings.admin_api_key = "dev-admin-key"
    app = create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            alarm_id = await _trigger_alarm(client)

            first = await client.delete(
                f"/v1/alarms/{alarm_id}",
                headers=_admin_headers(),
            )
            assert first.status_code == 204

            second = await client.delete(
                f"/v1/alarms/{alarm_id}",
                headers=_admin_headers(),
            )
            assert second.status_code == 404


async def test_alarm_delete_nonexistent_returns_404(engine, seeded_db, fake_redis, settings):
    """Deleting a nonexistent alarm returns 404."""
    settings.admin_api_key = "dev-admin-key"
    missing_id = uuid.uuid4()
    app = create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.delete(
                f"/v1/alarms/{missing_id}",
                headers=_admin_headers(),
            )

    assert response.status_code == 404
