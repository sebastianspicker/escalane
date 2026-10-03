"""Alarm list, filter, ordering, export, stats, and cursor-pagination route tests."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from escalane.persistence.models import Alarm, AlarmStatus
from tests.support.assertions import expect
from tests.support.clients import app_client
from tests.support.clients import trigger_alarm as _trigger_alarm
from tests.support.factories import make_alarm

pytestmark = [pytest.mark.integration]

ADMIN_KEY = "dev-admin-key"
ADMIN_HEADERS = {"X-Admin-Key": ADMIN_KEY}


def _make_alarm(*, index: int, now: datetime, **overrides) -> Alarm:
    """Helper to create an Alarm with sensible defaults."""
    overrides.setdefault("ack_token", f"query-test-{index}-{uuid.uuid4().hex[:8]}")
    overrides.setdefault("created_at", now - timedelta(minutes=index))
    return make_alarm(**overrides)


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


@pytest.mark.parametrize(
    ("filter_name", "matching", "other"),
    [
        ("person_id", "ma-012", "ma-999"),
        ("severity", "P0", "P1"),
        ("room_id", "bg-1.23", "bg-2.01"),
        ("source", "yealink", "manual"),
    ],
)
async def test_list_alarms_attribute_filters(
    engine, sessionmaker, seeded_db, fake_redis, settings, filter_name, matching, other
):
    """Each supported attribute filter returns only the matching alarm."""
    settings.admin_api_key = ADMIN_KEY
    matching_id, other_id = uuid.uuid4(), uuid.uuid4()
    async with sessionmaker() as session:
        session.add(make_alarm(alarm_id=matching_id, **{filter_name: matching}))
        session.add(make_alarm(alarm_id=other_id, **{filter_name: other}))
        await session.commit()

    async with app_client(settings=settings, engine=engine, redis=fake_redis) as client:
        response = await client.get(
            "/v1/alarms", params={filter_name: matching}, headers=ADMIN_HEADERS
        )

    matching_ids = {alarm["id"] for alarm in response.json()}
    expect(response.status_code == 200)
    expect(str(matching_id) in matching_ids)
    expect(str(other_id) not in matching_ids)


@pytest.mark.parametrize(
    ("filter_name", "expected_offset"), [("created_after", 0), ("created_before", -5)]
)
async def test_list_alarms_creation_time_filters(
    engine, sessionmaker, seeded_db, fake_redis, settings, filter_name, expected_offset
):
    """Creation-time filters select the expected side of a shared cutoff."""
    settings.admin_api_key = ADMIN_KEY
    now = datetime.now(UTC)
    old_id, new_id = uuid.uuid4(), uuid.uuid4()
    async with sessionmaker() as session:
        session.add(make_alarm(alarm_id=old_id, created_at=now - timedelta(days=5)))
        session.add(make_alarm(alarm_id=new_id, created_at=now))
        await session.commit()

    async with app_client(settings=settings, engine=engine, redis=fake_redis) as client:
        response = await client.get(
            "/v1/alarms",
            params={filter_name: (now - timedelta(days=1)).isoformat()},
            headers=ADMIN_HEADERS,
        )

    ids = {alarm["id"] for alarm in response.json()}
    expected_id = str(new_id if expected_offset == 0 else old_id)
    unexpected_id = str(old_id if expected_offset == 0 else new_id)
    expect(response.status_code == 200)
    expect(expected_id in ids)
    expect(unexpected_id not in ids)


async def test_export_csv_empty(engine, seeded_db, fake_redis, settings):
    """CSV export with no matching alarms returns empty CSV."""
    settings.admin_api_key = ADMIN_KEY

    async with app_client(settings=settings, engine=engine, redis=fake_redis) as client:
        resp = await client.get(
            "/v1/alarms/export",
            params={"format": "csv", "person_id": "nonexistent"},
            headers=ADMIN_HEADERS,
        )

    expect(resp.status_code == 200)
    expect("text/csv" in resp.headers["content-type"])
    expect(resp.text.strip() == "")


@pytest.mark.parametrize("sort_by", ["created_at", "status", "severity"])
@pytest.mark.parametrize("sort_order", ["asc", "desc"])
async def test_cursor_pagination_walks_each_sort_order_without_gaps(
    engine, sessionmaker, seeded_db, fake_redis, settings, sort_by, sort_order
):
    """Cursor pagination follows the selected sort column and UUID tie-breaker."""
    settings.admin_api_key = ADMIN_KEY
    alarms = _pagination_alarms()
    async with sessionmaker() as session:
        session.add_all(alarms)
        await session.commit()

    expected_ids = [
        str(alarm.id)
        for alarm in sorted(
            alarms,
            key=lambda alarm: (getattr(alarm, sort_by), str(alarm.id)),
            reverse=sort_order == "desc",
        )
    ]
    actual_ids = await _walk_alarm_pages(settings, engine, fake_redis, sort_by, sort_order)
    expect(actual_ids == expected_ids)
    expect(len(actual_ids) == len(set(actual_ids)))


def _pagination_alarms() -> list[Alarm]:
    base_time = datetime(2025, 1, 1, tzinfo=UTC)
    return [
        make_alarm(
            alarm_id=uuid.UUID(int=1),
            created_at=base_time,
            status=AlarmStatus.RESOLVED,
            severity="P1",
        ),
        make_alarm(
            alarm_id=uuid.UUID(int=2),
            created_at=base_time + timedelta(minutes=4),
            status=AlarmStatus.TRIGGERED,
            severity="P0",
        ),
        make_alarm(
            alarm_id=uuid.UUID(int=3),
            created_at=base_time,
            status=AlarmStatus.TRIGGERED,
            severity="P2",
        ),
        make_alarm(
            alarm_id=uuid.UUID(int=4),
            created_at=base_time + timedelta(minutes=4),
            status=AlarmStatus.CANCELLED,
            severity="P0",
        ),
        make_alarm(
            alarm_id=uuid.UUID(int=5),
            created_at=base_time + timedelta(minutes=2),
            status=AlarmStatus.RESOLVED,
            severity="P1",
        ),
        make_alarm(
            alarm_id=uuid.UUID(int=6),
            created_at=base_time + timedelta(minutes=2),
            status=AlarmStatus.TRIGGERED,
            severity="P2",
        ),
    ]


async def _walk_alarm_pages(
    settings, engine, fake_redis, sort_by: str, sort_order: str
) -> list[str]:
    actual_ids = []
    cursor = None
    async with app_client(settings=settings, engine=engine, redis=fake_redis) as client:
        while True:
            params = {"limit": 2, "sort_by": sort_by, "sort_order": sort_order}
            if cursor is not None:
                params["cursor"] = cursor
            response = await client.get("/v1/alarms", params=params, headers=ADMIN_HEADERS)
            expect(response.status_code == 200)
            actual_ids.extend(alarm["id"] for alarm in response.json())
            cursor = response.headers.get("X-Next-Cursor")
            if cursor is None:
                break
    return actual_ids
