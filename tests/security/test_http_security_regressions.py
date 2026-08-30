"""HTTP security regressions covering SSRF, header trust, and session refresh."""

from __future__ import annotations

import pytest

from escalane.security.url_validation import (
    SSRFError,
    pin_url_to_address,
    redact_url_for_logging,
    validate_url_not_internal,
    validate_webhook_host_allowed,
)
from tests.support.admin_test_helpers import csrf_token, login_admin
from tests.support.api_test_helpers import app_client
from tests.support.constants import TEST_ADMIN_API_KEY

pytestmark = pytest.mark.security


@pytest.mark.parametrize("url", ["https://[::]/hook", "https://hooks.example.test:invalid/hook"])
async def test_ssrf_validation_rejects_invalid_endpoint_forms(url: str) -> None:
    with pytest.raises(SSRFError):
        await validate_url_not_internal(url)


def test_allowlist_validation_normalizes_malformed_url_errors() -> None:
    with pytest.raises(SSRFError, match="invalid host"):
        validate_webhook_host_allowed("https://[invalid/hook", "invalid")


def test_pinned_webhook_url_preserves_tls_host_without_credentials() -> None:
    pinned_url, host_header, sni_hostname = pin_url_to_address(
        "https://user:secret@hooks.example.test:8443/path?token=secret", "203.0.113.10"
    )

    assert pinned_url == "https://203.0.113.10:8443/path?token=secret"
    assert host_header == "hooks.example.test:8443"
    assert sni_hostname == "hooks.example.test"
    assert "user" not in pinned_url
    assert "secret@" not in pinned_url


def test_webhook_url_redaction_removes_userinfo_path_query_and_fragment() -> None:
    assert (
        redact_url_for_logging(
            "https://user:secret@hooks.example.test:8443/private/path?token=secret#fragment"
        )
        == "https://hooks.example.test:8443"
    )


@pytest.mark.parametrize(
    ("trusted_proxy_cidrs", "expects_hsts"),
    [("127.0.0.1/32", True), ("", False)],
)
async def test_hsts_respects_only_trusted_forwarded_https(
    engine, fake_redis, settings, trusted_proxy_cidrs: str, expects_hsts: bool
) -> None:
    settings.trusted_proxy_cidrs = trusted_proxy_cidrs
    async with app_client(settings=settings, engine=engine, redis=fake_redis) as client:
        response = await client.get("/healthz", headers={"x-forwarded-proto": "https"})

    assert ("strict-transport-security" in response.headers) is expects_hsts


async def test_explicit_admin_session_extension_refreshes_cookie_expiry(
    engine, fake_redis, settings
) -> None:
    settings.admin_api_key = TEST_ADMIN_API_KEY
    async with app_client(settings=settings, engine=engine, redis=fake_redis) as client:
        assert (await login_admin(client, TEST_ADMIN_API_KEY, "Session Ops")).status_code == 303
        page = await client.get("/admin")
        response = await client.post(
            "/admin/session/extend",
            data={"csrf_token": csrf_token(page.text)},
            follow_redirects=False,
        )

    cookie = response.headers.get("set-cookie", "")
    assert response.status_code == 303
    assert "admin_session=" in cookie
    assert "Max-Age=3600" in cookie
    assert "HttpOnly" in cookie
    assert "SameSite=strict" in cookie
