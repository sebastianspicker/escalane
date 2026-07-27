"""Admin-console browser-style workflow and authentication-boundary tests."""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient

from tests.admin_test_helpers import csrf_token, login_admin
from tests.api_test_helpers import app_client, make_alarm
from tests.constants import TEST_ADMIN_API_KEY, value_for_test

pytestmark = pytest.mark.integration


async def _seed_browser_alarm(sessionmaker) -> uuid.UUID:
    alarm_id = uuid.uuid4()
    async with sessionmaker() as session:
        session.add(
            make_alarm(
                alarm_id=alarm_id,
                ack_token=value_for_test("browser-action"),
            )
        )
        await session.commit()
    return alarm_id


async def _exercise_browser_session(client: AsyncClient, alarm_id: uuid.UUID) -> None:
    login = await login_admin(
        client, TEST_ADMIN_API_KEY, "Leitstelle Nord", path="/admin/login?lang=de"
    )
    assert login.status_code == 303
    detail = await client.get(f"/admin/alarms/{alarm_id}?lang=de")
    assert detail.status_code == 200
    assert "Leitstelle Nord" in detail.text
    assert TEST_ADMIN_API_KEY not in detail.text

    missing_csrf = await client.post(f"/admin/alarms/{alarm_id}/ack")
    assert missing_csrf.status_code == 403
    assert "Sicherheitsprüfung" in missing_csrf.text
    acknowledged = await client.post(
        f"/admin/alarms/{alarm_id}/ack",
        data={"csrf_token": csrf_token(detail.text)},
        follow_redirects=False,
    )
    assert acknowledged.status_code == 303


async def _check_rest_authentication_boundary(client: AsyncClient, alarm_id: uuid.UUID) -> None:
    rest_without_key = await client.post(
        f"/v1/alarms/{alarm_id}/resolve", json={"actor": "browser"}
    )
    assert rest_without_key.status_code == 401
    rest_with_key = await client.post(
        f"/v1/alarms/{alarm_id}/resolve",
        json={"actor": "API"},
        headers={"X-Admin-Key": TEST_ADMIN_API_KEY},
    )
    assert rest_with_key.status_code == 204


async def test_named_session_csrf_action_and_rest_boundary(
    engine, sessionmaker, seeded_db, fake_redis, settings
) -> None:
    settings.admin_api_key = TEST_ADMIN_API_KEY
    alarm_id = await _seed_browser_alarm(sessionmaker)

    async with app_client(settings=settings, engine=engine, redis=fake_redis) as client:
        await _exercise_browser_session(client, alarm_id)
        await _check_rest_authentication_boundary(client, alarm_id)


async def test_locale_precedence_and_packaged_assets(
    engine, seeded_db, fake_redis, settings
) -> None:
    settings.admin_api_key = TEST_ADMIN_API_KEY
    async with app_client(settings=settings, engine=engine, redis=fake_redis) as client:
        german = await client.get("/admin/login", headers={"Accept-Language": "de-DE,de;q=0.9"})
        assert german.status_code == 200
        assert '<html lang="de">' in german.text
        explicit_english = await client.get("/admin/login?lang=en")
        assert '<html lang="en">' in explicit_english.text
        css = await client.get("/admin/assets/ui.css")
        js = await client.get("/admin/assets/ui.js")

    assert css.status_code == 200
    assert js.status_code == 200
    assert "'unsafe-inline'" not in css.headers["content-security-policy"]
    assert "script-src 'self'" in js.headers["content-security-policy"]


async def test_configuration_masks_tokens_and_rejects_stale_version(
    engine, seeded_db, fake_redis, settings
) -> None:
    settings.admin_api_key = TEST_ADMIN_API_KEY
    token = value_for_test("browser-device-secret")
    async with app_client(settings=settings, engine=engine, redis=fake_redis) as client:
        assert (await login_admin(client, TEST_ADMIN_API_KEY, "Config Ops")).status_code == 303
        page = await client.get("/admin/configuration/devices")
        csrf = csrf_token(page.text)
        created = await client.post(
            "/admin/configuration/devices/save",
            data={
                "csrf_token": csrf,
                "resource_id": "ui-device",
                "vendor": "yealink",
                "model_family": "T5",
                "device_token": token,
                "active": "on",
            },
            follow_redirects=False,
        )
        assert created.status_code == 303
        rendered = await client.get("/admin/configuration/devices")
        assert token not in rendered.text
        assert token[-4:] in rendered.text

        stale = await client.post(
            "/admin/configuration/devices/save",
            data={
                "csrf_token": csrf_token(rendered.text),
                "resource_id": "ui-device",
                "version": "0",
                "vendor": "yealink",
                "model_family": "T5",
                "active": "on",
            },
        )
        assert stale.status_code == 409
        assert "changed since it was loaded" in stale.text
