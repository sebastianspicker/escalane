"""Alarm query filters, ordering, empty export, and cursor pagination."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from escalane.contracts.alarms import AlarmStatus
from escalane.persistence.models import Alarm
from tests.support.api_test_helpers import app_client
from tests.support.api_test_helpers import make_alarm as _make_alarm
from tests.support.assertions import expect

pytestmark = [pytest.mark.integration]

ADMIN_KEY = "dev-admin-key"
HEADERS = {"X-Admin-Key": ADMIN_KEY}


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
        session.add(_make_alarm(alarm_id=matching_id, **{filter_name: matching}))
        session.add(_make_alarm(alarm_id=other_id, **{filter_name: other}))
        await session.commit()

    async with app_client(settings=settings, engine=engine, redis=fake_redis) as client:
        response = await client.get("/v1/alarms", params={filter_name: matching}, headers=HEADERS)

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
        session.add(_make_alarm(alarm_id=old_id, created_at=now - timedelta(days=5)))
        session.add(_make_alarm(alarm_id=new_id, created_at=now))
        await session.commit()

    async with app_client(settings=settings, engine=engine, redis=fake_redis) as client:
        response = await client.get(
            "/v1/alarms",
            params={filter_name: (now - timedelta(days=1)).isoformat()},
            headers=HEADERS,
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
            headers=HEADERS,
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
        _make_alarm(
            alarm_id=uuid.UUID(int=1),
            created_at=base_time,
            status=AlarmStatus.RESOLVED,
            severity="P1",
        ),
        _make_alarm(
            alarm_id=uuid.UUID(int=2),
            created_at=base_time + timedelta(minutes=4),
            status=AlarmStatus.TRIGGERED,
            severity="P0",
        ),
        _make_alarm(
            alarm_id=uuid.UUID(int=3),
            created_at=base_time,
            status=AlarmStatus.TRIGGERED,
            severity="P2",
        ),
        _make_alarm(
            alarm_id=uuid.UUID(int=4),
            created_at=base_time + timedelta(minutes=4),
            status=AlarmStatus.CANCELLED,
            severity="P0",
        ),
        _make_alarm(
            alarm_id=uuid.UUID(int=5),
            created_at=base_time + timedelta(minutes=2),
            status=AlarmStatus.RESOLVED,
            severity="P1",
        ),
        _make_alarm(
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
            response = await client.get("/v1/alarms", params=params, headers=HEADERS)
            expect(response.status_code == 200)
            actual_ids.extend(alarm["id"] for alarm in response.json())
            cursor = response.headers.get("X-Next-Cursor")
            if cursor is None:
                break
    return actual_ids
