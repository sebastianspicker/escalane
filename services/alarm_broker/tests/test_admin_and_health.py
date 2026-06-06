"""Tests for admin_ui.py and health.py routes."""

from __future__ import annotations

try:
    from tests.assertions import expect
except ModuleNotFoundError:
    from assertions import expect

import uuid
from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient

from alarm_broker.api.main import create_app
from alarm_broker.api.routes.admin_ui import _render_alarm_row
from alarm_broker.db.models import Alarm, AlarmStatus

try:
    from tests.constants import ACK_ADMIN_HTML_TOKEN, TEST_ADMIN_API_KEY, value_for_test
    from tests.helpers import admin_login
except ModuleNotFoundError:
    from constants import ACK_ADMIN_HTML_TOKEN, TEST_ADMIN_API_KEY, value_for_test
    from helpers import admin_login

pytestmark = [pytest.mark.integration]


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
        "ack_token": value_for_test(f"admin-{uuid.uuid4().hex[:8]}"),
        "created_at": datetime.now(UTC),
        "meta": {},
    }
    defaults.update(overrides)
    return Alarm(**defaults)


def _expect_filter_result(html: str, *, visible_id: uuid.UUID, hidden_id: uuid.UUID) -> None:
    expect(str(visible_id)[:8] in html)
    expect(str(hidden_id)[:8] not in html)


@pytest.mark.unit
def test_render_alarm_row_escapes_fields_and_action_states() -> None:
    """Alarm rows should escape display data and expose stable action states."""
    created_at = datetime(2026, 1, 2, 3, 4, tzinfo=UTC)

    triggered = _make_alarm(
        status=AlarmStatus.TRIGGERED,
        person_id="<person>",
        room_id="room&1",
        source="src'bad",
        severity='P"0',
        created_at=created_at,
    )
    triggered_row = _render_alarm_row(triggered)

    expect("data-person='&lt;person&gt;'" in triggered_row)
    expect("data-room='room&amp;1'" in triggered_row)
    expect("data-source='src&#x27;bad'" in triggered_row)
    expect("data-severity='P&quot;0'" in triggered_row)
    expect("data-can-ack='true'" in triggered_row)
    expect("data-can-resolve='true'" in triggered_row)
    expect("quick-ack-btn'>Quick Ack</button>" in triggered_row)
    expect("quick-resolve-btn'>Quick Resolve</button>" in triggered_row)

    acknowledged_row = _render_alarm_row(
        _make_alarm(
            status=AlarmStatus.ACKNOWLEDGED,
            acked_by="Ops <Lead>",
            created_at=created_at,
        )
    )
    expect("data-acked-by='Ops &lt;Lead&gt;'" in acknowledged_row)
    expect("data-can-ack='false'" in acknowledged_row)
    expect("data-can-resolve='true'" in acknowledged_row)
    expect("quick-ack-btn' disabled>Quick Ack</button>" in acknowledged_row)
    expect("quick-resolve-btn'>Quick Resolve</button>" in acknowledged_row)

    resolved_row = _render_alarm_row(
        _make_alarm(status=AlarmStatus.RESOLVED, created_at=created_at)
    )
    expect("data-can-ack='false'" in resolved_row)
    expect("data-can-resolve='false'" in resolved_row)
    expect("quick-ack-btn' disabled>Quick Ack</button>" in resolved_row)
    expect("quick-resolve-btn' disabled>Quick Resolve</button>" in resolved_row)


async def test_admin_dashboard_renders_html_with_seeded_alarms(
    engine, sessionmaker, seeded_db, fake_redis, settings
):
    """Admin dashboard returns HTML containing seeded alarm data."""
    settings.admin_api_key = TEST_ADMIN_API_KEY
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
                ack_token=ACK_ADMIN_HTML_TOKEN,
                created_at=datetime.now(UTC),
                meta={},
            )
        )
        await session.commit()

    app = create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            await admin_login(client, TEST_ADMIN_API_KEY)
            response = await client.get("/admin")

    expect(response.status_code == 200)
    expect("text/html" in response.headers["content-type"])
    expect(str(alarm_id)[:8] in response.text)
    expect("triggered" in response.text)


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

    app = create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            await admin_login(client, TEST_ADMIN_API_KEY)
            response = await client.get("/admin", params={"status": "resolved"})

    expect(response.status_code == 200)
    _expect_filter_result(response.text, visible_id=resolved_id, hidden_id=triggered_id)


async def test_admin_dashboard_without_api_key_returns_401(engine, seeded_db, fake_redis, settings):
    """Admin dashboard returns 401 when no session cookie is present."""
    settings.admin_api_key = TEST_ADMIN_API_KEY

    app = create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/admin")

    expect(response.status_code == 401)


async def test_admin_login_cookie_works_over_local_http(engine, seeded_db, fake_redis, settings):
    """Local HTTP login should work without manually injecting cookies in tests."""
    settings.admin_api_key = TEST_ADMIN_API_KEY
    app = create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            login = await client.post(
                "/admin/login",
                data={"admin_key": TEST_ADMIN_API_KEY},
                follow_redirects=False,
            )
            dashboard = await client.get("/admin")

    expect(login.status_code == 303)
    expect("Secure" not in login.headers.get("set-cookie", ""))
    expect(dashboard.status_code == 200)


async def test_admin_session_expires_via_redis_ttl(engine, seeded_db, fake_redis, settings):
    """Admin sessions should expire based on Redis TTL rather than process-local state."""
    settings.admin_api_key = TEST_ADMIN_API_KEY
    app = create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
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
                ack_token=ack_token,
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
                    response = await client_a.get(f"/a/{ack_token}")
                    expect(response.status_code == 200)
                for _ in range(5):
                    response = await client_b.get(f"/a/{ack_token}")
                    expect(response.status_code == 200)

                throttled = await client_b.get(f"/a/{ack_token}")

    expect(throttled.status_code == 429)


async def test_readyz_healthy_returns_200(engine, seeded_db, fake_redis, settings):
    """Readyz returns 200 when DB and Redis are healthy."""
    app = create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/readyz")

    expect(response.status_code == 200)
    body = response.json()
    expect(body["ok"] == "true")
    expect(body["db"] == "ok")


async def test_metrics_returns_prometheus_text_with_alarm_counts(
    engine, sessionmaker, seeded_db, fake_redis, settings
):
    """Metrics endpoint returns Prometheus text format including alarm counts."""
    settings.admin_api_key = TEST_ADMIN_API_KEY
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
                ack_token=value_for_test("metrics"),
                created_at=datetime.now(UTC),
                meta={},
            )
        )
        await session.commit()

    app = create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/metrics", headers={"X-Admin-Key": TEST_ADMIN_API_KEY})

    expect(response.status_code == 200)
    expect(response.headers["content-type"].startswith("text/plain"))

    body = response.text
    expect("alarm_broker_alarms_by_status" in body)
    expect('status="triggered"' in body)
