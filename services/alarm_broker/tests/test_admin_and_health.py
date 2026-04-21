"""Tests for admin_ui.py and health.py routes."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient

from alarm_broker.api.main import create_app
from alarm_broker.db.models import Alarm, AlarmStatus

try:
    from tests.helpers import admin_login
except ModuleNotFoundError:
    from helpers import admin_login

pytestmark = [pytest.mark.integration]


async def test_admin_dashboard_renders_html_with_seeded_alarms(
    engine, sessionmaker, seeded_db, fake_redis, settings
):
    """Admin dashboard returns HTML containing seeded alarm data."""
    settings.admin_api_key = "dev-admin-key"
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
                ack_token="admin-html-test",
                created_at=datetime.now(UTC),
                meta={},
            )
        )
        await session.commit()

    app = create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            await admin_login(client, "dev-admin-key")
            response = await client.get("/admin")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert str(alarm_id)[:8] in response.text
    assert "triggered" in response.text


async def test_admin_dashboard_status_filter(engine, sessionmaker, seeded_db, fake_redis, settings):
    """Admin dashboard filters alarms by status query param."""
    settings.admin_api_key = "dev-admin-key"
    now = datetime.now(UTC)

    triggered_id = uuid.uuid4()
    resolved_id = uuid.uuid4()

    async with sessionmaker() as session:
        session.add(
            Alarm(
                id=triggered_id,
                status=AlarmStatus.TRIGGERED,
                source="test",
                event="alarm.trigger",
                person_id="ma-012",
                room_id="bg-1.23",
                site_id="bg",
                device_id="ylk-t5-10023",
                severity="P0",
                silent=True,
                ack_token="filter-triggered",
                created_at=now,
                meta={},
            )
        )
        session.add(
            Alarm(
                id=resolved_id,
                status=AlarmStatus.RESOLVED,
                source="test",
                event="alarm.trigger",
                person_id="ma-012",
                room_id="bg-1.23",
                site_id="bg",
                device_id="ylk-t5-10023",
                severity="P0",
                silent=True,
                ack_token="filter-resolved",
                created_at=now,
                resolved_at=now,
                resolved_by="Ops",
                meta={},
            )
        )
        await session.commit()

    app = create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            await admin_login(client, "dev-admin-key")
            response = await client.get("/admin", params={"status": "resolved"})

    assert response.status_code == 200
    # Resolved alarm should be visible
    assert str(resolved_id)[:8] in response.text
    # Triggered alarm should NOT be visible when filtering by resolved
    assert str(triggered_id)[:8] not in response.text


async def test_admin_dashboard_without_api_key_returns_401(engine, seeded_db, fake_redis, settings):
    """Admin dashboard returns 401 when no session cookie is present."""
    settings.admin_api_key = "dev-admin-key"

    app = create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/admin")

    assert response.status_code == 401


async def test_admin_login_cookie_works_over_local_http(engine, seeded_db, fake_redis, settings):
    """Local HTTP login should work without manually injecting cookies in tests."""
    settings.admin_api_key = "dev-admin-key"
    app = create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            login = await client.post(
                "/admin/login",
                data={"admin_key": "dev-admin-key"},
                follow_redirects=False,
            )
            dashboard = await client.get("/admin")

    assert login.status_code == 303
    assert "Secure" not in login.headers.get("set-cookie", "")
    assert dashboard.status_code == 200


async def test_admin_session_expires_via_redis_ttl(engine, seeded_db, fake_redis, settings):
    """Admin sessions should expire based on Redis TTL rather than process-local state."""
    settings.admin_api_key = "dev-admin-key"
    app = create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            await admin_login(client, "dev-admin-key")
            fake_redis.advance(3601)
            response = await client.get("/admin")

    assert response.status_code == 401


async def test_ack_rate_limit_is_shared_through_redis(
    engine, sessionmaker, seeded_db, fake_redis, settings
):
    """ACK throttling should apply across clients sharing the same Redis backend."""
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
                ack_token="ack-rate-limit-test",
                created_at=datetime.now(UTC),
                meta={},
            )
        )
        await session.commit()

    app = create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client_a:
            async with AsyncClient(transport=transport, base_url="http://test") as client_b:
                for _ in range(5):
                    response = await client_a.get("/a/ack-rate-limit-test")
                    assert response.status_code == 200
                for _ in range(5):
                    response = await client_b.get("/a/ack-rate-limit-test")
                    assert response.status_code == 200

                throttled = await client_b.get("/a/ack-rate-limit-test")

    assert throttled.status_code == 429


async def test_readyz_healthy_returns_200(engine, seeded_db, fake_redis, settings):
    """Readyz returns 200 when DB and Redis are healthy."""
    app = create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/readyz")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] == "true"
    assert body["db"] == "ok"


async def test_metrics_returns_prometheus_text_with_alarm_counts(
    engine, sessionmaker, seeded_db, fake_redis, settings
):
    """Metrics endpoint returns Prometheus text format including alarm counts."""
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
                ack_token="metrics-test",
                created_at=datetime.now(UTC),
                meta={},
            )
        )
        await session.commit()

    app = create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/metrics", headers={"X-Admin-Key": "dev-admin-key"})

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")

    body = response.text
    assert "alarm_broker_alarms_by_status" in body
    assert 'status="triggered"' in body
