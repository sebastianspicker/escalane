from __future__ import annotations

import html
import re
import uuid
from datetime import UTC, datetime

import pytest
from httpx import ASGITransport, AsyncClient

from alarm_broker.api.main import create_app
from alarm_broker.db.models import Alarm, AlarmStatus, Site

try:
    from tests.constants import TEST_ADMIN_API_KEY, value_for_test
except ModuleNotFoundError:
    from constants import TEST_ADMIN_API_KEY, value_for_test


pytestmark = pytest.mark.integration


def _hidden(page: str, name: str) -> str:
    match = re.search(rf'name="{name}"\s+value="([^"]*)"', page)
    assert match is not None
    return html.unescape(match.group(1))


async def _login(client: AsyncClient) -> None:
    response = await client.post(
        "/admin/login",
        data={"admin_key": TEST_ADMIN_API_KEY, "operator_name": "Workflow Ops"},
    )
    assert response.status_code == 303


def _alarm(alarm_id: uuid.UUID, token: str) -> Alarm:
    return Alarm(
        id=alarm_id,
        status=AlarmStatus.TRIGGERED,
        source="workflow",
        event="alarm.trigger",
        person_id="ma-012",
        room_id="bg-1.23",
        site_id="bg",
        device_id="ylk-t5-10023",
        severity="P0",
        silent=True,
        ack_token=token,
        created_at=datetime.now(UTC),
        meta={},
    )


async def _seed_workflow_alarms(sessionmaker) -> list[uuid.UUID]:
    ids = [uuid.uuid4() for _ in range(4)]
    async with sessionmaker() as session:
        session.add_all(
            [_alarm(item, value_for_test(f"workflow-{index}")) for index, item in enumerate(ids)]
        )
        await session.commit()
    return ids


async def _run_alarm_workflow(client: AsyncClient, ids: list[uuid.UUID]) -> None:
    worklist = await client.get("/admin?search=workflow&status=triggered")
    csrf = _hidden(worklist.text, "csrf_token")
    revision = await client.get("/admin/revision")
    assert revision.status_code == 200 and revision.json()["revision"]

    detail = await client.get(f"/admin/alarms/{ids[0]}")
    detail_csrf = _hidden(detail.text, "csrf_token")
    note = await client.post(
        f"/admin/alarms/{ids[0]}/notes",
        data={"csrf_token": detail_csrf, "note": "Checked location"},
        follow_redirects=False,
    )
    assert note.status_code == 303
    resolved = await client.post(
        f"/admin/alarms/{ids[0]}/resolve",
        data={"csrf_token": detail_csrf, "note": "Completed"},
        follow_redirects=False,
    )
    assert resolved.status_code == 303
    cancelled = await client.post(
        f"/admin/alarms/{ids[1]}/cancel",
        data={"csrf_token": csrf, "reason": "False alarm"},
        follow_redirects=False,
    )
    assert cancelled.status_code == 303
    bulk = await client.post(
        "/admin/alarms/bulk",
        data={"csrf_token": csrf, "action": "ack", "alarm_id": [str(ids[2]), str(ids[3])]},
        follow_redirects=False,
    )
    assert bulk.status_code == 303
    deleted = await client.post(
        f"/admin/alarms/{ids[0]}/delete",
        data={"csrf_token": csrf, "reason": "Retention exception"},
        follow_redirects=False,
    )
    assert deleted.status_code == 303
    extended = await client.post(
        "/admin/session/extend", data={"csrf_token": csrf}, follow_redirects=False
    )
    assert extended.status_code == 303
    logged_out = await client.post(
        "/admin/logout", data={"csrf_token": csrf}, follow_redirects=False
    )
    assert logged_out.status_code == 303
    assert (await client.get("/admin")).status_code == 401


async def test_alarm_detail_note_transitions_bulk_delete_and_session_controls(
    engine, sessionmaker, seeded_db, fake_redis, settings
) -> None:
    settings.admin_api_key = TEST_ADMIN_API_KEY
    ids = await _seed_workflow_alarms(sessionmaker)

    app = create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await _login(client)
            await _run_alarm_workflow(client, ids)


async def _manage_site_and_import_configuration(client: AsyncClient) -> None:
    sites = await client.get("/admin/configuration/sites")
    csrf = _hidden(sites.text, "csrf_token")
    created = await client.post(
        "/admin/configuration/sites/save",
        data={
            "csrf_token": csrf,
            "resource_id": "temporary-site",
            "name": "Temporary",
            "active": "on",
        },
        follow_redirects=False,
    )
    assert created.status_code == 303
    deactivated = await client.post(
        "/admin/configuration/sites/temporary-site/deactivate",
        data={"csrf_token": csrf, "version": "1"},
        follow_redirects=False,
    )
    assert deactivated.status_code == 303
    deleted = await client.post(
        "/admin/configuration/sites/temporary-site/delete",
        data={"csrf_token": csrf, "version": "2"},
        follow_redirects=False,
    )
    assert deleted.status_code == 303

    import_page = await client.get("/admin/configuration/import")
    import_csrf = _hidden(import_page.text, "csrf_token")
    seed = "sites:\n  - id: imported-site\n    name: Imported\n"
    preview = await client.post(
        "/admin/configuration/import",
        data={"csrf_token": import_csrf, "action": "preview", "seed_text": seed},
    )
    assert preview.status_code == 200
    applied = await client.post(
        "/admin/configuration/import",
        data={
            "csrf_token": _hidden(preview.text, "csrf_token"),
            "action": "apply",
            "seed_text": seed,
            "content_hash": _hidden(preview.text, "content_hash"),
        },
        follow_redirects=False,
    )
    assert applied.status_code == 303


async def _check_admin_status_pages(client: AsyncClient) -> None:
    assert (await client.get("/admin/activity")).status_code == 200
    assert (await client.get("/admin/system")).status_code == 200
    simulation = await client.get("/admin/simulation")
    assert simulation.status_code == 200
    cleared = await client.post(
        "/admin/simulation/clear",
        data={"csrf_token": _hidden(simulation.text, "csrf_token")},
        follow_redirects=False,
    )
    assert cleared.status_code == 303


async def test_configuration_import_activity_system_and_simulation(
    engine, sessionmaker, seeded_db, fake_redis, settings
) -> None:
    settings.admin_api_key = TEST_ADMIN_API_KEY
    settings.simulation_enabled = True
    app = create_app(settings=settings, injected_engine=engine, injected_redis=fake_redis)
    async with app.router.lifespan_context(app):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            await _login(client)
            await _manage_site_and_import_configuration(client)
            await _check_admin_status_pages(client)

    async with sessionmaker() as session:
        assert await session.get(Site, "imported-site") is not None
