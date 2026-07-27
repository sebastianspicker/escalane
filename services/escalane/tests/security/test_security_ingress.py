"""Ingress trust-boundary, allowlist, and client-address rate-limit tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from escalane.core.ip_allowlist import ip_allowed
from escalane.core.rate_limit import rate_limit_key
from escalane.settings import Settings
from tests.security_test_helpers import security_client

try:
    from tests.assertions import expect
    from tests.constants import TEST_DEVICE_TOKEN
except ModuleNotFoundError:
    from assertions import expect
    from constants import TEST_DEVICE_TOKEN

pytestmark = [pytest.mark.security]


async def _request_alarm(
    settings: Settings, engine, fake_redis, headers: dict[str, str] | None = None
):
    """Exercise the Yealink ingress path through a configured in-process app."""
    async with security_client(settings, engine, fake_redis) as client:
        return await client.get(
            "/v1/yealink/alarm", params={"token": TEST_DEVICE_TOKEN}, headers=headers
        )


async def _forwarded_https_login(client, admin_key: str):
    """Submit the operator login through the proxy HTTPS trust boundary."""
    return await client.post(
        "/admin/login",
        data={"admin_key": admin_key},
        headers={"x-forwarded-proto": "https"},
        follow_redirects=False,
    )


def test_canonical_container_disables_uvicorn_access_logging() -> None:
    """Bearer tokens in query strings and ACK paths must not reach Uvicorn logs."""
    dockerfile = Path(__file__).resolve().parents[4] / "Dockerfile"

    expect('"--no-access-log"' in dockerfile.read_text(encoding="utf-8"))


async def test_untrusted_x_forwarded_for_does_not_bypass_ip_allowlist(
    engine, seeded_db, fake_redis, settings
):
    payload = settings.model_dump()
    payload.update({"yelk_ip_allowlist": "203.0.113.0/24", "simulation_enabled": False})
    resp = await _request_alarm(
        Settings(**payload), engine, fake_redis, {"x-forwarded-for": "203.0.113.10"}
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
    resp = await _request_alarm(
        Settings(**payload), engine, fake_redis, {"x-forwarded-for": "203.0.113.10"}
    )

    expect(resp.status_code == 200)


async def test_trusted_proxy_forwarded_https_sets_secure_cookie_and_hsts(
    engine, seeded_db, fake_redis, settings
) -> None:
    payload = settings.model_dump()
    payload["trusted_proxy_cidrs"] = "127.0.0.1/32,::1/128"
    async with security_client(Settings(**payload), engine, fake_redis) as client:
        response = await _forwarded_https_login(client, settings.admin_api_key)

    expect(response.status_code == 303)
    expect("Secure" in response.headers["set-cookie"])
    expect(response.headers.get("strict-transport-security") is not None)


async def test_untrusted_forwarded_https_does_not_set_secure_cookie_or_hsts(
    engine, seeded_db, fake_redis, settings
) -> None:
    payload = settings.model_dump()
    payload["trusted_proxy_cidrs"] = ""
    async with security_client(Settings(**payload), engine, fake_redis) as client:
        response = await _forwarded_https_login(client, settings.admin_api_key)

    expect(response.status_code == 303)
    expect("Secure" not in response.headers["set-cookie"])
    expect(response.headers.get("strict-transport-security") is None)


def test_rate_limit_key_does_not_include_raw_token() -> None:
    key = rate_limit_key("TOPSECRET_DEVICE_TOKEN", 42)

    expect(key.startswith("rl:"))
    expect("TOPSECRET_DEVICE_TOKEN" not in key)


async def test_invalid_allowlist_config_fails_closed_without_500(
    engine, seeded_db, fake_redis, settings
):
    payload = settings.model_dump()
    payload.update({"yelk_ip_allowlist": "not-a-cidr", "simulation_enabled": False})
    resp = await _request_alarm(Settings(**payload), engine, fake_redis)

    expect(resp.status_code == 403)


async def test_invalid_trusted_proxy_config_is_ignored_without_500(
    engine, seeded_db, fake_redis, settings
):
    payload = settings.model_dump()
    payload.update({"trusted_proxy_cidrs": "invalid-cidr"})
    resp = await _request_alarm(
        Settings(**payload), engine, fake_redis, {"x-forwarded-for": "203.0.113.10"}
    )

    expect(resp.status_code == 200)


def test_ip_allowlist_ipv6_host_entry_matches_only_exact_host() -> None:
    expect(ip_allowed("2001:db8::1", "2001:db8::1"))
    expect(not ip_allowed("2001:db8::2", "2001:db8::1"))
