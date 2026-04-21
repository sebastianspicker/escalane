"""Tests for alarm_operations.py and alarms.py routes."""

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

ADMIN_HEADERS = {"X-Admin-Key": "dev-admin-key"}


def _make_alarm(*, index: int, now: datetime, **overrides) -> Alarm:
    """Helper to create an Alarm with sensible defaults."""
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
        "ack_token": f"query-test-{index}-{uuid.uuid4().hex[:8]}",
        "created_at": now - timedelta(minutes=index),
        "meta": {},
    }
    defaults.update(overrides)
    return Alarm(**defaults)


async def test_list_alarms_sort_by_created_at_asc(
    engine, sessionmaker, seeded_db, fake_redis, settings
):
    """List alarms sorted by created_at ascending."""
    settings.admin_api_key = "dev-admin-key"
    now = datetime.now(UTC)

    ids = []
    async with sessionmaker() as session:
        for i in range(3):
            alarm = _make_alarm(index=i, now=now)
            ids.append(alarm.id)
            session.add(alarm)
        await session.commit()

    app = create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                "/v1/alarms",
                params={"sort_by": "created_at", "sort_order": "asc"},
                headers=ADMIN_HEADERS,
            )

    assert response.status_code == 200
    data = response.json()
    assert len(data) >= 3
    timestamps = [item["created_at"] for item in data]
    assert timestamps == sorted(timestamps)


async def test_list_alarms_cursor_pagination(engine, sessionmaker, seeded_db, fake_redis, settings):
    """List alarms with cursor pagination returns X-Next-Cursor header."""
    settings.admin_api_key = "dev-admin-key"
    now = datetime.now(UTC)

    async with sessionmaker() as session:
        for i in range(5):
            session.add(_make_alarm(index=i, now=now))
        await session.commit()

    app = create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            page1 = await client.get("/v1/alarms", params={"limit": 2}, headers=ADMIN_HEADERS)
            assert page1.status_code == 200
            assert len(page1.json()) == 2
            assert "X-Next-Cursor" in page1.headers

            cursor = page1.headers["X-Next-Cursor"]
            page2 = await client.get(
                "/v1/alarms",
                params={"limit": 2, "cursor": cursor},
                headers=ADMIN_HEADERS,
            )
            assert page2.status_code == 200
            assert len(page2.json()) >= 1

            # Verify no overlap between pages
            page1_ids = {item["id"] for item in page1.json()}
            page2_ids = {item["id"] for item in page2.json()}
            assert page1_ids.isdisjoint(page2_ids)


async def test_list_alarms_status_filter(engine, sessionmaker, seeded_db, fake_redis, settings):
    """List alarms filtered by status."""
    settings.admin_api_key = "dev-admin-key"
    now = datetime.now(UTC)

    async with sessionmaker() as session:
        session.add(_make_alarm(index=0, now=now, status=AlarmStatus.TRIGGERED))
        session.add(
            _make_alarm(
                index=1,
                now=now,
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
            response = await client.get(
                "/v1/alarms",
                params={"status": "resolved"},
                headers=ADMIN_HEADERS,
            )

    assert response.status_code == 200
    data = response.json()
    assert all(item["status"] == "resolved" for item in data)
    assert len(data) >= 1


async def test_export_alarms_csv(engine, sessionmaker, seeded_db, fake_redis, settings):
    """Export alarms as CSV with Content-Disposition header."""
    settings.admin_api_key = "dev-admin-key"
    now = datetime.now(UTC)

    async with sessionmaker() as session:
        session.add(_make_alarm(index=0, now=now))
        await session.commit()

    app = create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                "/v1/alarms/export",
                params={"format": "csv"},
                headers=ADMIN_HEADERS,
            )

    assert response.status_code == 200
    assert "text/csv" in response.headers["content-type"]
    assert "content-disposition" in response.headers
    assert "attachment" in response.headers["content-disposition"]
    assert ".csv" in response.headers["content-disposition"]
    # Verify CSV has header row
    lines = response.text.strip().split("\n")
    assert len(lines) >= 2  # header + at least one data row
    assert "id" in lines[0]
    assert "status" in lines[0]


async def test_export_alarms_json(engine, sessionmaker, seeded_db, fake_redis, settings):
    """Export alarms as JSON."""
    settings.admin_api_key = "dev-admin-key"
    now = datetime.now(UTC)

    async with sessionmaker() as session:
        session.add(_make_alarm(index=0, now=now))
        await session.commit()

    app = create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get(
                "/v1/alarms/export",
                params={"format": "json"},
                headers=ADMIN_HEADERS,
            )

    assert response.status_code == 200
    assert "application/json" in response.headers["content-type"]
    data = response.json()
    assert isinstance(data, list)
    assert len(data) >= 1
    assert "id" in data[0]
    assert "status" in data[0]


async def test_alarm_stats_structure(engine, sessionmaker, seeded_db, fake_redis, settings):
    """Alarm stats returns total, by_status, and by_severity keys."""
    settings.admin_api_key = "dev-admin-key"
    now = datetime.now(UTC)

    async with sessionmaker() as session:
        session.add(_make_alarm(index=0, now=now, severity="P0"))
        session.add(
            _make_alarm(
                index=1,
                now=now,
                severity="P1",
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
            response = await client.get("/v1/alarms/stats", headers=ADMIN_HEADERS)

    assert response.status_code == 200
    data = response.json()
    assert "total" in data
    assert "by_status" in data
    assert "by_severity" in data
    assert data["total"] >= 2
    assert isinstance(data["by_status"], dict)
    assert isinstance(data["by_severity"], dict)


async def test_patch_alarm_severity(engine, sessionmaker, seeded_db, fake_redis, settings):
    """Patch alarm severity updates the alarm."""
    settings.admin_api_key = "dev-admin-key"
    now = datetime.now(UTC)
    alarm = _make_alarm(index=0, now=now, severity="P0")

    async with sessionmaker() as session:
        session.add(alarm)
        await session.commit()

    app = create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.patch(
                f"/v1/alarms/{alarm.id}",
                json={"severity": "P1"},
                headers=ADMIN_HEADERS,
            )

    assert response.status_code == 200
    assert response.json()["severity"] == "P1"


async def test_single_alarm_resolve(engine, seeded_db, fake_redis, settings):
    """Resolve a single alarm via API."""
    settings.admin_api_key = "dev-admin-key"
    app = create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            alarm_id = await _trigger_alarm(client)
            response = await client.post(
                f"/v1/alarms/{alarm_id}/resolve",
                json={"actor": "TestOps", "note": "resolved in test"},
                headers=ADMIN_HEADERS,
            )

    assert response.status_code == 204


async def test_single_alarm_cancel(engine, seeded_db, fake_redis, settings):
    """Cancel a single alarm via API."""
    settings.admin_api_key = "dev-admin-key"
    app = create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            alarm_id = await _trigger_alarm(client)
            response = await client.post(
                f"/v1/alarms/{alarm_id}/cancel",
                json={"actor": "TestOps", "note": "cancelled in test"},
                headers=ADMIN_HEADERS,
            )

    assert response.status_code == 204
