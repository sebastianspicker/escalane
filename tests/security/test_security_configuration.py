"""Secure-default configuration and administrative seed-input validation tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException

from escalane.config.errors import ValidationError
from escalane.config.settings import Settings
from escalane.configuration.seed import apply_seed
from escalane.web.main import create_app
from tests.support.assertions import expect
from tests.support.constants import EMPTY_SECRET_VALUE, TEST_ADMIN_API_KEY
from tests.support.security_test_helpers import security_client

pytestmark = [pytest.mark.security]


def _admin_settings() -> Settings:
    return Settings(
        database_url="sqlite+aiosqlite:///:memory:",
        redis_url="redis://fake/0",
        base_url="http://localhost:8080",
        admin_api_key=TEST_ADMIN_API_KEY,
        yelk_ip_allowlist="127.0.0.1/32",
        zammad_api_token=EMPTY_SECRET_VALUE,
        sendxms_enabled=False,
        signal_enabled=False,
    )


async def test_docs_and_openapi_disabled_by_default(engine, seeded_db, fake_redis):
    async with security_client(_admin_settings(), engine, fake_redis) as client:
        docs = await client.get("/docs")
        openapi = await client.get("/openapi.json")

    expect(docs.status_code == 404)
    expect(openapi.status_code == 404)


def test_openapi_schema_uses_escalane_brand() -> None:
    settings = _admin_settings().model_copy(update={"enable_api_docs": True})

    app = create_app(settings=settings)

    expect(app.openapi()["info"]["title"] == "Escalane")


def test_default_admin_api_key_is_not_empty_in_dev(monkeypatch) -> None:
    """Settings receives the explicit local-development key when supplied."""
    monkeypatch.setenv("ADMIN_API_KEY", "change-me-admin-key")
    monkeypatch.setenv("SIMULATION_ENABLED", "true")
    expect(Settings().admin_api_key != "")


async def test_invalid_alarm_id_rejected_with_422(engine, seeded_db, fake_redis) -> None:
    async with security_client(_admin_settings(), engine, fake_redis) as client:
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


def test_env_example_does_not_ship_static_admin_secret() -> None:
    env_example = Path(__file__).resolve().parents[2] / ".env.example"
    text = env_example.read_text(encoding="utf-8")
    expect(f"ADMIN_API_KEY={TEST_ADMIN_API_KEY}" not in text)


@pytest.mark.parametrize("content_length", ["-1", "invalid", ""])
def test_admin_seed_rejects_malformed_content_length(content_length: str) -> None:
    from escalane.web.routes.admin import _declared_seed_content_length

    with pytest.raises(HTTPException) as exc_info:
        _declared_seed_content_length(content_length)

    expect(exc_info.value.status_code == 400)


def test_admin_seed_rejects_declared_content_length_over_seed_limit() -> None:
    from escalane.configuration.importer import _MAX_SEED_BYTES
    from escalane.web.routes.admin import _declared_seed_content_length

    with pytest.raises(HTTPException) as exc_info:
        _declared_seed_content_length(str(_MAX_SEED_BYTES + 1))

    expect(exc_info.value.status_code == 413)


def test_admin_seed_rejects_pathologically_large_content_length_without_integer_error() -> None:
    from escalane.web.routes.admin import _declared_seed_content_length

    with pytest.raises(HTTPException) as exc_info:
        _declared_seed_content_length("9" * 10_000)

    expect(exc_info.value.status_code == 413)


async def test_admin_seed_invalid_json_returns_400(engine, seeded_db, fake_redis) -> None:
    async with security_client(_admin_settings(), engine, fake_redis) as client:
        resp = await client.post(
            "/v1/admin/seed",
            headers={"X-Admin-Key": TEST_ADMIN_API_KEY, "Content-Type": "application/json"},
            content=b"{invalid-json",
        )

    expect(resp.status_code == 400)


async def test_admin_seed_invalid_yaml_returns_400(engine, seeded_db, fake_redis) -> None:
    async with security_client(_admin_settings(), engine, fake_redis) as client:
        resp = await client.post(
            "/v1/admin/seed",
            headers={"X-Admin-Key": TEST_ADMIN_API_KEY, "Content-Type": "application/x-yaml"},
            content=b"foo: [\n",
        )

    expect(resp.status_code == 400)


async def test_admin_seed_accepts_application_yaml_content_type(
    engine, seeded_db, fake_redis
) -> None:
    async with security_client(_admin_settings(), engine, fake_redis) as client:
        resp = await client.post(
            "/v1/admin/seed",
            headers={"X-Admin-Key": TEST_ADMIN_API_KEY, "Content-Type": "application/yaml"},
            content=b"sites: []\nrooms: []\n",
        )

    expect(resp.status_code == 200)


async def _submit_invalid_policy(engine, fake_redis, target_ids: list[str]):
    """Submit one invalid policy target reference through the administrative API."""
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
        "steps": [{"step_no": 1, "after_seconds": 60, "target_ids": target_ids}],
    }

    async with security_client(_admin_settings(), engine, fake_redis) as client:
        resp = await client.post(
            "/v1/admin/escalation-policy", headers={"X-Admin-Key": TEST_ADMIN_API_KEY}, json=payload
        )

    return resp


async def test_policy_rejects_missing_target_references(engine, seeded_db, fake_redis) -> None:
    resp = await _submit_invalid_policy(engine, fake_redis, ["missing-target"])

    expect(resp.status_code == 400)


def test_default_zammad_api_token_is_empty(monkeypatch) -> None:
    monkeypatch.setenv("ADMIN_API_KEY", TEST_ADMIN_API_KEY)
    monkeypatch.setenv("SIMULATION_ENABLED", "true")
    expect(Settings().zammad_api_token == EMPTY_SECRET_VALUE)


def test_env_example_does_not_ship_static_zammad_token() -> None:
    env_example = Path(__file__).resolve().parents[2] / ".env.example"
    text = env_example.read_text(encoding="utf-8")
    expect("ZAMMAD_API_TOKEN=change-me" not in text)


async def test_policy_duplicate_step_target_rejected(engine, seeded_db, fake_redis) -> None:
    resp = await _submit_invalid_policy(engine, fake_redis, ["t1", "t1"])

    expect(resp.status_code == 400)


async def test_admin_seed_invalid_structure_returns_400(engine, seeded_db, fake_redis) -> None:
    async with security_client(_admin_settings(), engine, fake_redis) as client:
        resp = await client.post(
            "/v1/admin/seed",
            headers={"X-Admin-Key": TEST_ADMIN_API_KEY, "Content-Type": "application/json"},
            json={"sites": [{}]},
        )

    expect(resp.status_code == 400)


async def test_seed_rejects_unapproved_boolean_environment_placeholder(
    sessionmaker, settings
) -> None:
    raw = {
        "persons": [
            {"id": "p1", "display_name": "Person 1", "active": "${TEST_ACTIVE}"},
        ]
    }

    async with sessionmaker() as session:
        with pytest.raises(ValidationError, match="placeholder"):
            await apply_seed(session, raw, settings)
