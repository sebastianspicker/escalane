"""Tests for alarm_operations.py and alarms.py routes."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from escalane.contracts.alarms import AlarmStatus
from escalane.persistence.models import Alarm
from tests.support.api_test_helpers import app_client
from tests.support.api_test_helpers import make_alarm as _base_alarm
from tests.support.assertions import expect
from tests.support.helpers import trigger_alarm as _trigger_alarm

pytestmark = [pytest.mark.integration]

ADMIN_HEADERS = {"X-Admin-Key": "dev-admin-key"}


def _make_alarm(*, index: int, now: datetime, **overrides) -> Alarm:
    """Helper to create an Alarm with sensible defaults."""
    overrides.setdefault("ack_token", f"query-test-{index}-{uuid.uuid4().hex[:8]}")
    overrides.setdefault("created_at", now - timedelta(minutes=index))
    return _base_alarm(**overrides)


async def _export_alarm_response(*, sessionmaker, settings, engine, fake_redis, export_format: str):
    """Seed one alarm and return its export response in the requested format."""
    settings.admin_api_key = "dev-admin-key"
    now = datetime.now(UTC)
    async with sessionmaker() as session:
        session.add(_make_alarm(index=0, now=now))
        await session.commit()

    async with app_client(settings=settings, engine=engine, redis=fake_redis) as client:
        return await client.get(
            "/v1/alarms/export", params={"format": export_format}, headers=ADMIN_HEADERS
        )


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

    async with app_client(settings=settings, engine=engine, redis=fake_redis) as client:
        response = await client.get(
            "/v1/alarms",
            params={"sort_by": "created_at", "sort_order": "asc"},
            headers=ADMIN_HEADERS,
        )

    expect(response.status_code == 200)
    data = response.json()
    expect(len(data) >= 3)
    timestamps = [item["created_at"] for item in data]
    expect(timestamps == sorted(timestamps))


async def test_list_alarms_cursor_pagination(engine, sessionmaker, seeded_db, fake_redis, settings):
    """List alarms with cursor pagination returns X-Next-Cursor header."""
    settings.admin_api_key = "dev-admin-key"
    now = datetime.now(UTC)

    async with sessionmaker() as session:
        for i in range(5):
            session.add(_make_alarm(index=i, now=now))
        await session.commit()

    async with app_client(settings=settings, engine=engine, redis=fake_redis) as client:
        page1 = await client.get("/v1/alarms", params={"limit": 2}, headers=ADMIN_HEADERS)
        expect(page1.status_code == 200)
        expect(len(page1.json()) == 2)
        expect("X-Next-Cursor" in page1.headers)
        cursor = page1.headers["X-Next-Cursor"]
        page2 = await client.get(
            "/v1/alarms", params={"limit": 2, "cursor": cursor}, headers=ADMIN_HEADERS
        )
        expect(page2.status_code == 200)
        expect(len(page2.json()) >= 1)
        page1_ids = {item["id"] for item in page1.json()}
        page2_ids = {item["id"] for item in page2.json()}
        expect(page1_ids.isdisjoint(page2_ids))


async def test_list_alarms_ignores_cursor_outside_active_filters(
    engine, sessionmaker, seeded_db, fake_redis, settings
):
    """A cursor from another result set must not skip rows in the active result set."""
    settings.admin_api_key = "dev-admin-key"
    now = datetime.now(UTC)
    outside = _make_alarm(index=0, now=now, source="outside", severity="P1")
    expected = [
        _make_alarm(index=1, now=now, source="inside", severity="P0"),
        _make_alarm(index=2, now=now, source="inside", severity="P2"),
    ]

    async with sessionmaker() as session:
        session.add_all([outside, *expected])
        await session.commit()

    async with app_client(settings=settings, engine=engine, redis=fake_redis) as client:
        response = await client.get(
            "/v1/alarms",
            params={
                "source": "inside",
                "sort_by": "severity",
                "sort_order": "asc",
                "cursor": str(outside.id),
            },
            headers=ADMIN_HEADERS,
        )

    expect(response.status_code == 200)
    expect([item["id"] for item in response.json()] == [str(alarm.id) for alarm in expected])


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

    async with app_client(settings=settings, engine=engine, redis=fake_redis) as client:
        response = await client.get(
            "/v1/alarms", params={"status": "resolved"}, headers=ADMIN_HEADERS
        )

    expect(response.status_code == 200)
    data = response.json()
    expect(all(item["status"] == "resolved" for item in data))
    expect(len(data) >= 1)


async def test_export_alarms_csv(engine, sessionmaker, seeded_db, fake_redis, settings):
    """Export alarms as CSV with Content-Disposition header."""
    response = await _export_alarm_response(
        sessionmaker=sessionmaker,
        settings=settings,
        engine=engine,
        fake_redis=fake_redis,
        export_format="csv",
    )

    expect(response.status_code == 200)
    expect("text/csv" in response.headers["content-type"])
    expect("content-disposition" in response.headers)
    expect("attachment" in response.headers["content-disposition"])
    expect(".csv" in response.headers["content-disposition"])
    # Verify CSV has header row
    lines = response.text.strip().split("\n")
    expect(len(lines) >= 2)  # header + at least one data row
    expect("id" in lines[0])
    expect("status" in lines[0])


async def test_export_alarms_json(engine, sessionmaker, seeded_db, fake_redis, settings):
    """Export alarms as JSON."""
    response = await _export_alarm_response(
        sessionmaker=sessionmaker,
        settings=settings,
        engine=engine,
        fake_redis=fake_redis,
        export_format="json",
    )

    expect(response.status_code == 200)
    expect("application/json" in response.headers["content-type"])
    data = response.json()
    expect(isinstance(data, list))
    expect(len(data) >= 1)
    expect("id" in data[0])
    expect("status" in data[0])


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

    async with app_client(settings=settings, engine=engine, redis=fake_redis) as client:
        response = await client.get("/v1/alarms/stats", headers=ADMIN_HEADERS)

    expect(response.status_code == 200)
    data = response.json()
    expect("total" in data)
    expect("by_status" in data)
    expect("by_severity" in data)
    expect(data["total"] >= 2)
    expect(isinstance(data["by_status"], dict))
    expect(isinstance(data["by_severity"], dict))


async def test_patch_alarm_severity(engine, sessionmaker, seeded_db, fake_redis, settings):
    """Patch alarm severity updates the alarm."""
    settings.admin_api_key = "dev-admin-key"
    now = datetime.now(UTC)
    alarm = _make_alarm(index=0, now=now, severity="P0")

    async with sessionmaker() as session:
        session.add(alarm)
        await session.commit()

    async with app_client(settings=settings, engine=engine, redis=fake_redis) as client:
        response = await client.patch(
            f"/v1/alarms/{alarm.id}", json={"severity": "P1"}, headers=ADMIN_HEADERS
        )

    expect(response.status_code == 200)
    expect(response.json()["severity"] == "P1")


async def test_single_alarm_resolve(engine, seeded_db, fake_redis, settings):
    """Resolve a single alarm via API."""
    settings.admin_api_key = "dev-admin-key"
    async with app_client(settings=settings, engine=engine, redis=fake_redis) as client:
        alarm_id = await _trigger_alarm(client)
        response = await client.post(
            f"/v1/alarms/{alarm_id}/resolve",
            json={"actor": "TestOps", "note": "resolved in test"},
            headers=ADMIN_HEADERS,
        )

    expect(response.status_code == 204)


async def test_single_alarm_cancel(engine, seeded_db, fake_redis, settings):
    """Cancel a single alarm via API."""
    settings.admin_api_key = "dev-admin-key"
    async with app_client(settings=settings, engine=engine, redis=fake_redis) as client:
        alarm_id = await _trigger_alarm(client)
        response = await client.post(
            f"/v1/alarms/{alarm_id}/cancel",
            json={"actor": "TestOps", "note": "cancelled in test"},
            headers=ADMIN_HEADERS,
        )

    expect(response.status_code == 204)
