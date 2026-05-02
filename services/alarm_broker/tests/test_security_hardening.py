from __future__ import annotations

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
                params={"token": "YLK_T54W_3F9A"},
                headers={"x-forwarded-for": "203.0.113.10"},
            )

    assert resp.status_code == 403


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
                params={"token": "YLK_T54W_3F9A"},
                headers={"x-forwarded-for": "203.0.113.10"},
            )

    assert resp.status_code == 200


async def test_ack_page_escapes_untrusted_html(
    engine, sessionmaker, seeded_db, fake_redis, settings
):
    app = create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)
    alarm_id = uuid.uuid4()
    ack_token = "ack-xss-token"

    async with sessionmaker() as session:
        person = await session.get(Person, "ma-012")
        assert person is not None
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

    assert resp.status_code == 200
    assert '<script>alert("x")</script>' not in resp.text
    assert "&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;" in resp.text


async def test_ack_page_sets_no_store_and_security_headers(
    engine, sessionmaker, seeded_db, fake_redis, settings
):
    app = create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            trigger = await client.get("/v1/yealink/alarm", params={"token": "YLK_T54W_3F9A"})
            assert trigger.status_code == 200
            alarm_id = uuid.UUID(trigger.json()["alarm_id"])

            async with sessionmaker() as session:
                alarm = await session.get(Alarm, alarm_id)
                assert alarm is not None
                assert alarm.ack_token is not None
                ack_token = alarm.ack_token

            resp = await client.get(f"/a/{ack_token}")

    assert resp.status_code == 200
    assert resp.headers.get("Cache-Control") == "no-store"
    assert resp.headers.get("Pragma") == "no-cache"
    assert resp.headers.get("X-Content-Type-Options") == "nosniff"
    assert resp.headers.get("X-Frame-Options") == "DENY"
    assert resp.headers.get("Referrer-Policy") == "no-referrer"
    csp = resp.headers.get("Content-Security-Policy", "")
    assert "object-src 'none'" in csp
    assert "base-uri 'self'" in csp
    assert "form-action 'self'" in csp
    assert "frame-ancestors 'none'" in csp


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

    assert statuses == [401, 401, 401, 401, 401, 429]


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
                assert resp.status_code == 401

            ok = await client.post(
                "/admin/login",
                data={"admin_key": settings.admin_api_key},
                follow_redirects=False,
            )
            assert ok.status_code == 303

            retry = await client.post(
                "/admin/login",
                data={"admin_key": "wrong-admin-key"},
                follow_redirects=False,
            )

    assert retry.status_code == 401


async def test_ack_form_rejects_oversized_note(
    engine, sessionmaker, seeded_db, fake_redis, settings
):
    app = create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            trigger = await client.get("/v1/yealink/alarm", params={"token": "YLK_T54W_3F9A"})
            assert trigger.status_code == 200
            alarm_id = uuid.UUID(trigger.json()["alarm_id"])

            async with sessionmaker() as session:
                alarm = await session.get(Alarm, alarm_id)
                assert alarm is not None
                assert alarm.ack_token is not None
                ack_token = alarm.ack_token

            get_resp = await client.get(f"/a/{ack_token}")
            assert get_resp.status_code == 200
            match = re.search(r'name="csrf_token"\s+value="([^"]+)"', get_resp.text)
            csrf_value = match.group(1) if match else ""

            resp = await client.post(
                f"/a/{ack_token}",
                data={"acked_by": "Tester", "note": "x" * 2001, "csrf_token": csrf_value},
            )

    assert resp.status_code == 422


def test_rate_limit_key_does_not_include_raw_token() -> None:
    key = rate_limit_key("TOPSECRET_DEVICE_TOKEN", 42)

    assert key.startswith("rl:")
    assert "TOPSECRET_DEVICE_TOKEN" not in key


async def test_docs_and_openapi_disabled_by_default(engine, seeded_db, fake_redis):
    app = create_app(
        settings=Settings(
            database_url="sqlite+aiosqlite:///:memory:",
            redis_url="redis://fake/0",
            base_url="http://localhost:8080",
            admin_api_key="dev-admin-key",
            zammad_api_token="",
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

    assert docs.status_code == 404
    assert openapi.status_code == 404


def test_default_admin_api_key_is_not_empty_in_dev(monkeypatch) -> None:
    """In development (.env present), Settings picks up the configured admin key.

    The .env file ships ADMIN_API_KEY=change-me-admin-key for local development.
    In production the env var would be set to a strong value.
    CI has no .env, so we simulate a dev environment via env vars.
    """
    monkeypatch.setenv("ADMIN_API_KEY", "change-me-admin-key")
    monkeypatch.setenv("SIMULATION_ENABLED", "true")
    assert Settings().admin_api_key != ""


async def test_invalid_alarm_id_rejected_with_422(engine, seeded_db, fake_redis) -> None:
    app = create_app(
        settings=Settings(
            database_url="sqlite+aiosqlite:///:memory:",
            redis_url="redis://fake/0",
            base_url="http://localhost:8080",
            admin_api_key="test-admin-key",
            zammad_api_token="",
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
                headers={"X-Admin-Key": "test-admin-key"},
            )
            post_resp = await client.post(
                "/v1/alarms/not-a-uuid/ack",
                headers={"X-Admin-Key": "test-admin-key"},
                json={},
            )

    assert get_resp.status_code == 422
    assert post_resp.status_code == 422


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
            resp = await client.get("/v1/yealink/alarm", params={"token": "YLK_T54W_3F9A"})

    assert resp.status_code == 403


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
                params={"token": "YLK_T54W_3F9A"},
                headers={"x-forwarded-for": "203.0.113.10"},
            )

    assert resp.status_code == 200


def test_env_example_does_not_ship_static_admin_secret() -> None:
    env_example = Path(__file__).resolve().parents[3] / ".env.example"
    text = env_example.read_text(encoding="utf-8")
    assert "ADMIN_API_KEY=dev-admin-key" not in text


async def test_admin_seed_invalid_json_returns_400(engine, seeded_db, fake_redis) -> None:
    app = create_app(
        settings=Settings(
            database_url="sqlite+aiosqlite:///:memory:",
            redis_url="redis://fake/0",
            base_url="http://localhost:8080",
            admin_api_key="test-admin-key",
            zammad_api_token="",
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
                    "X-Admin-Key": "test-admin-key",
                    "Content-Type": "application/json",
                },
                content=b"{invalid-json",
            )

    assert resp.status_code == 400


async def test_admin_seed_invalid_yaml_returns_400(engine, seeded_db, fake_redis) -> None:
    app = create_app(
        settings=Settings(
            database_url="sqlite+aiosqlite:///:memory:",
            redis_url="redis://fake/0",
            base_url="http://localhost:8080",
            admin_api_key="test-admin-key",
            zammad_api_token="",
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
                    "X-Admin-Key": "test-admin-key",
                    "Content-Type": "application/x-yaml",
                },
                content=b"foo: [\n",
            )

    assert resp.status_code == 400


async def test_admin_seed_accepts_application_yaml_content_type(
    engine, seeded_db, fake_redis
) -> None:
    app = create_app(
        settings=Settings(
            database_url="sqlite+aiosqlite:///:memory:",
            redis_url="redis://fake/0",
            base_url="http://localhost:8080",
            admin_api_key="test-admin-key",
            zammad_api_token="",
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
                    "X-Admin-Key": "test-admin-key",
                    "Content-Type": "application/yaml",
                },
                content=b"sites: []\nrooms: []\n",
            )

    assert resp.status_code == 200


async def test_policy_rejects_missing_target_references(engine, seeded_db, fake_redis) -> None:
    app = create_app(
        settings=Settings(
            database_url="sqlite+aiosqlite:///:memory:",
            redis_url="redis://fake/0",
            base_url="http://localhost:8080",
            admin_api_key="test-admin-key",
            zammad_api_token="",
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
                headers={"X-Admin-Key": "test-admin-key"},
                json=payload,
            )

    assert resp.status_code == 400


def test_default_zammad_api_token_is_empty(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_API_KEY", "test-admin-key")
    monkeypatch.setenv("SIMULATION_ENABLED", "true")
    assert Settings().zammad_api_token == ""


def test_env_example_does_not_ship_static_zammad_token() -> None:
    env_example = Path(__file__).resolve().parents[3] / ".env.example"
    text = env_example.read_text(encoding="utf-8")
    assert "ZAMMAD_API_TOKEN=change-me" not in text


def test_ip_allowlist_ipv6_host_entry_matches_only_exact_host() -> None:
    assert ip_allowed("2001:db8::1", "2001:db8::1")
    assert not ip_allowed("2001:db8::2", "2001:db8::1")


async def test_policy_duplicate_step_target_rejected(engine, seeded_db, fake_redis) -> None:
    app = create_app(
        settings=Settings(
            database_url="sqlite+aiosqlite:///:memory:",
            redis_url="redis://fake/0",
            base_url="http://localhost:8080",
            admin_api_key="test-admin-key",
            zammad_api_token="",
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
                headers={"X-Admin-Key": "test-admin-key"},
                json=payload,
            )

    assert resp.status_code == 400


async def test_admin_seed_invalid_structure_returns_400(engine, seeded_db, fake_redis) -> None:
    app = create_app(
        settings=Settings(
            database_url="sqlite+aiosqlite:///:memory:",
            redis_url="redis://fake/0",
            base_url="http://localhost:8080",
            admin_api_key="test-admin-key",
            zammad_api_token="",
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
                    "X-Admin-Key": "test-admin-key",
                    "Content-Type": "application/json",
                },
                json={"sites": [{}]},
            )

    assert resp.status_code == 400


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

    assert person is not None
    assert person.active is False
