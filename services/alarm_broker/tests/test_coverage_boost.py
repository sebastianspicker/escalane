"""Targeted tests to boost coverage for low-coverage modules.

Covers:
- alarm_operations.py: bulk ack (mixed states), bulk resolve, bulk cancel
- alarms.py: cursor pagination with multiple pages, CSV export sanitisation
- admin_ui.py: empty-state dashboard rendering
- health.py: /healthz/details endpoint
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
        "cancelled_at": None,
        "cancelled_by": None,
        "acked_at": None,
        "acked_by": None,
    }
    defaults.update(overrides)
    return Alarm(**defaults)


# ---------------------------------------------------------------------------
# Bulk ACK: mix of triggered, already-acknowledged, already-resolved, missing
# ---------------------------------------------------------------------------


async def test_bulk_ack_mixed_states(engine, sessionmaker, seeded_db, fake_redis, settings):
    """Bulk ACK with triggered + already-resolved + missing IDs reports correct counts."""
    settings.admin_api_key = ADMIN_KEY
    now = datetime.now(UTC)

    triggered_id = uuid.uuid4()
    resolved_id = uuid.uuid4()
    missing_id = uuid.uuid4()

    async with sessionmaker() as session:
        session.add(
            _make_alarm(alarm_id=triggered_id, status=AlarmStatus.TRIGGERED, created_at=now)
        )
        session.add(
            _make_alarm(
                alarm_id=resolved_id,
                status=AlarmStatus.RESOLVED,
                created_at=now - timedelta(minutes=1),
                resolved_at=now - timedelta(minutes=1),
                resolved_by="OldOps",
            )
        )
        await session.commit()

    app = create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/v1/alarms/bulk/ack",
                headers=HEADERS,
                json={
                    "alarm_ids": [str(triggered_id), str(resolved_id), str(missing_id)],
                    "acked_by": "BulkUser",
                    "note": "triage round",
                },
            )

    expect(resp.status_code == 200, resp.text)
    body = resp.json()
    expect(body["requested"] == 3)
    expect(body["changed"] == 1)  # only triggered -> acknowledged
    expect(body["unchanged"] == 1)  # resolved cannot be acked
    expect(body["missing"] == [str(missing_id)])


# ---------------------------------------------------------------------------
# Bulk resolve: all triggered -> resolved
# ---------------------------------------------------------------------------


async def test_bulk_resolve_all_changed(engine, sessionmaker, seeded_db, fake_redis, settings):
    """Bulk resolve where every alarm can transition reports all changed."""
    settings.admin_api_key = ADMIN_KEY
    now = datetime.now(UTC)

    ids = [uuid.uuid4() for _ in range(2)]
    async with sessionmaker() as session:
        for aid in ids:
            session.add(
                _make_alarm(
                    alarm_id=aid,
                    status=AlarmStatus.TRIGGERED,
                    created_at=now,
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
                json={"alarm_ids": [str(aid) for aid in ids], "actor": "Ops", "note": "done"},
            )

    expect(resp.status_code == 200)
    body = resp.json()
    expect(body["requested"] == 2)
    expect(body["changed"] == 2)
    expect(body["unchanged"] == 0)
    expect(body["missing"] == [])


# ---------------------------------------------------------------------------
# Bulk cancel: mix of triggered and already-cancelled
# ---------------------------------------------------------------------------


async def test_bulk_cancel_counts(engine, sessionmaker, seeded_db, fake_redis, settings):
    """Bulk cancel reports correct changed/unchanged counts."""
    settings.admin_api_key = ADMIN_KEY
    now = datetime.now(UTC)

    triggered_id = uuid.uuid4()
    cancelled_id = uuid.uuid4()

    async with sessionmaker() as session:
        session.add(
            _make_alarm(alarm_id=triggered_id, status=AlarmStatus.TRIGGERED, created_at=now)
        )
        session.add(
            _make_alarm(
                alarm_id=cancelled_id,
                status=AlarmStatus.CANCELLED,
                created_at=now - timedelta(minutes=1),
                cancelled_at=now - timedelta(minutes=1),
                cancelled_by="PrevOps",
            )
        )
        await session.commit()

    app = create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/v1/alarms/bulk/cancel",
                headers=HEADERS,
                json={
                    "alarm_ids": [str(triggered_id), str(cancelled_id)],
                    "actor": "Admin",
                },
            )

    expect(resp.status_code == 200)
    body = resp.json()
    expect(body["requested"] == 2)
    expect(body["changed"] == 1)
    expect(body["unchanged"] == 1)
    expect(body["missing"] == [])


# ---------------------------------------------------------------------------
# Cursor pagination: 5 alarms, fetch limit=2, walk all pages via cursor
# ---------------------------------------------------------------------------


async def test_cursor_pagination_multiple_pages(
    engine, sessionmaker, seeded_db, fake_redis, settings
):
    """Create 5 alarms, paginate with limit=2, verify all pages and no duplicates."""
    settings.admin_api_key = ADMIN_KEY
    now = datetime.now(UTC)

    created_ids = []
    async with sessionmaker() as session:
        for i in range(5):
            aid = uuid.uuid4()
            created_ids.append(aid)
            session.add(
                _make_alarm(
                    alarm_id=aid,
                    status=AlarmStatus.TRIGGERED,
                    created_at=now - timedelta(seconds=i),
                )
            )
        await session.commit()

    app = create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)
    all_fetched: list[str] = []

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            cursor = None
            pages = 0
            while True:
                params: dict = {"limit": 2}
                if cursor:
                    params["cursor"] = cursor
                resp = await client.get("/v1/alarms", params=params, headers=HEADERS)
                expect(resp.status_code == 200)
                data = resp.json()
                all_fetched.extend(item["id"] for item in data)
                pages += 1
                cursor = resp.headers.get("X-Next-Cursor")
                if not cursor:
                    break

    # We should have walked through >= 3 pages (2+2+1)
    expect(pages >= 3)
    # No duplicates
    expect(len(all_fetched) == len(set(all_fetched)))
    # All created alarms should appear
    for aid in created_ids:
        expect(str(aid) in all_fetched)


# ---------------------------------------------------------------------------
# CSV export: formula injection sanitisation
# ---------------------------------------------------------------------------


async def test_export_csv_sanitises_formula_injection(
    engine, sessionmaker, seeded_db, fake_redis, settings
):
    """CSV export prefixes dangerous characters (=, +, -, @, tab, CR) with apostrophe."""
    settings.admin_api_key = ADMIN_KEY

    malicious_id = uuid.uuid4()
    async with sessionmaker() as session:
        session.add(
            _make_alarm(
                alarm_id=malicious_id,
                status=AlarmStatus.TRIGGERED,
                source="=cmd('calc')",
            )
        )
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

    expect(resp.status_code == 200)
    expect("text/csv" in resp.headers["content-type"])
    body = resp.text
    # The malicious source should be sanitised with a leading apostrophe
    expect("'=cmd('calc')" in body)
    # The raw unsanitised version should NOT appear as-is in a CSV cell start
    lines = body.strip().split("\n")
    for line in lines[1:]:  # skip header
        for cell in line.split(","):
            cell = cell.strip().strip('"')
            if cell.startswith("=cmd"):
                pytest.fail(f"Unsanitised formula found in CSV: {cell}")


# ---------------------------------------------------------------------------
# Admin dashboard: empty state (no alarms)
# ---------------------------------------------------------------------------


async def test_admin_dashboard_empty_state(engine, seeded_db, fake_redis, settings):
    """Admin dashboard renders correctly when there are zero alarms."""
    settings.admin_api_key = ADMIN_KEY

    app = create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            await admin_login(client, ADMIN_KEY)
            resp = await client.get("/admin")

    expect(resp.status_code == 200)
    expect("text/html" in resp.headers["content-type"])
    expect("No alarms found" in resp.text)


# ---------------------------------------------------------------------------
# Health details: /healthz/details returns dependency and connector info
# ---------------------------------------------------------------------------


async def test_healthz_details_returns_dependency_info(engine, seeded_db, fake_redis, settings):
    """GET /healthz/details returns 200 with database, redis, and connector status."""
    settings.admin_api_key = ADMIN_KEY
    app = create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/healthz/details", headers=HEADERS)

    expect(resp.status_code == 200)
    body = resp.json()

    # Application section
    expect(body["application"]["name"] == "alarm-broker")
    expect("uptime_seconds" in body["application"])
    expect("timestamp" in body["application"])

    # Dependencies
    expect(body["dependencies"]["database"]["status"] == "ok")
    expect(body["dependencies"]["redis"]["status"] == "ok")
    expect("latency_ms" in body["dependencies"]["redis"])

    # Connectors
    expect("zammad" in body["connectors"])
    expect("sms" in body["connectors"])
    expect("signal" in body["connectors"])

    # Overall
    expect(body["status"] == "healthy")


async def test_healthz_details_unhealthy_redis(engine, seeded_db, settings):
    """GET /healthz/details returns 503 when Redis is down."""
    settings.admin_api_key = ADMIN_KEY

    class BrokenRedis:
        async def get(self, _key: str):
            raise RuntimeError("redis unavailable")

    app = create_app(settings=settings, injected_engine=engine, injected_redis=BrokenRedis())

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/healthz/details", headers=HEADERS)

    expect(resp.status_code == 503)
    body = resp.json()
    expect(body["status"] == "unhealthy")
    expect(body["dependencies"]["redis"]["status"] == "error")
    # Database should still be ok
    expect(body["dependencies"]["database"]["status"] == "ok")
