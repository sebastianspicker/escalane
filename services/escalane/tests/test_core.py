"""Direct alarm, outbox, connector, and ingress safety contracts."""

from __future__ import annotations

import hashlib
import logging
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.requests import Request

from escalane.api.admin_session import AdminSession, validate_admin_csrf
from escalane.api.deps import get_client_ip
from escalane.api.routes import ack as ack_route
from escalane.api.routes import admin_configuration_import as import_route
from escalane.api.routes.yealink import _validate_source_ip
from escalane.connectors.mock import MockZammadClient, get_mock_store
from escalane.core.ip_allowlist import ip_allowed
from escalane.core.rate_limit import rate_limit_key
from escalane.core.url_validation import (
    SSRFError,
    pin_url_to_address,
    validate_url_not_internal,
    validate_webhook_host_allowed,
)
from escalane.db.base import Base
from escalane.db.models import Alarm, AlarmStatus, Device, Person, Room, Site
from escalane.services.event_publisher import EventPublisher
from escalane.services.event_service import enqueue_alarm_acked_event
from escalane.services.trigger_result import TriggerResult
from escalane.services.trigger_service import TriggerService
from escalane.services.webhook_delivery import _post_webhook_to_validated_address
from escalane.settings import Settings


class _MemoryRedis:
    """Tiny in-process Redis boundary for direct idempotency tests."""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def set(self, key: str, value: str, *, ex: int, nx: bool) -> bool:
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    async def eval(self, script: str, _keys: int, key: str, value: object) -> int:
        if "incr" in script:
            self.values[key] = str(int(self.values.get(key, "0")) + 1)
            return int(self.values[key])
        if self.values.get(key) == str(value):
            del self.values[key]
            return 1
        return 0

    async def enqueue_job(self, _name: str, *_args: object, **_kwargs: object) -> object:
        return object()


@pytest.mark.asyncio
async def test_browser_security_failures_do_not_mutate_import_or_acknowledgement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise direct route ordering before their durable effects are reached."""
    browser_session = AdminSession(token="browser", operator_name="operator", csrf_token="csrf")
    import_audits: list[object] = []
    imported: list[object] = []
    acknowledged: list[object] = []
    request = SimpleNamespace(state=SimpleNamespace(request_id="request"))

    async def action_session(_request, _settings, _cookie, submitted):
        validate_admin_csrf(browser_session, submitted)
        return browser_session

    async def apply_seed(_session, *, data, settings):
        imported.append((data, settings))

    monkeypatch.setattr(import_route, "_action_session", action_session)
    monkeypatch.setattr(import_route, "_requested_locale", lambda *_: "en")
    monkeypatch.setattr(import_route, "parse_seed_payload", lambda *_: {"sites": []})
    monkeypatch.setattr(
        import_route, "add_admin_audit_event", lambda *args, **kwargs: import_audits.append(kwargs)
    )
    monkeypatch.setattr(import_route, "apply_seed_payload", apply_seed)
    payload = "sites: []"
    digest = hashlib.sha256(payload.encode()).hexdigest()

    for csrf in (None, "wrong"):
        with pytest.raises(HTTPException, match="csrf_invalid"):
            await import_route.admin_import_submit(
                request, payload, "apply", digest, csrf, "browser", object(), object()
            )
    with pytest.raises(HTTPException, match="import_preview_is_stale"):
        await import_route.admin_import_submit(
            request, payload, "apply", "stale", "csrf", "browser", object(), object()
        )
    assert not import_audits and not imported

    response = await import_route.admin_import_submit(
        request, payload, "apply", digest, "csrf", "browser", object(), object()
    )
    assert response.status_code == 303
    assert len(import_audits) == len(imported) == 1

    class AckRequest:
        state = SimpleNamespace()

        async def form(self):
            return {"csrf_token": self.csrf_token, "acked_by": "operator", "note": ""}

    ack_request = AckRequest()
    monkeypatch.setattr(ack_route, "get_app_settings", lambda _: object())
    monkeypatch.setattr(ack_route, "get_redis", lambda _: object())
    monkeypatch.setattr(ack_route, "_check_ack_rate_limit", AsyncMock())
    monkeypatch.setattr(
        ack_route, "get_alarm_by_ack_token", AsyncMock(return_value=SimpleNamespace(id="alarm"))
    )
    monkeypatch.setattr(ack_route, "_locale", lambda *_: "en")
    monkeypatch.setattr(
        ack_route,
        "_apply_acknowledgement",
        AsyncMock(side_effect=lambda *args: acknowledged.append(args)),
    )
    monkeypatch.setattr(ack_route, "enrich_alarm_context", AsyncMock(return_value=object()))
    monkeypatch.setattr(ack_route, "render_ack_page", lambda *_args, **_kwargs: "acknowledged")

    for submitted in ("", "wrong"):
        ack_request.csrf_token = submitted
        with pytest.raises(HTTPException, match="Security validation failed"):
            await ack_route.ack_submit(
                ack_request, "ack-token", csrf_token="csrf", session=object()
            )
    assert not acknowledged

    ack_request.csrf_token = "csrf"
    await ack_route.ack_submit(ack_request, "ack-token", csrf_token="csrf", session=object())
    assert len(acknowledged) == 1


@pytest.mark.asyncio
async def test_outbox_event_is_enqueued_with_a_stable_redacted_job_id() -> None:
    redis = AsyncMock()
    alarm_id = uuid.uuid4()

    result = await enqueue_alarm_acked_event(
        redis,
        alarm_id=alarm_id,
        acked_by=None,
        note="operator note",
        logger=logging.getLogger(__name__),
    )

    assert result.success
    name, payload = redis.enqueue_job.await_args.args
    assert name == EventPublisher.JOB_NAME
    assert payload["alarm_id"] == str(alarm_id)
    assert payload["acknowledged_by"] == "unknown"
    assert redis.enqueue_job.await_args.kwargs["_job_id"] == (
        f"process_alarm_event:alarm.acknowledged:{alarm_id}"
    )


@pytest.mark.asyncio
async def test_mock_connector_preserves_the_simulation_boundary() -> None:
    store = get_mock_store()
    store.clear()
    connector = MockZammadClient()

    ticket_id = await connector.create_ticket({"title": "Alarm", "group": "on-call"})

    assert ticket_id >= 1001
    assert store.get_all()[-1].payload["title"] == "Alarm"


def test_ingress_allowlist_fails_closed_and_rate_keys_do_not_expose_tokens() -> None:
    assert ip_allowed("203.0.113.7", "203.0.113.0/24")
    assert not ip_allowed("203.0.114.7", "203.0.113.0/24")
    assert not ip_allowed("203.0.113.7", "not-a-cidr")
    key = rate_limit_key("private-device-token", 7)
    assert key.startswith("rl:")
    assert "private-device-token" not in key


def test_trigger_result_has_stable_success_and_failure_contracts() -> None:
    alarm_id = uuid.uuid4()
    ok = TriggerResult.ok(alarm_id, status=AlarmStatus.TRIGGERED)
    failure = TriggerResult.error(403, "denied")

    assert ok.success and ok.alarm_id == alarm_id
    assert not failure.success and failure.error_code == 403


def test_untrusted_forwarded_for_cannot_bypass_ingress_allowlist() -> None:
    request = Request(
        {
            "type": "http",
            "headers": [(b"x-forwarded-for", b"203.0.113.7")],
            "client": ("198.51.100.9", 1234),
            "scheme": "http",
            "path": "/v1/yealink/alarm",
        }
    )
    settings = SimpleNamespace(
        trusted_proxy_cidrs="", yelk_ip_allowlist="203.0.113.0/24", simulation_enabled=False
    )

    assert get_client_ip(request, settings) == "198.51.100.9"
    with pytest.raises(HTTPException, match="IP not allowed"):
        _validate_source_ip(request, settings)


@pytest.mark.asyncio
async def test_webhook_ssrf_policy_requires_allowlisted_public_pinned_targets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(SSRFError, match="not in WEBHOOK_ALLOWED_HOSTS"):
        validate_webhook_host_allowed("https://other.example/hook", "hooks.example")
    validate_webhook_host_allowed("https://hooks.example/hook", "hooks.example")

    class Resolver:
        address = "127.0.0.1"

        async def getaddrinfo(self, *_args: object, **_kwargs: object):
            return [(0, 0, 0, "", (self.address, 0))]

    resolver = Resolver()
    monkeypatch.setattr("escalane.core.url_validation.asyncio.get_running_loop", lambda: resolver)
    for blocked in ("127.0.0.1", "192.0.2.1"):
        resolver.address = blocked
        with pytest.raises(SSRFError, match="blocked IP range"):
            await validate_url_not_internal("https://hooks.example/hook")

    pinned, host, sni = pin_url_to_address("https://hooks.example:8443/hook?key=secret", "8.8.8.8")
    assert pinned == "https://8.8.8.8:8443/hook?key=secret"
    assert (host, sni) == ("hooks.example:8443", "hooks.example")


@pytest.mark.asyncio
async def test_unknown_trigger_credential_is_not_reflected() -> None:
    secret = "unknown-device-secret"
    session = AsyncMock()
    session.scalar.return_value = None
    result = await TriggerService(
        session,
        _MemoryRedis(),
        Settings(simulation_enabled=True),
    ).process_trigger(secret, "127.0.0.1", "test")

    assert result.error_code == 404
    assert secret not in (result.error_message or "")


@pytest.mark.asyncio
async def test_duplicate_trigger_reuses_one_alarm_id(tmp_path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'alarms.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    redis = _MemoryRedis()
    settings = Settings(simulation_enabled=True)
    token = "direct-test-token"
    try:
        async with sessions() as session:
            session.add_all(
                [
                    Site(id="site", name="Site"),
                    Room(id="room", site_id="site", label="Room"),
                    Person(id="person", display_name="Person"),
                    Device(id="device", device_token=token, person_id="person", room_id="room"),
                ]
            )
            await session.commit()

        async with sessions() as first_session:
            first = await TriggerService(first_session, redis, settings).process_trigger(
                token, "127.0.0.1", "first"
            )
        async with sessions() as second_session:
            second = await TriggerService(second_session, redis, settings).process_trigger(
                token, "127.0.0.1", "second"
            )
            alarms = list((await second_session.scalars(select(Alarm))).all())

        assert first.success and second.success and second.is_duplicate
        assert first.alarm_id == second.alarm_id
        assert len(alarms) == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_webhook_provider_errors_redact_secret_urls(caplog: pytest.LogCaptureFixture) -> None:
    secret = "provider-secret"

    class FailingClient:
        async def post(self, url: str, **_kwargs: object) -> None:
            raise httpx.ConnectError(f"request failed for {url}?token={secret}")

    caplog.set_level(logging.WARNING, logger="escalane")
    error = await _post_webhook_to_validated_address(
        FailingClient(),
        f"https://user:{secret}@hooks.example/private/{secret}?key={secret}",
        {"alarm_id": "alarm"},
        "8.8.8.8",
        "target",
        "delivery",
    )

    assert isinstance(error, httpx.ConnectError)
    record = next(
        record
        for record in caplog.records
        if record.message == "webhook_notification_address_failed"
    )
    assert record.url == "https://hooks.example"
    assert secret not in caplog.text and secret not in record.error
