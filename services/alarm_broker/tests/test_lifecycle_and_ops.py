from __future__ import annotations

try:
    from tests.assertions import expect
except ModuleNotFoundError:
    from assertions import expect

import re
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from alarm_broker.api.main import create_app
from alarm_broker.db.models import Alarm, AlarmStatus

try:
    from tests.constants import TEST_ADMIN_API_KEY, value_for_test
    from tests.helpers import admin_login
    from tests.helpers import trigger_alarm as _trigger_alarm
except ModuleNotFoundError:
    from constants import TEST_ADMIN_API_KEY, value_for_test
    from helpers import admin_login
    from helpers import trigger_alarm as _trigger_alarm

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
        "ack_token": value_for_test(f"alarm-{uuid.uuid4().hex[:8]}"),
        "created_at": datetime.now(UTC),
        "meta": {},
    }
    defaults.update(overrides)
    return Alarm(**defaults)


def _expect_bulk_payload(payload: dict, *, requested: int, changed: int, unchanged: int) -> None:
    expect(payload["requested"] == requested)
    expect(payload["changed"] == changed)
    expect(payload["unchanged"] == unchanged)


def _queued_ack_alarm_ids(fake_redis) -> list[str]:
    return [
        job[1][0]["alarm_id"]
        for job in fake_redis.jobs
        if job[0] == "process_alarm_event" and job[1][0].get("event_type") == "alarm.acknowledged"
    ]


def _markdown_link_targets(text: str) -> set[str]:
    return {match.group(1).strip() for match in re.finditer(r"\[[^\]]+\]\(([^)]+\.md)\)", text)}


def _backtick_markdown_targets(text: str) -> set[str]:
    candidates: set[str] = set()
    for line in text.splitlines():
        parts = line.split("`")
        if len(parts) >= 3 and parts[1].endswith(".md"):
            candidates.add(parts[1])
    return candidates


def _docs_index_candidates(text: str) -> set[str]:
    return _markdown_link_targets(text) | _backtick_markdown_targets(text)


async def test_readyz_healthy(engine, seeded_db, fake_redis, settings):
    app = create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/readyz")

    expect(response.status_code == 200)
    expect(response.json()["ok"] == "true")


async def test_readyz_redis_unhealthy_returns_503(engine, seeded_db, settings):
    class BrokenRedis:
        async def get(self, _key: str):
            raise RuntimeError("redis unavailable")

    app = create_app(settings=settings, injected_engine=engine, injected_redis=BrokenRedis())

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/readyz")

    expect(response.status_code == 503)
    expect(response.json()["redis"] == "down")


async def test_readyz_db_unhealthy_returns_503(engine, seeded_db, fake_redis, settings):
    class BrokenSessionmaker:
        def __call__(self):
            return self

        async def __aenter__(self):
            raise RuntimeError("db unavailable")

        async def __aexit__(self, exc_type, exc, tb):
            return None

    app = create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)

    async with app.router.lifespan_context(app):
        app.state.sessionmaker = BrokenSessionmaker()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/readyz")

    expect(response.status_code == 503)
    expect(response.json()["db"] == "down")


async def test_alarm_resolve_success_and_invalid_transition(
    engine, sessionmaker, seeded_db, fake_redis, settings
):
    settings.admin_api_key = TEST_ADMIN_API_KEY
    app = create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            alarm_id = await _trigger_alarm(client)

            resolve_response = await client.post(
                f"/v1/alarms/{alarm_id}/resolve",
                headers={"X-Admin-Key": TEST_ADMIN_API_KEY},
                json={"actor": "Ops", "note": "handled"},
            )
            expect(resolve_response.status_code == 204)

            invalid_response = await client.post(
                f"/v1/alarms/{alarm_id}/cancel",
                headers={"X-Admin-Key": TEST_ADMIN_API_KEY},
                json={"actor": "Ops", "note": "too late"},
            )
            expect(invalid_response.status_code == 409)

    async with sessionmaker() as session:
        alarm = await session.get(Alarm, alarm_id)
        expect(alarm is not None)
        expect(alarm.status == AlarmStatus.RESOLVED)
        expect(alarm.resolved_by == "Ops")
        expect(alarm.resolved_at is not None)


async def test_alarm_resolve_idempotent(engine, seeded_db, fake_redis, settings):
    settings.admin_api_key = TEST_ADMIN_API_KEY
    app = create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            alarm_id = await _trigger_alarm(client)

            first = await client.post(
                f"/v1/alarms/{alarm_id}/resolve",
                headers={"X-Admin-Key": TEST_ADMIN_API_KEY},
                json={"actor": "Ops"},
            )
            second = await client.post(
                f"/v1/alarms/{alarm_id}/resolve",
                headers={"X-Admin-Key": TEST_ADMIN_API_KEY},
                json={"actor": "Ops"},
            )

    expect(first.status_code == 204)
    expect(second.status_code == 204)


async def test_alarm_transition_rejects_oversized_actor(engine, seeded_db, fake_redis, settings):
    settings.admin_api_key = TEST_ADMIN_API_KEY
    app = create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            alarm_id = await _trigger_alarm(client)
            response = await client.post(
                f"/v1/alarms/{alarm_id}/resolve",
                headers={"X-Admin-Key": TEST_ADMIN_API_KEY},
                json={"actor": "A" * 121},
            )

    expect(response.status_code == 422)


async def test_alarm_pagination_cursor(engine, sessionmaker, seeded_db, fake_redis, settings):
    settings.admin_api_key = TEST_ADMIN_API_KEY

    now = datetime.now(UTC)
    alarm_ids: list[uuid.UUID] = []

    async with sessionmaker() as session:
        for index in range(3):
            alarm_id = uuid.uuid4()
            alarm_ids.append(alarm_id)
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
                    ack_token=f"token-{index}",
                    created_at=now - timedelta(minutes=index),
                    meta={},
                )
            )
        await session.commit()

    app = create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            page_1 = await client.get(
                "/v1/alarms",
                params={"limit": 2},
                headers={"X-Admin-Key": TEST_ADMIN_API_KEY},
            )
            expect(page_1.status_code == 200)
            expect(len(page_1.json()) == 2)
            expect("X-Next-Cursor" in page_1.headers)

            cursor = page_1.headers["X-Next-Cursor"]
            page_2 = await client.get(
                "/v1/alarms",
                params={"limit": 2, "cursor": cursor},
                headers={"X-Admin-Key": TEST_ADMIN_API_KEY},
            )
            expect(page_2.status_code == 200)
            expect(len(page_2.json()) >= 1)


async def test_bulk_resolve_reports_changed_unchanged_and_missing(
    engine, sessionmaker, seeded_db, fake_redis, settings
):
    settings.admin_api_key = TEST_ADMIN_API_KEY
    now = datetime.now(UTC)

    triggered_id = uuid.uuid4()
    already_resolved_id = uuid.uuid4()
    missing_id = uuid.uuid4()

    async with sessionmaker() as session:
        session.add(
            _make_alarm(
                alarm_id=triggered_id,
                ack_token=value_for_test("bulk-resolve-active"),
                created_at=now,
            )
        )
        session.add(
            _make_alarm(
                alarm_id=already_resolved_id,
                status=AlarmStatus.RESOLVED,
                ack_token=value_for_test("bulk-resolve-already"),
                created_at=now - timedelta(minutes=1),
                resolved_at=now - timedelta(minutes=1),
                resolved_by="Ops",
            )
        )
        await session.commit()

    app = create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/v1/alarms/bulk/resolve",
                headers={"X-Admin-Key": TEST_ADMIN_API_KEY},
                json={
                    "alarm_ids": [str(triggered_id), str(already_resolved_id), str(missing_id)],
                    "actor": "BulkOps",
                    "note": "batch resolution",
                },
            )

    expect(response.status_code == 200, response.text)
    payload = response.json()
    _expect_bulk_payload(payload, requested=3, changed=1, unchanged=1)
    expect(payload["missing"] == [str(missing_id)])

    async with sessionmaker() as session:
        updated = await session.get(Alarm, triggered_id)
        expect(updated is not None)
        expect(updated.status == AlarmStatus.RESOLVED)
        expect(updated.resolved_by == "BulkOps")


async def test_bulk_ack_enqueues_jobs_only_for_newly_acknowledged(
    engine, sessionmaker, seeded_db, fake_redis, settings
):
    settings.admin_api_key = TEST_ADMIN_API_KEY
    now = datetime.now(UTC)

    triggered_id = uuid.uuid4()
    acknowledged_id = uuid.uuid4()

    async with sessionmaker() as session:
        session.add(
            _make_alarm(
                alarm_id=triggered_id,
                ack_token=value_for_test("bulk-ack-triggered"),
                created_at=now,
            )
        )
        session.add(
            _make_alarm(
                alarm_id=acknowledged_id,
                status=AlarmStatus.ACKNOWLEDGED,
                ack_token=value_for_test("bulk-ack-existing"),
                created_at=now - timedelta(minutes=1),
                acked_at=now - timedelta(minutes=1),
                acked_by="Existing",
            )
        )
        await session.commit()

    app = create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/v1/alarms/bulk/ack",
                headers={"X-Admin-Key": TEST_ADMIN_API_KEY},
                json={
                    "alarm_ids": [str(triggered_id), str(acknowledged_id)],
                    "acked_by": "BulkResponder",
                    "note": "bulk ack note",
                },
            )

    expect(response.status_code == 200, response.text)
    payload = response.json()
    _expect_bulk_payload(payload, requested=2, changed=1, unchanged=1)
    expect(payload["missing"] == [])

    queued_ack_ids = _queued_ack_alarm_ids(fake_redis)
    expect(queued_ack_ids == [str(triggered_id)])


async def test_metrics_endpoint_exposes_prometheus_text(engine, seeded_db, fake_redis, settings):
    settings.admin_api_key = TEST_ADMIN_API_KEY
    app = create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            health_response = await client.get("/healthz")
            expect(health_response.status_code == 200)

            metrics_response = await client.get(
                "/metrics", headers={"X-Admin-Key": TEST_ADMIN_API_KEY}
            )

    expect(metrics_response.status_code == 200)
    expect(metrics_response.headers["content-type"].startswith("text/plain"))

    body = metrics_response.text
    expect("alarm_broker_http_requests_total" in body)
    expect("alarm_broker_http_request_duration_ms_total" in body)
    expect("alarm_broker_alarms_by_status" in body)
    expect("alarm_broker_notifications_total" in body)

    match = re.search(
        r'alarm_broker_http_requests_total\{method="GET",route="/healthz",status_code="200"\}\s+(\d+)',
        body,
    )
    expect(match is not None)
    expect(int(match.group(1)) >= 1)


async def test_admin_dashboard_requires_key_and_renders_alarms(
    engine, sessionmaker, seeded_db, fake_redis, settings
):
    settings.admin_api_key = TEST_ADMIN_API_KEY
    now = datetime.now(UTC)
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
                ack_token=value_for_test("admin-dashboard-alarm"),
                created_at=now,
                meta={},
            )
        )
        await session.commit()

    app = create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            unauthorized = await client.get("/admin")
            expect(unauthorized.status_code == 401)

            await admin_login(client, TEST_ADMIN_API_KEY)
            authorized = await client.get("/admin")

    expect(authorized.status_code == 200)
    expect("text/html" in authorized.headers["content-type"])
    # UUID is displayed truncated to 8 characters
    expect(str(alarm_id)[:8] in authorized.text)
    expect("triggered" in authorized.text)


@pytest.mark.unit
def test_docs_index_links_exist() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    docs_index = repo_root / "docs" / "README.md"
    text = docs_index.read_text(encoding="utf-8")

    for candidate in sorted(_docs_index_candidates(text)):
        if candidate.startswith("http://") or candidate.startswith("https://"):
            continue
        expect((repo_root / "docs" / candidate).exists(), f"Missing doc: {candidate}")
