"""Admin browser, URL-validation, error-shaping, and lifespan contracts."""

from __future__ import annotations

import hashlib
import socket
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request

from escalane.config.errors import (
    AuthenticationError,
    AuthorizationError,
    ConfigurationError,
    ConflictError,
    ConnectorError,
    EscalaneError,
    NotFoundError,
    RateLimitError,
    ValidationError,
)
from escalane.contracts.alarms import AlarmStatus
from escalane.persistence.models import Alarm
from escalane.security.url_validation import (
    RetryableSSRFError,
    SSRFError,
    pin_url_to_address,
    redact_url_for_logging,
    validate_url_not_internal,
    validate_webhook_host_allowed,
)
from escalane.web import main
from tests.support.admin_test_helpers import csrf_token, login_admin
from tests.support.api_test_helpers import app_client, make_alarm

pytestmark = pytest.mark.integration


async def test_admin_login_lockout_configuration_and_locale_paths(
    engine, fake_redis, settings
) -> None:
    """The login form localizes configuration and bounded failed-attempt responses."""
    settings.admin_api_key = ""
    async with app_client(settings=settings, engine=engine, redis=fake_redis) as client:
        unconfigured = await client.post("/admin/login?lang=de", data={"admin_key": "anything"})
        page = await client.get("/admin/login?lang=de")

    assert unconfigured.status_code == 500
    assert "nicht konfiguriert" in unconfigured.text
    assert page.status_code == 200
    assert "ui_locale=de" in page.headers["set-cookie"]


async def test_admin_login_lockout_then_successful_logout_clears_server_session(
    engine, fake_redis, settings
) -> None:
    """Failed login rate limiting does not prevent a later named session from clean logout."""
    settings.admin_api_key = "correct-key"
    async with app_client(settings=settings, engine=engine, redis=fake_redis) as client:
        attempts = [
            await client.post("/admin/login", data={"admin_key": "wrong"}) for _ in range(6)
        ]
        limited = await client.post("/admin/login", data={"admin_key": "wrong"})
        assert [response.status_code for response in attempts] == [401, 401, 401, 401, 401, 429]
        assert limited.status_code == 429

        fake_redis.advance(61)
        login = await login_admin(client, "correct-key", "  Night Ops  ")
        dashboard = await client.get("/admin")
        logout = await client.post(
            "/admin/logout",
            data={"csrf_token": csrf_token(dashboard.text)},
            follow_redirects=False,
        )
        after_logout = await client.get("/admin")

    assert login.status_code == 303
    assert "Night Ops" in dashboard.text
    assert logout.status_code == 303
    assert 'admin_session=""' in logout.headers["set-cookie"]
    assert after_logout.status_code == 401


async def test_admin_import_preview_rejects_stale_apply_and_system_simulation_actions(
    engine, seeded_db, fake_redis, settings
) -> None:
    """The import review hash and simulation controls retain their session protections."""
    settings.admin_api_key = "correct-key"
    settings.simulation_enabled = True
    payload = "sites: []\n"
    digest = hashlib.sha256(payload.encode()).hexdigest()
    async with app_client(settings=settings, engine=engine, redis=fake_redis) as client:
        assert (await login_admin(client, "correct-key", "Import Ops")).status_code == 303
        import_page = await client.get("/admin/configuration/import")
        token = csrf_token(import_page.text)
        preview = await client.post(
            "/admin/configuration/import",
            data={"seed_text": payload, "action": "preview", "csrf_token": token},
        )
        stale_apply = await client.post(
            "/admin/configuration/import",
            data={
                "seed_text": payload,
                "action": "apply",
                "content_hash": "stale",
                "csrf_token": token,
            },
        )
        system = await client.get("/admin/system")
        simulation = await client.get("/admin/simulation")
        clear = await client.post(
            "/admin/simulation/clear",
            data={"csrf_token": csrf_token(simulation.text)},
            follow_redirects=False,
        )

    assert preview.status_code == 200
    assert digest in preview.text
    assert "sites" in preview.text
    assert stale_apply.status_code == 409
    assert "import_preview_is_stale" in stale_apply.text
    assert system.status_code == 200
    assert "Application" in system.text and "Database" in system.text
    assert simulation.status_code == 200
    assert clear.status_code == 303


async def test_admin_alarm_detail_ack_note_bulk_validation_and_export(
    engine, sessionmaker, seeded_db, fake_redis, settings
) -> None:
    """Browser alarm actions mutate state, attribute notes, and retain form validation."""
    settings.admin_api_key = "correct-key"
    alarm_id = uuid.uuid4()
    async with sessionmaker() as session:
        session.add(make_alarm(alarm_id=alarm_id, status=AlarmStatus.TRIGGERED))
        await session.commit()

    async with app_client(settings=settings, engine=engine, redis=fake_redis) as client:
        assert (await login_admin(client, "correct-key", "Alarm Ops")).status_code == 303
        detail = await client.get(f"/admin/alarms/{alarm_id}")
        token = csrf_token(detail.text)
        acknowledged = await client.post(
            f"/admin/alarms/{alarm_id}/ack",
            data={"csrf_token": token, "note": "Taking ownership"},
            follow_redirects=False,
        )
        noted = await client.post(
            f"/admin/alarms/{alarm_id}/notes",
            data={"csrf_token": token, "note": "Follow-up recorded"},
            follow_redirects=False,
        )
        invalid_bulk = await client.post(
            "/admin/alarms/bulk",
            data={"csrf_token": token, "action": "ack"},
        )
        exported = await client.get("/admin/export?format=json")

    async with sessionmaker() as session:
        persisted = await session.get(Alarm, alarm_id)

    assert detail.status_code == 200
    assert "Alarm Ops" in detail.text
    assert acknowledged.status_code == 303
    assert noted.status_code == 303
    assert invalid_bulk.status_code == 422
    assert exported.status_code == 200
    assert persisted is not None and persisted.status is AlarmStatus.ACKNOWLEDGED
    assert str(alarm_id) in exported.text


def test_url_validation_rejects_untrusted_forms_and_preserves_ipv6_authority() -> None:
    """Webhook validation rejects ambiguous targets while preserving valid IPv6 host headers."""
    with pytest.raises(SSRFError, match="empty"):
        validate_webhook_host_allowed("https://hooks.example.test/path", "")
    with pytest.raises(SSRFError, match="not in"):
        validate_webhook_host_allowed("https://hooks.example.test/path", "other.example.test")
    with pytest.raises(SSRFError, match="invalid host"):
        pin_url_to_address("https://hooks.example.test:bad/path", "8.8.8.8")

    pinned, host_header, hostname = pin_url_to_address(
        "https://[2001:db8::1]/hook", "2001:4860:4860::8888"
    )
    assert pinned == "https://[2001:4860:4860::8888]/hook"
    assert host_header == "[2001:db8::1]"
    assert hostname == "2001:db8::1"
    assert redact_url_for_logging("https://hooks.example.test:bad/path") == "<invalid-url>"


async def test_url_validation_rejects_private_and_empty_dns_answers() -> None:
    """DNS policy fails closed for private addresses and transient empty resolver answers."""
    private_loop = MagicMock()
    private_loop.getaddrinfo = AsyncMock(
        return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 0))]
    )
    with (
        patch(
            "escalane.security.url_validation.asyncio.get_running_loop", return_value=private_loop
        ),
        pytest.raises(SSRFError, match="blocked IP"),
    ):
        await validate_url_not_internal("https://hooks.example.test/path")

    empty_loop = MagicMock()
    empty_loop.getaddrinfo = AsyncMock(return_value=[])
    with (
        patch("escalane.security.url_validation.asyncio.get_running_loop", return_value=empty_loop),
        pytest.raises(RetryableSSRFError, match="Cannot resolve hostname"),
    ):
        await validate_url_not_internal("https://hooks.example.test/path")


async def test_main_error_handlers_keep_domain_and_browser_error_shapes() -> None:
    """Domain and browser failures retain their promised public response shapes."""
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/admin/example",
            "headers": [(b"accept-language", b"de")],
            "query_string": b"",
            "server": ("test", 80),
            "scheme": "http",
        }
    )
    request.state.request_id = "req-42"
    browser = await main.browser_http_error_handler(
        request, StarletteHTTPException(status_code=401, detail="login_required")
    )
    api_request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/v1/example",
            "headers": [],
            "query_string": b"",
            "server": ("test", 80),
            "scheme": "http",
        }
    )
    api = await main.browser_http_error_handler(
        api_request, StarletteHTTPException(status_code=418, detail="teapot")
    )
    handlers = [
        (main.validation_error_handler, ValidationError("bad", field="name"), 400),
        (main.not_found_error_handler, NotFoundError("alarm", "missing"), 404),
        (main.conflict_error_handler, ConflictError("conflict"), 409),
        (main.authentication_error_handler, AuthenticationError("unauthenticated"), 401),
        (main.authorization_error_handler, AuthorizationError("forbidden"), 403),
        (main.rate_limit_error_handler, RateLimitError(5, 60), 429),
        (main.configuration_error_handler, ConfigurationError("secret detail"), 500),
        (
            main.connector_error_handler,
            ConnectorError("webhook", "send", RuntimeError("secret")),
            502,
        ),
        (main.generic_error_handler, EscalaneError("unexpected"), 500),
    ]
    responses = [await handler(api_request, error) for handler, error, _ in handlers]

    assert browser.status_code == 401 and "Melden Sie sich an" in browser.body.decode()
    assert api.status_code == 418 and api.body == b'{"detail":"teapot"}'
    assert [response.status_code for response in responses] == [
        expected for _, _, expected in handlers
    ]
    assert b"secret detail" not in responses[6].body
    assert b"secret" not in responses[7].body


async def test_main_lifespan_cleanup_closes_owned_resources_even_when_close_fails() -> None:
    """Lifespan cleanup attempts both owned resources and leaves injected resources untouched."""
    redis = MagicMock()
    redis.close = AsyncMock(side_effect=RuntimeError("down"))
    engine = MagicMock()
    engine.dispose = AsyncMock(side_effect=RuntimeError("down"))

    await main._close_lifespan_resources(
        engine=engine,
        redis=redis,
        injected_engine=None,
        injected_redis=None,
    )
    await main._close_lifespan_resources(
        engine=engine,
        redis=redis,
        injected_engine=engine,
        injected_redis=redis,
    )

    assert redis.close.await_count == 1
    assert engine.dispose.await_count == 1
