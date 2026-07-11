from __future__ import annotations

try:
    from tests.assertions import expect
except ModuleNotFoundError:
    from assertions import expect

import re
import uuid
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from alarm_broker.api.main import create_app
from alarm_broker.core.ip_allowlist import ip_allowed
from alarm_broker.core.rate_limit import rate_limit_key
from alarm_broker.db.models import Alarm, AlarmStatus, Person
from alarm_broker.seed import apply_seed
from alarm_broker.settings import Settings

try:
    from tests.constants import (
        EMPTY_SECRET_VALUE,
        TEST_ADMIN_API_KEY,
        TEST_DEVICE_TOKEN,
        value_for_test,
    )
except ModuleNotFoundError:
    from constants import EMPTY_SECRET_VALUE, TEST_ADMIN_API_KEY, TEST_DEVICE_TOKEN, value_for_test

pytestmark = [pytest.mark.security]


async def test_untrusted_x_forwarded_for_does_not_bypass_ip_allowlist(
    engine, seeded_db, fake_redis, settings
):
    payload = settings.model_dump()
    payload.update({"yelk_ip_allowlist": "203.0.113.0/24", "simulation_enabled": False})
    app = create_app(
        settings=Settings(**payload),
        injected_engine=engine,
        injected_redis=fake_redis,
    )

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/v1/yealink/alarm",
                params={"token": TEST_DEVICE_TOKEN},
                headers={"x-forwarded-for": "203.0.113.10"},
            )

    expect(resp.status_code == 403)


async def test_trusted_proxy_allows_forwarded_client_ip(engine, seeded_db, fake_redis, settings):
    payload = settings.model_dump()
    payload.update(
        {
            "yelk_ip_allowlist": "203.0.113.0/24",
            "trusted_proxy_cidrs": "127.0.0.1/32,::1/128",
        }
    )
    app = create_app(
        settings=Settings(**payload),
        injected_engine=engine,
        injected_redis=fake_redis,
    )

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/v1/yealink/alarm",
                params={"token": TEST_DEVICE_TOKEN},
                headers={"x-forwarded-for": "203.0.113.10"},
            )

    expect(resp.status_code == 200)


async def test_ack_page_escapes_untrusted_html(
    engine, sessionmaker, seeded_db, fake_redis, settings
):
    app = create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)
    alarm_id = uuid.uuid4()
    ack_token = value_for_test("ack-xss")

    async with sessionmaker() as session:
        person = await session.get(Person, "ma-012")
        expect(person is not None)
        person.display_name = '<script>alert("x")</script>'
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
                meta={},
            )
        )
        await session.commit()

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(f"/a/{ack_token}")

    expect(resp.status_code == 200)
    expect('<script>alert("x")</script>' not in resp.text)
    expect("&lt;script&gt;alert(" in resp.text)
    expect("&lt;/script&gt;" in resp.text)


async def test_ack_page_sets_no_store_and_security_headers(
    engine, sessionmaker, seeded_db, fake_redis, settings
):
    app = create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            trigger = await client.get("/v1/yealink/alarm", params={"token": TEST_DEVICE_TOKEN})
            expect(trigger.status_code == 200)
            alarm_id = uuid.UUID(trigger.json()["alarm_id"])

            async with sessionmaker() as session:
                alarm = await session.get(Alarm, alarm_id)
                expect(alarm is not None)
                expect(alarm.ack_token is not None)
                ack_token = alarm.ack_token

            resp = await client.get(f"/a/{ack_token}")

    expect(resp.status_code == 200)
    expect(resp.headers.get("Cache-Control") == "no-store")
    expect(resp.headers.get("Pragma") == "no-cache")
    expect(resp.headers.get("X-Content-Type-Options") == "nosniff")
    expect(resp.headers.get("X-Frame-Options") == "DENY")
    expect(resp.headers.get("Referrer-Policy") == "no-referrer")
    csp = resp.headers.get("Content-Security-Policy", "")
    expect("object-src 'none'" in csp)
    expect("base-uri 'self'" in csp)
    expect("form-action 'self'" in csp)
    expect("frame-ancestors 'none'" in csp)


async def test_admin_login_failed_attempts_are_rate_limited(
    engine, seeded_db, fake_redis, settings
) -> None:
    app = create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)

    statuses: list[int] = []
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            for _ in range(6):
                resp = await client.post(
                    "/admin/login",
                    data={"admin_key": "wrong-admin-key"},
                    follow_redirects=False,
                )
                statuses.append(resp.status_code)

    expect(statuses == [401, 401, 401, 401, 401, 429])


async def test_admin_login_success_clears_failed_attempt_counter(
    engine, seeded_db, fake_redis, settings
) -> None:
    app = create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            for _ in range(4):
                resp = await client.post(
                    "/admin/login",
                    data={"admin_key": "wrong-admin-key"},
                    follow_redirects=False,
                )
                expect(resp.status_code == 401)

            ok = await client.post(
                "/admin/login",
                data={"admin_key": settings.admin_api_key},
                follow_redirects=False,
            )
            expect(ok.status_code == 303)

            retry = await client.post(
                "/admin/login",
                data={"admin_key": "wrong-admin-key"},
                follow_redirects=False,
            )

    expect(retry.status_code == 401)


async def test_ack_form_rejects_oversized_note(
    engine, sessionmaker, seeded_db, fake_redis, settings
):
    app = create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            trigger = await client.get("/v1/yealink/alarm", params={"token": TEST_DEVICE_TOKEN})
            expect(trigger.status_code == 200)
            alarm_id = uuid.UUID(trigger.json()["alarm_id"])

            async with sessionmaker() as session:
                alarm = await session.get(Alarm, alarm_id)
                expect(alarm is not None)
                expect(alarm.ack_token is not None)
                ack_token = alarm.ack_token

            get_resp = await client.get(f"/a/{ack_token}")
            expect(get_resp.status_code == 200)
            match = re.search(r'name="csrf_token"\s+value="([^"]+)"', get_resp.text)
            csrf_value = match.group(1) if match else ""

            resp = await client.post(
                f"/a/{ack_token}",
                data={"acked_by": "Tester", "note": "x" * 2001, "csrf_token": csrf_value},
            )

    expect(resp.status_code == 422)


def test_rate_limit_key_does_not_include_raw_token() -> None:
    key = rate_limit_key("TOPSECRET_DEVICE_TOKEN", 42)

    expect(key.startswith("rl:"))
    expect("TOPSECRET_DEVICE_TOKEN" not in key)


async def test_docs_and_openapi_disabled_by_default(engine, seeded_db, fake_redis):
    app = create_app(
        settings=Settings(
            database_url="sqlite+aiosqlite:///:memory:",
            redis_url="redis://fake/0",
            base_url="http://localhost:8080",
            admin_api_key=TEST_ADMIN_API_KEY,
            zammad_api_token=EMPTY_SECRET_VALUE,
            sendxms_enabled=False,
            signal_enabled=False,
        ),
        injected_engine=engine,
        injected_redis=fake_redis,
    )

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            docs = await client.get("/docs")
            openapi = await client.get("/openapi.json")

    expect(docs.status_code == 404)
    expect(openapi.status_code == 404)


def test_default_admin_api_key_is_not_empty_in_dev(monkeypatch) -> None:
    """In development (.env present), Settings picks up the configured admin key.

    The .env file ships ADMIN_API_KEY=change-me-admin-key for local development.
    In production the env var would be set to a strong value.
    CI has no .env, so we simulate a dev environment via env vars.
    """
    monkeypatch.setenv("ADMIN_API_KEY", "change-me-admin-key")
    monkeypatch.setenv("SIMULATION_ENABLED", "true")
    expect(Settings().admin_api_key != "")


async def test_invalid_alarm_id_rejected_with_422(engine, seeded_db, fake_redis) -> None:
    app = create_app(
        settings=Settings(
            database_url="sqlite+aiosqlite:///:memory:",
            redis_url="redis://fake/0",
            base_url="http://localhost:8080",
            admin_api_key=TEST_ADMIN_API_KEY,
            zammad_api_token=EMPTY_SECRET_VALUE,
            sendxms_enabled=False,
            signal_enabled=False,
        ),
        injected_engine=engine,
        injected_redis=fake_redis,
    )

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            get_resp = await client.get(
                "/v1/alarms/not-a-uuid",
                headers={"X-Admin-Key": TEST_ADMIN_API_KEY},
            )
            post_resp = await client.post(
                "/v1/alarms/not-a-uuid/ack",
                headers={"X-Admin-Key": TEST_ADMIN_API_KEY},
                json={},
            )

    expect(get_resp.status_code == 422)
    expect(post_resp.status_code == 422)


async def test_invalid_allowlist_config_fails_closed_without_500(
    engine, seeded_db, fake_redis, settings
):
    payload = settings.model_dump()
    payload.update({"yelk_ip_allowlist": "not-a-cidr", "simulation_enabled": False})
    app = create_app(
        settings=Settings(**payload),
        injected_engine=engine,
        injected_redis=fake_redis,
    )

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/v1/yealink/alarm", params={"token": TEST_DEVICE_TOKEN})

    expect(resp.status_code == 403)


async def test_invalid_trusted_proxy_config_is_ignored_without_500(
    engine, seeded_db, fake_redis, settings
):
    payload = settings.model_dump()
    payload.update({"trusted_proxy_cidrs": "invalid-cidr"})
    app = create_app(
        settings=Settings(**payload),
        injected_engine=engine,
        injected_redis=fake_redis,
    )

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/v1/yealink/alarm",
                params={"token": TEST_DEVICE_TOKEN},
                headers={"x-forwarded-for": "203.0.113.10"},
            )

    expect(resp.status_code == 200)


def test_env_example_does_not_ship_static_admin_secret() -> None:
    env_example = Path(__file__).resolve().parents[3] / ".env.example"
    text = env_example.read_text(encoding="utf-8")
    expect(f"ADMIN_API_KEY={TEST_ADMIN_API_KEY}" not in text)


async def test_admin_seed_invalid_json_returns_400(engine, seeded_db, fake_redis) -> None:
    app = create_app(
        settings=Settings(
            database_url="sqlite+aiosqlite:///:memory:",
            redis_url="redis://fake/0",
            base_url="http://localhost:8080",
            admin_api_key=TEST_ADMIN_API_KEY,
            zammad_api_token=EMPTY_SECRET_VALUE,
            sendxms_enabled=False,
            signal_enabled=False,
        ),
        injected_engine=engine,
        injected_redis=fake_redis,
    )

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/v1/admin/seed",
                headers={
                    "X-Admin-Key": TEST_ADMIN_API_KEY,
                    "Content-Type": "application/json",
                },
                content=b"{invalid-json",
            )

    expect(resp.status_code == 400)


async def test_admin_seed_invalid_yaml_returns_400(engine, seeded_db, fake_redis) -> None:
    app = create_app(
        settings=Settings(
            database_url="sqlite+aiosqlite:///:memory:",
            redis_url="redis://fake/0",
            base_url="http://localhost:8080",
            admin_api_key=TEST_ADMIN_API_KEY,
            zammad_api_token=EMPTY_SECRET_VALUE,
            sendxms_enabled=False,
            signal_enabled=False,
        ),
        injected_engine=engine,
        injected_redis=fake_redis,
    )

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/v1/admin/seed",
                headers={
                    "X-Admin-Key": TEST_ADMIN_API_KEY,
                    "Content-Type": "application/x-yaml",
                },
                content=b"foo: [\n",
            )

    expect(resp.status_code == 400)


async def test_admin_seed_accepts_application_yaml_content_type(
    engine, seeded_db, fake_redis
) -> None:
    app = create_app(
        settings=Settings(
            database_url="sqlite+aiosqlite:///:memory:",
            redis_url="redis://fake/0",
            base_url="http://localhost:8080",
            admin_api_key=TEST_ADMIN_API_KEY,
            zammad_api_token=EMPTY_SECRET_VALUE,
            sendxms_enabled=False,
            signal_enabled=False,
        ),
        injected_engine=engine,
        injected_redis=fake_redis,
    )

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/v1/admin/seed",
                headers={
                    "X-Admin-Key": TEST_ADMIN_API_KEY,
                    "Content-Type": "application/yaml",
                },
                content=b"sites: []\nrooms: []\n",
            )

    expect(resp.status_code == 200)


async def test_policy_rejects_missing_target_references(engine, seeded_db, fake_redis) -> None:
    app = create_app(
        settings=Settings(
            database_url="sqlite+aiosqlite:///:memory:",
            redis_url="redis://fake/0",
            base_url="http://localhost:8080",
            admin_api_key=TEST_ADMIN_API_KEY,
            zammad_api_token=EMPTY_SECRET_VALUE,
            sendxms_enabled=False,
            signal_enabled=False,
        ),
        injected_engine=engine,
        injected_redis=fake_redis,
    )

    payload = {
        "policy_id": "default",
        "name": "Default",
        "targets": [
            {
                "id": "t1",
                "label": "Target 1",
                "channel": "sms",
                "address": "+491234",
                "enabled": True,
            }
        ],
        "steps": [{"step_no": 1, "after_seconds": 60, "target_ids": ["missing-target"]}],
    }

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/v1/admin/escalation-policy",
                headers={"X-Admin-Key": TEST_ADMIN_API_KEY},
                json=payload,
            )

    expect(resp.status_code == 400)


def test_default_zammad_api_token_is_empty(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_API_KEY", TEST_ADMIN_API_KEY)
    monkeypatch.setenv("SIMULATION_ENABLED", "true")
    expect(Settings().zammad_api_token == EMPTY_SECRET_VALUE)


def test_env_example_does_not_ship_static_zammad_token() -> None:
    env_example = Path(__file__).resolve().parents[3] / ".env.example"
    text = env_example.read_text(encoding="utf-8")
    expect("ZAMMAD_API_TOKEN=change-me" not in text)


def test_ip_allowlist_ipv6_host_entry_matches_only_exact_host() -> None:
    expect(ip_allowed("2001:db8::1", "2001:db8::1"))
    expect(not ip_allowed("2001:db8::2", "2001:db8::1"))


async def test_policy_duplicate_step_target_rejected(engine, seeded_db, fake_redis) -> None:
    app = create_app(
        settings=Settings(
            database_url="sqlite+aiosqlite:///:memory:",
            redis_url="redis://fake/0",
            base_url="http://localhost:8080",
            admin_api_key=TEST_ADMIN_API_KEY,
            zammad_api_token=EMPTY_SECRET_VALUE,
            sendxms_enabled=False,
            signal_enabled=False,
        ),
        injected_engine=engine,
        injected_redis=fake_redis,
    )

    payload = {
        "policy_id": "default",
        "name": "Default",
        "targets": [
            {
                "id": "t1",
                "label": "Target 1",
                "channel": "sms",
                "address": "+491234",
                "enabled": True,
            }
        ],
        "steps": [{"step_no": 1, "after_seconds": 60, "target_ids": ["t1", "t1"]}],
    }

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/v1/admin/escalation-policy",
                headers={"X-Admin-Key": TEST_ADMIN_API_KEY},
                json=payload,
            )

    expect(resp.status_code == 400)


async def test_admin_seed_invalid_structure_returns_400(engine, seeded_db, fake_redis) -> None:
    app = create_app(
        settings=Settings(
            database_url="sqlite+aiosqlite:///:memory:",
            redis_url="redis://fake/0",
            base_url="http://localhost:8080",
            admin_api_key=TEST_ADMIN_API_KEY,
            zammad_api_token=EMPTY_SECRET_VALUE,
            sendxms_enabled=False,
            signal_enabled=False,
        ),
        injected_engine=engine,
        injected_redis=fake_redis,
    )

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/v1/admin/seed",
                headers={
                    "X-Admin-Key": TEST_ADMIN_API_KEY,
                    "Content-Type": "application/json",
                },
                json={"sites": [{}]},
            )

    expect(resp.status_code == 400)


async def test_seed_env_false_expands_to_boolean_false(sessionmaker, settings, monkeypatch) -> None:
    monkeypatch.setenv("TEST_ACTIVE", "false")
    raw = {
        "persons": [
            {"id": "p1", "display_name": "Person 1", "active": "${TEST_ACTIVE}"},
        ]
    }

    async with sessionmaker() as session:
        await apply_seed(session, raw, settings)
        person = await session.get(Person, "p1")

    expect(person is not None)
    expect(person.active is False)
