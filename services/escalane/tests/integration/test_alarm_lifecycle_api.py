"""Integration tests for alarm lifecycle, export, stats, notes, and deletion."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from escalane.db.models import Alarm, AlarmStatus
from tests.api_test_helpers import app_client
from tests.api_test_helpers import make_alarm as _base_alarm
from tests.assertions import expect
from tests.constants import TEST_ADMIN_API_KEY, value_for_test
from tests.helpers import trigger_alarm as _trigger_alarm

pytestmark = [pytest.mark.integration]


# ── Helper ───────────────────────────────────────────────────────────


def _admin_headers(key: str = TEST_ADMIN_API_KEY) -> dict[str, str]:
    return {"X-Admin-Key": key}


def _make_alarm(**overrides) -> Alarm:
    """Build an integration fixture with the source used by stats assertions."""
    overrides.setdefault("source", "stats-test")
    overrides.setdefault("ack_token", value_for_test(f"integration-{uuid.uuid4().hex[:8]}"))
    return _base_alarm(**overrides)


def _expect_alarm_stats_payload(data: dict) -> None:
    expect("total" in data)
    expect(data["total"] >= 2)
    expect("by_status" in data)
    expect(isinstance(data["by_status"], dict))
    expect(data["by_status"].get("triggered", 0) >= 1)
    expect(data["by_status"].get("resolved", 0) >= 1)
    expect("by_severity" in data)
    expect(isinstance(data["by_severity"], dict))
    expect(data["by_severity"].get("P0", 0) >= 1)
    expect(data["by_severity"].get("P1", 0) >= 1)


# ── Full alarm lifecycle: trigger → ack → resolve ────────────────────


async def test_full_alarm_lifecycle_trigger_ack_resolve(
    engine, sessionmaker, seeded_db, fake_redis, settings
):
    """An alarm can be triggered, acknowledged, and then resolved in sequence."""
    settings.admin_api_key = TEST_ADMIN_API_KEY
    async with app_client(settings=settings, engine=engine, redis=fake_redis) as client:
        # 1. Trigger
        alarm_id = await _trigger_alarm(client)

        # Verify triggered state
        get_resp = await client.get(
            f"/v1/alarms/{alarm_id}",
            headers=_admin_headers(),
        )
        expect(get_resp.status_code == 200)
        expect(get_resp.json()["status"] == "triggered")

        # 2. Acknowledge
        ack_resp = await client.post(
            f"/v1/alarms/{alarm_id}/ack",
            headers=_admin_headers(),
            json={"acked_by": "Nurse A", "note": "On my way"},
        )
        expect(ack_resp.status_code == 204)

        get_resp_ack = await client.get(
            f"/v1/alarms/{alarm_id}",
            headers=_admin_headers(),
        )
        expect(get_resp_ack.status_code == 200)
        data_ack = get_resp_ack.json()
        expect(data_ack["status"] == "acknowledged")
        expect(data_ack["acked_by"] == "Nurse A")
        expect(data_ack["acked_at"] is not None)

        # 3. Resolve
        resolve_resp = await client.post(
            f"/v1/alarms/{alarm_id}/resolve",
            headers=_admin_headers(),
            json={"actor": "Doctor B", "note": "Situation handled"},
        )
        expect(resolve_resp.status_code == 204)

        get_resp_resolved = await client.get(
            f"/v1/alarms/{alarm_id}",
            headers=_admin_headers(),
        )
        expect(get_resp_resolved.status_code == 200)
        data_resolved = get_resp_resolved.json()
        expect(data_resolved["status"] == "resolved")
        expect(data_resolved["resolved_by"] == "Doctor B")
        expect(data_resolved["resolved_at"] is not None)


# ── Alarm export endpoint ────────────────────────────────────────────


async def test_alarm_export_json_format(engine, sessionmaker, seeded_db, fake_redis, settings):
    """The export endpoint returns valid JSON with the expected fields."""
    settings.admin_api_key = TEST_ADMIN_API_KEY
    now = datetime.now(UTC)
    alarm_id = uuid.uuid4()

    async with sessionmaker() as session:
        session.add(
            _base_alarm(
                alarm_id=alarm_id,
                source="test",
                ack_token=value_for_test("export"),
                created_at=now,
            )
        )
        await session.commit()

    async with app_client(settings=settings, engine=engine, redis=fake_redis) as client:
        response = await client.get(
            "/v1/alarms/export", params={"format": "json"}, headers=_admin_headers()
        )

    expect(response.status_code == 200)
    expect("application/json" in response.headers["content-type"])

    data = response.json()
    expect(isinstance(data, list))
    expect(len(data) >= 1)

    # Check that the exported alarm has the expected structure
    exported = next((a for a in data if a["id"] == str(alarm_id)), None)
    expect(exported is not None)
    expect(exported["status"] == "triggered")
    expect(exported["source"] == "test")
    expect(exported["severity"] == "P0")
    expect("created_at" in exported)


async def test_alarm_export_csv_format(engine, sessionmaker, seeded_db, fake_redis, settings):
    """The export endpoint returns CSV content with a header row."""
    settings.admin_api_key = TEST_ADMIN_API_KEY
    now = datetime.now(UTC)

    async with sessionmaker() as session:
        session.add(
            _base_alarm(
                source="csv-test",
                ack_token=value_for_test("csv-export"),
                created_at=now,
            )
        )
        await session.commit()

    async with app_client(settings=settings, engine=engine, redis=fake_redis) as client:
        response = await client.get(
            "/v1/alarms/export", params={"format": "csv"}, headers=_admin_headers()
        )

    expect(response.status_code == 200)
    expect("text/csv" in response.headers["content-type"])

    body = response.text
    lines = body.strip().split("\n")
    expect(len(lines) >= 2)  # header + at least one data row

    header = lines[0]
    expect("id" in header)
    expect("status" in header)
    expect("source" in header)
    expect("severity" in header)


# ── Alarm stats endpoint ─────────────────────────────────────────────


async def test_alarm_stats_returns_correct_structure(
    engine, sessionmaker, seeded_db, fake_redis, settings
):
    """The stats endpoint returns total, by_status, and by_severity."""
    settings.admin_api_key = TEST_ADMIN_API_KEY
    now = datetime.now(UTC)

    async with sessionmaker() as session:
        session.add(
            _make_alarm(
                ack_token=value_for_test("stats-1"),
                created_at=now,
            )
        )
        session.add(
            _make_alarm(
                status=AlarmStatus.RESOLVED,
                severity="P1",
                ack_token=value_for_test("stats-2"),
                created_at=now - timedelta(hours=1),
                resolved_at=now,
                resolved_by="auto",
            )
        )
        await session.commit()

    async with app_client(settings=settings, engine=engine, redis=fake_redis) as client:
        response = await client.get("/v1/alarms/stats", headers=_admin_headers())

    expect(response.status_code == 200)
    _expect_alarm_stats_payload(response.json())


async def test_alarm_stats_empty_database(engine, seeded_db, fake_redis, settings):
    """Stats endpoint returns zero total when no alarms exist."""
    settings.admin_api_key = TEST_ADMIN_API_KEY
    async with app_client(settings=settings, engine=engine, redis=fake_redis) as client:
        response = await client.get("/v1/alarms/stats", headers=_admin_headers())

    expect(response.status_code == 200)
    data = response.json()
    expect(data["total"] == 0)
    expect(data["by_status"] == {})
    expect(data["by_severity"] == {})


# ── Alarm notes creation and listing ─────────────────────────────────


async def test_alarm_notes_creation_and_listing(engine, seeded_db, fake_redis, settings):
    """Notes can be created and listed for an alarm."""
    settings.admin_api_key = TEST_ADMIN_API_KEY
    async with app_client(settings=settings, engine=engine, redis=fake_redis) as client:
        alarm_id = await _trigger_alarm(client)

        # Create first note
        create_resp_1 = await client.post(
            f"/v1/alarms/{alarm_id}/notes",
            headers=_admin_headers(),
            json={"note": "First observation", "created_by": "Nurse A"},
        )
        expect(create_resp_1.status_code == 201)
        note_1 = create_resp_1.json()
        expect(note_1["note"] == "First observation")
        expect(note_1["created_by"] == "Nurse A")
        expect(note_1["note_type"] == "manual")
        expect(note_1["alarm_id"] == str(alarm_id))

        # Create second note
        create_resp_2 = await client.post(
            f"/v1/alarms/{alarm_id}/notes",
            headers=_admin_headers(),
            json={"note": "Second update", "created_by": "Doctor B"},
        )
        expect(create_resp_2.status_code == 201)

        # List notes
        list_resp = await client.get(
            f"/v1/alarms/{alarm_id}/notes",
            headers=_admin_headers(),
        )
        expect(list_resp.status_code == 200)
        notes = list_resp.json()
        expect(len(notes) == 2)
        expect(notes[0]["note"] == "First observation")
        expect(notes[1]["note"] == "Second update")


async def test_alarm_notes_for_nonexistent_alarm_returns_404(
    engine, seeded_db, fake_redis, settings
):
    """Creating a note for a nonexistent alarm returns 404."""
    settings.admin_api_key = TEST_ADMIN_API_KEY
    missing_id = uuid.uuid4()
    async with app_client(settings=settings, engine=engine, redis=fake_redis) as client:
        response = await client.post(
            f"/v1/alarms/{missing_id}/notes",
            headers=_admin_headers(),
            json={"note": "This should fail"},
        )

    expect(response.status_code == 404)


# ── Alarm deletion (soft delete) ─────────────────────────────────────


async def test_alarm_soft_delete(engine, sessionmaker, seeded_db, fake_redis, settings):
    """Deleting an alarm sets deleted_at and deleted_by without removing the record."""
    settings.admin_api_key = TEST_ADMIN_API_KEY
    async with app_client(settings=settings, engine=engine, redis=fake_redis) as client:
        alarm_id = await _trigger_alarm(client)

        delete_resp = await client.delete(
            f"/v1/alarms/{alarm_id}",
            headers=_admin_headers(),
        )
        expect(delete_resp.status_code == 204)

    # Verify in DB: record still exists but deleted_at is set
    async with sessionmaker() as session:
        alarm = await session.get(Alarm, alarm_id)
        expect(alarm is not None)
        expect(alarm.deleted_at is not None)
        expect(alarm.deleted_by is not None)
        expect(alarm.deleted_by == "admin")


async def test_alarm_soft_delete_idempotent_returns_not_found(
    engine, seeded_db, fake_redis, settings
):
    """Deleting an already-deleted alarm returns 404 Not Found.

    The new security model makes get_alarm_or_404 return 404 for soft-deleted
    alarms, so the second delete attempt is treated as if the alarm does not exist.
    """
    settings.admin_api_key = TEST_ADMIN_API_KEY
    async with app_client(settings=settings, engine=engine, redis=fake_redis) as client:
        alarm_id = await _trigger_alarm(client)

        first = await client.delete(
            f"/v1/alarms/{alarm_id}",
            headers=_admin_headers(),
        )
        expect(first.status_code == 204)

        second = await client.delete(
            f"/v1/alarms/{alarm_id}",
            headers=_admin_headers(),
        )
        expect(second.status_code == 404)


async def test_alarm_delete_nonexistent_returns_404(engine, seeded_db, fake_redis, settings):
    """Deleting a nonexistent alarm returns 404."""
    settings.admin_api_key = TEST_ADMIN_API_KEY
    missing_id = uuid.uuid4()
    async with app_client(settings=settings, engine=engine, redis=fake_redis) as client:
        response = await client.delete(
            f"/v1/alarms/{missing_id}",
            headers=_admin_headers(),
        )

    expect(response.status_code == 404)
