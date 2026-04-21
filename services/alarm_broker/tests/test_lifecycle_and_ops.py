from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from alarm_broker.api.main import create_app
from alarm_broker.db.models import Alarm, AlarmStatus

try:
    from tests.helpers import admin_login
    from tests.helpers import trigger_alarm as _trigger_alarm
except ModuleNotFoundError:
    from helpers import admin_login
    from helpers import trigger_alarm as _trigger_alarm

pytestmark = [pytest.mark.integration]


async def test_readyz_healthy(engine, seeded_db, fake_redis, settings):
    app = create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/readyz")

    assert response.status_code == 200
    assert response.json()["ok"] == "true"


async def test_readyz_redis_unhealthy_returns_503(engine, seeded_db, settings):
    class BrokenRedis:
        async def get(self, _key: str):
            raise RuntimeError("redis unavailable")

    app = create_app(settings=settings, injected_engine=engine, injected_redis=BrokenRedis())

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/readyz")

    assert response.status_code == 503
    assert response.json()["redis"] == "down"


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

    assert response.status_code == 503
    assert response.json()["db"] == "down"


async def test_alarm_resolve_success_and_invalid_transition(
    engine, sessionmaker, seeded_db, fake_redis, settings
):
    settings.admin_api_key = "dev-admin-key"
    app = create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            alarm_id = await _trigger_alarm(client)

            resolve_response = await client.post(
                f"/v1/alarms/{alarm_id}/resolve",
                headers={"X-Admin-Key": "dev-admin-key"},
                json={"actor": "Ops", "note": "handled"},
            )
            assert resolve_response.status_code == 204

            invalid_response = await client.post(
                f"/v1/alarms/{alarm_id}/cancel",
                headers={"X-Admin-Key": "dev-admin-key"},
                json={"actor": "Ops", "note": "too late"},
            )
            assert invalid_response.status_code == 409

    async with sessionmaker() as session:
        alarm = await session.get(Alarm, alarm_id)
        assert alarm is not None
        assert alarm.status == AlarmStatus.RESOLVED
        assert alarm.resolved_by == "Ops"
        assert alarm.resolved_at is not None


async def test_alarm_resolve_idempotent(engine, seeded_db, fake_redis, settings):
    settings.admin_api_key = "dev-admin-key"
    app = create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            alarm_id = await _trigger_alarm(client)

            first = await client.post(
                f"/v1/alarms/{alarm_id}/resolve",
                headers={"X-Admin-Key": "dev-admin-key"},
                json={"actor": "Ops"},
            )
            second = await client.post(
                f"/v1/alarms/{alarm_id}/resolve",
                headers={"X-Admin-Key": "dev-admin-key"},
                json={"actor": "Ops"},
            )

    assert first.status_code == 204
    assert second.status_code == 204


async def test_alarm_transition_rejects_oversized_actor(engine, seeded_db, fake_redis, settings):
    settings.admin_api_key = "dev-admin-key"
    app = create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            alarm_id = await _trigger_alarm(client)
            response = await client.post(
                f"/v1/alarms/{alarm_id}/resolve",
                headers={"X-Admin-Key": "dev-admin-key"},
                json={"actor": "A" * 121},
            )

    assert response.status_code == 422


async def test_alarm_pagination_cursor(engine, sessionmaker, seeded_db, fake_redis, settings):
    settings.admin_api_key = "dev-admin-key"

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
                headers={"X-Admin-Key": "dev-admin-key"},
            )
            assert page_1.status_code == 200
            assert len(page_1.json()) == 2
            assert "X-Next-Cursor" in page_1.headers

            cursor = page_1.headers["X-Next-Cursor"]
            page_2 = await client.get(
                "/v1/alarms",
                params={"limit": 2, "cursor": cursor},
                headers={"X-Admin-Key": "dev-admin-key"},
            )
            assert page_2.status_code == 200
            assert len(page_2.json()) >= 1


async def test_bulk_resolve_reports_changed_unchanged_and_missing(
    engine, sessionmaker, seeded_db, fake_redis, settings
):
    settings.admin_api_key = "dev-admin-key"
    now = datetime.now(UTC)

    triggered_id = uuid.uuid4()
    already_resolved_id = uuid.uuid4()
    missing_id = uuid.uuid4()

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
                ack_token="bulk-resolve-triggered",
                created_at=now,
                meta={},
            )
        )
        session.add(
            Alarm(
                id=already_resolved_id,
                status=AlarmStatus.RESOLVED,
                source="test",
                event="alarm.trigger",
                person_id="ma-012",
                room_id="bg-1.23",
                site_id="bg",
                device_id="ylk-t5-10023",
                severity="P0",
                silent=True,
                ack_token="bulk-resolve-already",
                created_at=now - timedelta(minutes=1),
                resolved_at=now - timedelta(minutes=1),
                resolved_by="Ops",
                meta={},
            )
        )
        await session.commit()

    app = create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/v1/alarms/bulk/resolve",
                headers={"X-Admin-Key": "dev-admin-key"},
                json={
                    "alarm_ids": [str(triggered_id), str(already_resolved_id), str(missing_id)],
                    "actor": "BulkOps",
                    "note": "batch resolution",
                },
            )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["requested"] == 3
    assert payload["changed"] == 1
    assert payload["unchanged"] == 1
    assert payload["missing"] == [str(missing_id)]

    async with sessionmaker() as session:
        updated = await session.get(Alarm, triggered_id)
        assert updated is not None
        assert updated.status == AlarmStatus.RESOLVED
        assert updated.resolved_by == "BulkOps"


async def test_bulk_ack_enqueues_jobs_only_for_newly_acknowledged(
    engine, sessionmaker, seeded_db, fake_redis, settings
):
    settings.admin_api_key = "dev-admin-key"
    now = datetime.now(UTC)

    triggered_id = uuid.uuid4()
    acknowledged_id = uuid.uuid4()

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
                ack_token="bulk-ack-triggered",
                created_at=now,
                meta={},
            )
        )
        session.add(
            Alarm(
                id=acknowledged_id,
                status=AlarmStatus.ACKNOWLEDGED,
                source="test",
                event="alarm.trigger",
                person_id="ma-012",
                room_id="bg-1.23",
                site_id="bg",
                device_id="ylk-t5-10023",
                severity="P0",
                silent=True,
                ack_token="bulk-ack-existing",
                created_at=now - timedelta(minutes=1),
                acked_at=now - timedelta(minutes=1),
                acked_by="Existing",
                meta={},
            )
        )
        await session.commit()

    app = create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/v1/alarms/bulk/ack",
                headers={"X-Admin-Key": "dev-admin-key"},
                json={
                    "alarm_ids": [str(triggered_id), str(acknowledged_id)],
                    "acked_by": "BulkResponder",
                    "note": "bulk ack note",
                },
            )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["requested"] == 2
    assert payload["changed"] == 1
    assert payload["unchanged"] == 1
    assert payload["missing"] == []

    queued_ack_jobs = [
        job
        for job in fake_redis.jobs
        if job[0] == "process_alarm_event" and job[1][0].get("event_type") == "alarm.acknowledged"
    ]
    assert len(queued_ack_jobs) == 1
    assert queued_ack_jobs[0][1][0]["alarm_id"] == str(triggered_id)


async def test_metrics_endpoint_exposes_prometheus_text(engine, seeded_db, fake_redis, settings):
    settings.admin_api_key = "dev-admin-key"
    app = create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            health_response = await client.get("/healthz")
            assert health_response.status_code == 200

            metrics_response = await client.get(
                "/metrics", headers={"X-Admin-Key": "dev-admin-key"}
            )

    assert metrics_response.status_code == 200
    assert metrics_response.headers["content-type"].startswith("text/plain")

    body = metrics_response.text
    assert "alarm_broker_http_requests_total" in body
    assert "alarm_broker_http_request_duration_ms_total" in body
    assert "alarm_broker_alarms_by_status" in body
    assert "alarm_broker_notifications_total" in body

    match = re.search(
        r'alarm_broker_http_requests_total\{method="GET",route="/healthz",status_code="200"\}\s+(\d+)',
        body,
    )
    assert match is not None
    assert int(match.group(1)) >= 1


async def test_admin_dashboard_requires_key_and_renders_alarms(
    engine, sessionmaker, seeded_db, fake_redis, settings
):
    settings.admin_api_key = "dev-admin-key"
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
                ack_token="admin-dashboard-alarm",
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
            assert unauthorized.status_code == 401

            await admin_login(client, "dev-admin-key")
            authorized = await client.get("/admin")

    assert authorized.status_code == 200
    assert "text/html" in authorized.headers["content-type"]
    # UUID is displayed truncated to 8 characters
    assert str(alarm_id)[:8] in authorized.text
    assert "triggered" in authorized.text


@pytest.mark.unit
def test_docs_index_links_exist() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    docs_index = repo_root / "docs" / "README.md"
    text = docs_index.read_text(encoding="utf-8")
    candidates: set[str] = set()

    for match in re.finditer(r"\[[^\]]+\]\(([^)]+\.md)\)", text):
        candidates.add(match.group(1).strip())

    for line in text.splitlines():
        if "`" not in line:
            continue
        parts = line.split("`")
        if len(parts) < 3:
            continue
        candidate = parts[1]
        if candidate.endswith(".md"):
            candidates.add(candidate)

    for candidate in sorted(candidates):
        if candidate.startswith("http://") or candidate.startswith("https://"):
            continue
        assert (repo_root / "docs" / candidate).exists(), f"Missing doc: {candidate}"
