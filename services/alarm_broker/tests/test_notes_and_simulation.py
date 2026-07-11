from __future__ import annotations

try:
    from tests.assertions import expect
except ModuleNotFoundError:
    from assertions import expect

import pytest
from httpx import ASGITransport, AsyncClient

from alarm_broker.api.main import create_app
from alarm_broker.connectors.mock import get_mock_store

try:
    from tests.helpers import admin_login
    from tests.helpers import trigger_alarm as _trigger_alarm
except ModuleNotFoundError:
    from helpers import admin_login
    from helpers import trigger_alarm as _trigger_alarm

pytestmark = [pytest.mark.integration]


ADMIN_KEY = "dev-admin-key"


def _admin_headers(**extra: str) -> dict[str, str]:
    return {"X-Admin-Key": ADMIN_KEY, **extra}


async def _post_note(client, alarm_id, note: str, **headers: str):
    payload = {"note": note}
    created_by = headers.pop("created_by", None)
    if created_by is not None:
        payload["created_by"] = created_by
    response = await client.post(
        f"/v1/alarms/{alarm_id}/notes",
        headers=_admin_headers(**headers),
        json=payload,
    )
    expect(response.status_code == 201)
    return response.json()


async def _simulation_status(client):
    response = await client.get("/v1/simulation/status", headers=_admin_headers())
    expect(response.status_code == 200)
    return response.json()


async def _simulation_notifications(client, **params):
    response = await client.get(
        "/v1/simulation/notifications",
        params=params or None,
        headers=_admin_headers(),
    )
    expect(response.status_code == 200)
    return response.json()


async def test_notes_endpoint_is_canonical_and_compatible(engine, seeded_db, fake_redis, settings):
    settings.admin_api_key = ADMIN_KEY
    app = create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            alarm_id = await _trigger_alarm(client)

            explicit_author = await _post_note(client, alarm_id, "explicit", created_by="Ops Team")
            expect(explicit_author["created_by"] == "Ops Team")
            expect(explicit_author["note_type"] == "manual")

            header_fallback = await _post_note(
                client, alarm_id, "header fallback", **{"X-Admin-Email": "ops@example.org"}
            )
            expect(header_fallback["created_by"] == "ops@example.org")

            default_fallback = await _post_note(client, alarm_id, "default fallback")
            expect(default_fallback["created_by"] == "admin")

            listed = await client.get(
                f"/v1/alarms/{alarm_id}/notes",
                headers=_admin_headers(),
            )
            expect(listed.status_code == 200)
            payload = listed.json()
            expect(len(payload) == 3)
            expect(
                [item["note"] for item in payload]
                == ["explicit", "header fallback", "default fallback"]
            )
            expect([item["note_type"] for item in payload] == ["manual", "manual", "manual"])


async def test_simulation_endpoints_return_404_when_disabled(
    engine, seeded_db, fake_redis, settings
):
    settings.admin_api_key = ADMIN_KEY
    settings.simulation_enabled = False
    app = create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            for method, path in (
                ("get", "/v1/simulation/notifications"),
                ("post", "/v1/simulation/notifications/clear"),
                ("get", "/v1/simulation/status"),
                ("post", "/v1/simulation/seed"),
            ):
                response = await getattr(client, method)(
                    path,
                    headers=_admin_headers(),
                )
                expect(response.status_code == 404)
                expect(response.json()["detail"] == "Simulation endpoint not found")


async def test_simulation_endpoints_work_when_enabled(engine, seeded_db, fake_redis, settings):
    settings.admin_api_key = ADMIN_KEY
    settings.simulation_enabled = True
    app = create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)

    store = get_mock_store()
    store.clear()

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            initial_payload = await _simulation_status(client)
            expect(initial_payload["simulation_enabled"] is True)
            expect(initial_payload["total_notifications"] == 0)
            expect(initial_payload["by_channel"] == {"zammad": 0, "sms": 0, "signal": 0})

            store.add("sms", {"message": "sms-test"})
            store.add("signal", {"message": "signal-test"})

            all_payload = await _simulation_notifications(client)
            expect(all_payload["total"] == 2)
            expect(all_payload["channel_filter"] is None)

            sms_payload = await _simulation_notifications(client, channel="sms")
            expect(sms_payload["total"] == 1)
            expect(sms_payload["channel_filter"] == "sms")
            expect(sms_payload["notifications"][0]["channel"] == "sms")

            invalid_channel = await client.get(
                "/v1/simulation/notifications",
                params={"channel": "invalid"},
                headers=_admin_headers(),
            )
            expect(invalid_channel.status_code == 400)

            clear_notifications = await client.post(
                "/v1/simulation/notifications/clear",
                headers=_admin_headers(),
            )
            expect(clear_notifications.status_code == 200)
            expect(clear_notifications.json()["status"] == "ok")

            expect((await _simulation_status(client))["total_notifications"] == 0)

            seed_info = await client.post(
                "/v1/simulation/seed",
                headers=_admin_headers(),
            )
            expect(seed_info.status_code == 200)
            seed_payload = seed_info.json()
            expect(seed_payload["status"] == "ok")
            expect(seed_payload["admin_seed_endpoint"] == "/v1/admin/seed")
            expect(seed_payload["seed_file"].endswith("deploy/simulation_seed.yaml"))

    store.clear()


async def test_admin_ui_simulation_panel_state(engine, seeded_db, fake_redis, settings):
    settings.admin_api_key = "dev-admin-key"
    settings.simulation_enabled = True
    app = create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            await admin_login(client, "dev-admin-key")
            enabled_page = await client.get("/admin")
            expect(enabled_page.status_code == 200)
            expect("id='simulation-panel'" in enabled_page.text)
            expect("data-enabled='true'" in enabled_page.text)
            expect("id='sim-refresh-btn'" in enabled_page.text)
            expect("async function refreshSimulationStatus()" in enabled_page.text)

    settings.simulation_enabled = False
    app_disabled = create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)
    async with app_disabled.router.lifespan_context(app_disabled):
        transport = ASGITransport(app=app_disabled)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            await admin_login(client, "dev-admin-key")
            disabled_page = await client.get("/admin")
            expect(disabled_page.status_code == 200)
            expect("id='simulation-panel'" in disabled_page.text)
            expect("data-enabled='false'" in disabled_page.text)
            expect("Simulation mode is currently disabled on this server." in disabled_page.text)
