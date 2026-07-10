"""Targeted alarm query/export coverage tests split from final coverage."""

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

    expect(resp.status_code == 200)
    ids = [a["id"] for a in resp.json()]
    expect(str(new_id) in ids)
    expect(str(old_id) not in ids)


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

    expect(resp.status_code == 200)
    ids = [a["id"] for a in resp.json()]
    expect(str(old_id) in ids)
    expect(str(new_id) not in ids)


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

    expect(resp.status_code == 200)
    ids = [a["id"] for a in resp.json()]
    expect(str(target_id) in ids)
    expect(str(other_id) not in ids)


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

    expect(resp.status_code == 200)
    ids = [a["id"] for a in resp.json()]
    expect(str(p0_id) in ids)
    expect(str(p1_id) not in ids)


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

    expect(resp.status_code == 200)
    ids = [a["id"] for a in resp.json()]
    expect(str(target_id) in ids)
    expect(str(other_id) not in ids)


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

    expect(resp.status_code == 200)
    ids = [a["id"] for a in resp.json()]
    expect(str(target_id) in ids)
    expect(str(other_id) not in ids)


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

    expect(resp.status_code == 200)
    expect("text/csv" in resp.headers["content-type"])
    expect(resp.text.strip() == "")


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

    expect(resp.status_code == 200)
    expect("application/json" in resp.headers["content-type"])
    data = resp.json()
    expect(isinstance(data, list))
    expect(len(data) >= 1)


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

    expect(resp.status_code == 200)
    lines = resp.text.strip().split("\n")
    expect(len(lines) >= 2)
    expect("id" in lines[0])
    expect("status" in lines[0])


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

    expect(resp.status_code == 200)
    body = resp.json()
    expect(body["total"] >= 2)
    expect("by_status" in body)
    expect("by_severity" in body)


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

    expect(resp.status_code == 200)
    data = resp.json()
    expect(len(data) >= 2)
    expect(data[0]["created_at"] <= data[1]["created_at"])


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
            expect(resp.status_code == 200)
            cursor = resp.headers.get("X-Next-Cursor")
            if cursor:
                resp2 = await client.get(
                    "/v1/alarms",
                    params={"limit": 2, "sort_order": "asc", "cursor": cursor},
                    headers=HEADERS,
                )
                expect(resp2.status_code == 200)


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
    app = create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)
    actual_ids = await _walk_alarm_pages(app, sort_by, sort_order)
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


async def _walk_alarm_pages(app, sort_by: str, sort_order: str) -> list[str]:
    actual_ids = []
    cursor = None
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
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
