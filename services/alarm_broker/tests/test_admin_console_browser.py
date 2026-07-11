from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient

from alarm_broker.api.main import create_app
from alarm_broker.db.models import Alarm, AlarmStatus

try:
    from tests.constants import TEST_ADMIN_API_KEY, value_for_test
except ModuleNotFoundError:
    from constants import TEST_ADMIN_API_KEY, value_for_test


pytestmark = pytest.mark.integration


def _csrf(html: str) -> str:
    match = re.search(r'name="csrf_token"\s+value="([^"]+)"', html)
    assert match is not None
    return match.group(1)


async def _seed_browser_alarm(sessionmaker) -> uuid.UUID:
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
                ack_token=value_for_test("browser-action"),
                created_at=datetime.now(UTC),
                meta={},
            )
        )
        await session.commit()
    return alarm_id


async def _exercise_browser_session(client: AsyncClient, alarm_id: uuid.UUID) -> None:
    login = await client.post(
        "/admin/login?lang=de",
        data={"admin_key": TEST_ADMIN_API_KEY, "operator_name": "Leitstelle Nord"},
        follow_redirects=False,
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
        data={"csrf_token": _csrf(detail.text)},
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

    app = create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await _exercise_browser_session(client, alarm_id)
            await _check_rest_authentication_boundary(client, alarm_id)


async def test_locale_precedence_and_packaged_assets(
    engine, seeded_db, fake_redis, settings
) -> None:
    settings.admin_api_key = TEST_ADMIN_API_KEY
    app = create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
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
    app = create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)
    token = value_for_test("browser-device-secret")
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await client.post(
                "/admin/login",
                data={"admin_key": TEST_ADMIN_API_KEY, "operator_name": "Config Ops"},
            )
            page = await client.get("/admin/configuration/devices")
            csrf = _csrf(page.text)
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
                    "csrf_token": _csrf(rendered.text),
                    "resource_id": "ui-device",
                    "version": "0",
                    "vendor": "yealink",
                    "model_family": "T5",
                    "active": "on",
                },
            )
            assert stale.status_code == 409
            assert "changed since it was loaded" in stale.text
