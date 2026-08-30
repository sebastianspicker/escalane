"""Alarm lifecycle, readiness, observability, and public-documentation tests."""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient

from escalane.contracts.alarms import AlarmStatus
from escalane.persistence.models import Alarm
from escalane.web.main import create_app
from tests.support.api_test_helpers import app_client
from tests.support.api_test_helpers import make_alarm as _base_alarm
from tests.support.assertions import expect
from tests.support.constants import TEST_ADMIN_API_KEY, value_for_test
from tests.support.helpers import trigger_alarm as _trigger_alarm

pytestmark = [pytest.mark.integration]


def _make_alarm(**overrides) -> Alarm:
    """Build a lifecycle fixture with its stable acknowledgement-token format."""
    overrides.setdefault("ack_token", value_for_test(f"alarm-{uuid.uuid4().hex[:8]}"))
    return _base_alarm(**overrides)


def _expect_bulk_payload(payload: dict, *, requested: int, changed: int, unchanged: int) -> None:
    expect(payload["requested"] == requested)
    expect(payload["changed"] == changed)
    expect(payload["unchanged"] == unchanged)


def _queued_ack_alarm_ids(fake_redis) -> list[str]:
    acknowledgement_jobs = (
        args[0] for name, args in fake_redis.jobs if name == "process_alarm_event"
    )
    return [
        job["alarm_id"]
        for job in acknowledgement_jobs
        if job.get("event_type") == "alarm.acknowledged"
    ]


async def test_readyz_redis_unhealthy_returns_503(engine, seeded_db, settings):
    class BrokenRedis:
        async def get(self, _key: str):
            raise RuntimeError("redis unavailable")

    async with app_client(settings=settings, engine=engine, redis=BrokenRedis()) as client:
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
    async with app_client(settings=settings, engine=engine, redis=fake_redis) as client:
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
    async with app_client(settings=settings, engine=engine, redis=fake_redis) as client:
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
    async with app_client(settings=settings, engine=engine, redis=fake_redis) as client:
        alarm_id = await _trigger_alarm(client)
        response = await client.post(
            f"/v1/alarms/{alarm_id}/resolve",
            headers={"X-Admin-Key": TEST_ADMIN_API_KEY},
            json={"actor": "A" * 121},
        )

    expect(response.status_code == 422)


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

    async with app_client(settings=settings, engine=engine, redis=fake_redis) as client:
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

    async with app_client(settings=settings, engine=engine, redis=fake_redis) as client:
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
    async with app_client(settings=settings, engine=engine, redis=fake_redis) as client:
        health_response = await client.get("/healthz")
        expect(health_response.status_code == 200)
        metrics_response = await client.get("/metrics", headers={"X-Admin-Key": TEST_ADMIN_API_KEY})

    expect(metrics_response.status_code == 200)
    expect(metrics_response.headers["content-type"].startswith("text/plain"))

    body = metrics_response.text
    expect("escalane_http_requests_total" in body)
    expect("escalane_http_request_duration_ms_total" in body)
    expect("escalane_alarms_by_status" in body)
    expect("escalane_notifications_total" in body)

    match = re.search(
        r'escalane_http_requests_total\{method="GET",route="/healthz",status_code="200"\}\s+(\d+)',
        body,
    )
    expect(match is not None)
    expect(int(match.group(1)) >= 1)
