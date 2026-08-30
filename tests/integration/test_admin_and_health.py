"""Tests for admin_ui.py and health.py routes."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import text

from escalane.contracts.alarms import AlarmStatus
from escalane.persistence.models import Alarm
from escalane.web.routes.health import EXPECTED_ALEMBIC_HEAD
from tests.support.api_test_helpers import app_client
from tests.support.api_test_helpers import make_alarm as _base_alarm
from tests.support.assertions import expect
from tests.support.constants import ACK_ADMIN_HTML_TOKEN, TEST_ADMIN_API_KEY, value_for_test
from tests.support.helpers import admin_login

pytestmark = [pytest.mark.integration]


def _make_alarm(**overrides) -> Alarm:
    """Build an admin-route fixture with a distinct non-secret token label."""
    overrides.setdefault("ack_token", value_for_test(f"admin-{uuid.uuid4().hex[:8]}"))
    return _base_alarm(**overrides)


def _expect_filter_result(html: str, *, visible_id: uuid.UUID, hidden_id: uuid.UUID) -> None:
    expect(str(visible_id)[:8] in html)
    expect(str(hidden_id)[:8] not in html)


def _enable_connector_readiness_probe(settings) -> None:
    """Configure connector flags for the effective-readiness scenario."""
    settings.simulation_enabled = True
    settings.sendxms_enabled = True
    settings.signal_enabled = True


async def _health_details(settings, engine, fake_redis):
    """Request detailed health with the configured administrative key."""
    async with app_client(settings=settings, engine=engine, redis=fake_redis) as client:
        return await client.get("/healthz/details", headers={"X-Admin-Key": TEST_ADMIN_API_KEY})


async def test_admin_dashboard_renders_html_with_seeded_alarms(
    engine, sessionmaker, seeded_db, fake_redis, settings
):
    """Admin dashboard returns HTML containing seeded alarm data."""
    settings.admin_api_key = TEST_ADMIN_API_KEY
    alarm_id = uuid.uuid4()

    async with sessionmaker() as session:
        session.add(
            _base_alarm(
                alarm_id=alarm_id,
                ack_token=ACK_ADMIN_HTML_TOKEN,
                created_at=datetime.now(UTC),
            )
        )
        await session.commit()

    async with app_client(settings=settings, engine=engine, redis=fake_redis) as client:
        await admin_login(client, TEST_ADMIN_API_KEY)
        response = await client.get("/admin")

    expect(response.status_code == 200)
    expect("text/html" in response.headers["content-type"])
    expect(str(alarm_id)[:8] in response.text)
    expect("triggered" in response.text)
    expect(">Owner<" in response.text)
    expect("Unassigned" in response.text)


async def test_admin_dashboard_status_filter(engine, sessionmaker, seeded_db, fake_redis, settings):
    """Admin dashboard filters alarms by status query param."""
    settings.admin_api_key = TEST_ADMIN_API_KEY
    now = datetime.now(UTC)

    triggered_id = uuid.uuid4()
    resolved_id = uuid.uuid4()

    async with sessionmaker() as session:
        session.add(
            _make_alarm(
                alarm_id=triggered_id,
                ack_token=value_for_test("filter-triggered"),
                created_at=now,
            )
        )
        session.add(
            _make_alarm(
                alarm_id=resolved_id,
                status=AlarmStatus.RESOLVED,
                ack_token=value_for_test("filter-resolved"),
                created_at=now,
                resolved_at=now,
                resolved_by="Ops",
            )
        )
        await session.commit()

    async with app_client(settings=settings, engine=engine, redis=fake_redis) as client:
        await admin_login(client, TEST_ADMIN_API_KEY)
        response = await client.get("/admin", params={"status": "resolved"})

    expect(response.status_code == 200)
    _expect_filter_result(response.text, visible_id=resolved_id, hidden_id=triggered_id)


async def test_admin_dashboard_without_api_key_returns_401(engine, seeded_db, fake_redis, settings):
    """Admin dashboard returns 401 when no session cookie is present."""
    settings.admin_api_key = TEST_ADMIN_API_KEY

    async with app_client(settings=settings, engine=engine, redis=fake_redis) as client:
        response = await client.get("/admin")

    expect(response.status_code == 401)


async def test_admin_login_cookie_works_over_local_http(engine, seeded_db, fake_redis, settings):
    """Local HTTP login should work without manually injecting cookies in tests."""
    settings.admin_api_key = TEST_ADMIN_API_KEY
    async with app_client(settings=settings, engine=engine, redis=fake_redis) as client:
        login = await client.post(
            "/admin/login", data={"admin_key": TEST_ADMIN_API_KEY}, follow_redirects=False
        )
        dashboard = await client.get("/admin")

    expect(login.status_code == 303)
    expect("Secure" not in login.headers.get("set-cookie", ""))
    expect(dashboard.status_code == 200)


async def test_admin_session_expires_via_redis_ttl(engine, seeded_db, fake_redis, settings):
    """Admin sessions should expire based on Redis TTL rather than process-local state."""
    settings.admin_api_key = TEST_ADMIN_API_KEY
    async with app_client(settings=settings, engine=engine, redis=fake_redis) as client:
        await admin_login(client, TEST_ADMIN_API_KEY)
        fake_redis.advance(3601)
        response = await client.get("/admin")

    expect(response.status_code == 401)


async def test_ack_rate_limit_is_shared_through_redis(
    engine, sessionmaker, seeded_db, fake_redis, settings
):
    """ACK throttling should apply across clients sharing the same Redis backend."""
    alarm_id = uuid.uuid4()
    ack_token = value_for_test("ack-rate-limit")

    async with sessionmaker() as session:
        session.add(
            _base_alarm(
                alarm_id=alarm_id,
                ack_token=ack_token,
                created_at=datetime.now(UTC),
            )
        )
        await session.commit()

    async with app_client(settings=settings, engine=engine, redis=fake_redis) as client_a:
        async with app_client(settings=settings, engine=engine, redis=fake_redis) as client_b:
            for client in (client_a, client_b):
                for _ in range(5):
                    response = await client.get(f"/a/{ack_token}")
                    expect(response.status_code == 200)

            throttled = await client_b.get(f"/a/{ack_token}")

    expect(throttled.status_code == 429)


async def test_readyz_healthy_returns_200(engine, seeded_db, fake_redis, settings):
    """Readyz returns 200 when DB and Redis are healthy."""
    async with app_client(settings=settings, engine=engine, redis=fake_redis) as client:
        response = await client.get("/readyz")

    expect(response.status_code == 200)
    body = response.json()
    expect(body["ok"] == "true")
    expect(body["db"] == "ok")
    expect(body["schema"] == "ok")


@pytest.mark.parametrize(
    ("versions", "expected_status"),
    [
        ([], "empty"),
        (["0006"], "stale"),
        ([EXPECTED_ALEMBIC_HEAD, "0006"], "multiple"),
    ],
)
async def test_readyz_rejects_non_current_migration_state(
    engine, sessionmaker, seeded_db, fake_redis, settings, versions, expected_status
):
    """Readiness fails closed for empty, stale, and divergent Alembic state."""
    async with sessionmaker() as session:
        await session.execute(text("DELETE FROM alembic_version"))
        for version in versions:
            await session.execute(
                text("INSERT INTO alembic_version (version_num) VALUES (:version)"),
                {"version": version},
            )
        await session.commit()

    async with app_client(settings=settings, engine=engine, redis=fake_redis) as client:
        response = await client.get("/readyz")

    expect(response.status_code == 503)
    body = response.json()
    expect(body["ok"] == "false")
    expect(body["db"] == "ok")
    expect(body["schema"] == expected_status)


async def test_readyz_rejects_missing_migration_state(
    engine, sessionmaker, seeded_db, fake_redis, settings
):
    """Readiness fails closed when Alembic has never initialized the database."""
    async with sessionmaker() as session:
        await session.execute(text("DROP TABLE alembic_version"))
        await session.commit()

    async with app_client(settings=settings, engine=engine, redis=fake_redis) as client:
        response = await client.get("/readyz")

    expect(response.status_code == 503)
    expect(response.json()["schema"] == "missing")


async def test_health_details_rejects_stale_schema(
    engine, sessionmaker, seeded_db, fake_redis, settings
):
    """Detailed health does not report a stale database schema as healthy."""
    settings.admin_api_key = TEST_ADMIN_API_KEY
    async with sessionmaker() as session:
        await session.execute(text("DELETE FROM alembic_version"))
        await session.execute(text("INSERT INTO alembic_version (version_num) VALUES ('0006')"))
        await session.commit()

    response = await _health_details(settings, engine, fake_redis)

    expect(response.status_code == 503)
    body = response.json()
    expect(body["status"] == "unhealthy")
    expect(body["dependencies"]["database"]["schema"]["status"] == "stale")


async def test_health_details_reports_effective_connector_readiness(
    engine, seeded_db, fake_redis, settings
):
    """Connector flags reflect usable configuration, not only enable switches."""
    settings.admin_api_key = TEST_ADMIN_API_KEY
    _enable_connector_readiness_probe(settings)

    response = await _health_details(settings, engine, fake_redis)

    expect(response.status_code == 200)
    connectors = response.json()["connectors"]
    expect(connectors["sms"]["enabled"] is False)
    expect(connectors["signal"]["enabled"] is False)


async def test_metrics_returns_prometheus_text_with_alarm_counts(
    engine, sessionmaker, seeded_db, fake_redis, settings
):
    """Metrics endpoint returns Prometheus text format including alarm counts."""
    settings.admin_api_key = TEST_ADMIN_API_KEY
    alarm_id = uuid.uuid4()

    async with sessionmaker() as session:
        session.add(
            _base_alarm(
                alarm_id=alarm_id,
                ack_token=value_for_test("metrics"),
                created_at=datetime.now(UTC),
            )
        )
        await session.commit()

    async with app_client(settings=settings, engine=engine, redis=fake_redis) as client:
        response = await client.get("/metrics", headers={"X-Admin-Key": TEST_ADMIN_API_KEY})

    expect(response.status_code == 200)
    expect(response.headers["content-type"].startswith("text/plain"))

    body = response.text
    expect("escalane_alarms_by_status" in body)
    expect('status="triggered"' in body)
