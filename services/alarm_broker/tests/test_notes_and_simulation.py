from __future__ import annotations

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


async def test_notes_endpoint_is_canonical_and_compatible(engine, seeded_db, fake_redis, settings):
    settings.admin_api_key = "dev-admin-key"
    app = create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)

    notes_post_routes = [
        route
        for route in app.routes
        if getattr(route, "path", "") == "/v1/alarms/{alarm_id}/notes"
        and "POST" in (getattr(route, "methods", set()) or set())
    ]
    assert len(notes_post_routes) == 1

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            alarm_id = await _trigger_alarm(client)

            explicit_author = await client.post(
                f"/v1/alarms/{alarm_id}/notes",
                headers={"X-Admin-Key": "dev-admin-key"},
                json={"note": "explicit", "created_by": "Ops Team"},
            )
            assert explicit_author.status_code == 201
            assert explicit_author.json()["created_by"] == "Ops Team"
            assert explicit_author.json()["note_type"] == "manual"

            header_fallback = await client.post(
                f"/v1/alarms/{alarm_id}/notes",
                headers={
                    "X-Admin-Key": "dev-admin-key",
                    "X-Admin-Email": "ops@example.org",
                },
                json={"note": "header fallback"},
            )
            assert header_fallback.status_code == 201
            assert header_fallback.json()["created_by"] == "ops@example.org"

            default_fallback = await client.post(
                f"/v1/alarms/{alarm_id}/notes",
                headers={"X-Admin-Key": "dev-admin-key"},
                json={"note": "default fallback"},
            )
            assert default_fallback.status_code == 201
            assert default_fallback.json()["created_by"] == "admin"

            listed = await client.get(
                f"/v1/alarms/{alarm_id}/notes",
                headers={"X-Admin-Key": "dev-admin-key"},
            )
            assert listed.status_code == 200
            payload = listed.json()
            assert len(payload) == 3
            assert [item["note"] for item in payload] == [
                "explicit",
                "header fallback",
                "default fallback",
            ]
            assert [item["note_type"] for item in payload] == ["manual", "manual", "manual"]


async def test_simulation_endpoints_return_404_when_disabled(
    engine, seeded_db, fake_redis, settings
):
    settings.admin_api_key = "dev-admin-key"
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
                    headers={"X-Admin-Key": "dev-admin-key"},
                )
                assert response.status_code == 404
                assert response.json()["detail"] == "Simulation endpoint not found"


async def test_simulation_endpoints_work_when_enabled(engine, seeded_db, fake_redis, settings):
    settings.admin_api_key = "dev-admin-key"
    settings.simulation_enabled = True
    app = create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)

    store = get_mock_store()
    store.clear()

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            initial_status = await client.get(
                "/v1/simulation/status",
                headers={"X-Admin-Key": "dev-admin-key"},
            )
            assert initial_status.status_code == 200
            initial_payload = initial_status.json()
            assert initial_payload["simulation_enabled"] is True
            assert initial_payload["total_notifications"] == 0
            assert initial_payload["by_channel"] == {"zammad": 0, "sms": 0, "signal": 0}

            store.add("sms", {"message": "sms-test"})
            store.add("signal", {"message": "signal-test"})

            all_notifications = await client.get(
                "/v1/simulation/notifications",
                headers={"X-Admin-Key": "dev-admin-key"},
            )
            assert all_notifications.status_code == 200
            all_payload = all_notifications.json()
            assert all_payload["total"] == 2
            assert all_payload["channel_filter"] is None

            sms_notifications = await client.get(
                "/v1/simulation/notifications",
                params={"channel": "sms"},
                headers={"X-Admin-Key": "dev-admin-key"},
            )
            assert sms_notifications.status_code == 200
            sms_payload = sms_notifications.json()
            assert sms_payload["total"] == 1
            assert sms_payload["channel_filter"] == "sms"
            assert sms_payload["notifications"][0]["channel"] == "sms"

            invalid_channel = await client.get(
                "/v1/simulation/notifications",
                params={"channel": "invalid"},
                headers={"X-Admin-Key": "dev-admin-key"},
            )
            assert invalid_channel.status_code == 400

            clear_notifications = await client.post(
                "/v1/simulation/notifications/clear",
                headers={"X-Admin-Key": "dev-admin-key"},
            )
            assert clear_notifications.status_code == 200
            assert clear_notifications.json()["status"] == "ok"

            post_clear_status = await client.get(
                "/v1/simulation/status",
                headers={"X-Admin-Key": "dev-admin-key"},
            )
            assert post_clear_status.status_code == 200
            assert post_clear_status.json()["total_notifications"] == 0

            seed_info = await client.post(
                "/v1/simulation/seed",
                headers={"X-Admin-Key": "dev-admin-key"},
            )
            assert seed_info.status_code == 200
            seed_payload = seed_info.json()
            assert seed_payload["status"] == "ok"
            assert seed_payload["admin_seed_endpoint"] == "/v1/admin/seed"
            assert seed_payload["seed_file"].endswith("deploy/simulation_seed.yaml")

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
            assert enabled_page.status_code == 200
            assert "id='simulation-panel'" in enabled_page.text
            assert "data-enabled='true'" in enabled_page.text
            assert "id='sim-refresh-btn'" in enabled_page.text
            assert "async function refreshSimulationStatus()" in enabled_page.text

    settings.simulation_enabled = False
    app_disabled = create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)
    async with app_disabled.router.lifespan_context(app_disabled):
        transport = ASGITransport(app=app_disabled)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            await admin_login(client, "dev-admin-key")
            disabled_page = await client.get("/admin")
            assert disabled_page.status_code == 200
            assert "id='simulation-panel'" in disabled_page.text
            assert "data-enabled='false'" in disabled_page.text
            assert "Simulation mode is currently disabled on this server." in disabled_page.text
