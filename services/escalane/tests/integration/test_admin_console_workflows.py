"""Admin-console form workflows, bulk actions, and operational page tests."""

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient

from escalane.db.models import Alarm, Site
from tests.admin_test_helpers import hidden_input, login_admin
from tests.api_test_helpers import app_client, make_alarm

try:
    from tests.constants import TEST_ADMIN_API_KEY as _test_admin_api_key
    from tests.constants import value_for_test as _value_for_test
except ModuleNotFoundError:
    from constants import TEST_ADMIN_API_KEY as _test_admin_api_key  # type: ignore[no-redef]
    from constants import value_for_test as _value_for_test  # type: ignore[no-redef]


TEST_ADMIN_API_KEY = _test_admin_api_key
value_for_test = _value_for_test


pytestmark = pytest.mark.integration


def _alarm(alarm_id: uuid.UUID, token: str) -> Alarm:
    return make_alarm(
        alarm_id=alarm_id,
        source="workflow",
        ack_token=token,
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
    csrf = hidden_input(worklist.text, "csrf_token")
    revision = await client.get("/admin/revision")
    assert revision.status_code == 200 and revision.json()["revision"]

    detail = await client.get(f"/admin/alarms/{ids[0]}")
    detail_csrf = hidden_input(detail.text, "csrf_token")
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


def _bulk_action_data(action: str, csrf_token: str, alarm_ids: tuple[uuid.UUID, ...]) -> dict:
    return {
        "csrf_token": csrf_token,
        "action": action,
        "reason": {"cancel": "False alarm"}.get(action, ""),
        "alarm_id": [str(alarm_id) for alarm_id in alarm_ids],
    }


def _assert_bulk_event_jobs(fake_redis, alarm_ids, expected_events, expected_status: str) -> None:
    payloads = [args[0] for name, args in fake_redis.jobs if name == "process_alarm_event"]
    assert len(payloads) == len(alarm_ids) * len(expected_events)
    assert [payload["event_type"] for payload in payloads] == expected_events * len(alarm_ids)
    assert {payload["alarm_id"] for payload in payloads} == {
        str(alarm_id) for alarm_id in alarm_ids
    }
    assert {payload["new_state"] for payload in payloads if "new_state" in payload} == {
        expected_status
    }


async def test_alarm_detail_note_transitions_bulk_delete_and_session_controls(
    engine, sessionmaker, seeded_db, fake_redis, settings
) -> None:
    settings.admin_api_key = TEST_ADMIN_API_KEY
    ids = await _seed_workflow_alarms(sessionmaker)

    async with app_client(settings=settings, engine=engine, redis=fake_redis) as client:
        assert (await login_admin(client, TEST_ADMIN_API_KEY, "Workflow Ops")).status_code == 303
        await _run_alarm_workflow(client, ids)


@pytest.mark.parametrize(
    ("action", "expected_status", "expected_events"),
    [
        ("ack", "acknowledged", ["alarm.acknowledged", "alarm.state_changed"]),
        ("resolve", "resolved", ["alarm.state_changed"]),
        ("cancel", "cancelled", ["alarm.state_changed"]),
    ],
)
async def test_bulk_actions_enqueue_followup_events_for_each_changed_alarm(
    engine, sessionmaker, seeded_db, fake_redis, settings, action, expected_status, expected_events
) -> None:
    settings.admin_api_key = TEST_ADMIN_API_KEY
    alarm_ids = (await _seed_workflow_alarms(sessionmaker))[:2]

    async with app_client(settings=settings, engine=engine, redis=fake_redis) as client:
        assert (await login_admin(client, TEST_ADMIN_API_KEY, "Workflow Ops")).status_code == 303
        worklist = await client.get("/admin")
        response = await client.post(
            "/admin/alarms/bulk",
            data=_bulk_action_data(action, hidden_input(worklist.text, "csrf_token"), alarm_ids),
            follow_redirects=False,
        )

    assert response.status_code == 303
    _assert_bulk_event_jobs(fake_redis, alarm_ids, expected_events, expected_status)


async def test_admin_export_rejects_invalid_status(engine, seeded_db, fake_redis, settings) -> None:
    settings.admin_api_key = TEST_ADMIN_API_KEY

    async with app_client(settings=settings, engine=engine, redis=fake_redis) as client:
        assert (await login_admin(client, TEST_ADMIN_API_KEY, "Workflow Ops")).status_code == 303
        response = await client.get("/admin/export?status=bogus")

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["query", "status"]


async def _manage_site_and_import_configuration(client: AsyncClient) -> None:
    sites = await client.get("/admin/configuration/sites")
    csrf = hidden_input(sites.text, "csrf_token")
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
    import_csrf = hidden_input(import_page.text, "csrf_token")
    seed = "sites:\n  - id: imported-site\n    name: Imported\n"
    preview = await client.post(
        "/admin/configuration/import",
        data={"csrf_token": import_csrf, "action": "preview", "seed_text": seed},
    )
    assert preview.status_code == 200
    applied = await client.post(
        "/admin/configuration/import",
        data={
            "csrf_token": hidden_input(preview.text, "csrf_token"),
            "action": "apply",
            "seed_text": seed,
            "content_hash": hidden_input(preview.text, "content_hash"),
        },
        follow_redirects=False,
    )
    assert applied.status_code == 303


async def _check_admin_status_pages(client: AsyncClient) -> None:
    activity = await client.get("/admin/activity")
    assert activity.status_code == 200
    assert 'href="/admin/simulation"' in activity.text
    system = await client.get("/admin/system")
    assert system.status_code == 200
    assert 'href="/admin/simulation"' in system.text
    simulation = await client.get("/admin/simulation")
    assert simulation.status_code == 200
    assert 'href="/admin/simulation" aria-current="page"' in simulation.text
    cleared = await client.post(
        "/admin/simulation/clear",
        data={"csrf_token": hidden_input(simulation.text, "csrf_token")},
        follow_redirects=False,
    )
    assert cleared.status_code == 303


async def test_configuration_import_activity_system_and_simulation(
    engine, sessionmaker, seeded_db, fake_redis, settings
) -> None:
    settings.admin_api_key = TEST_ADMIN_API_KEY
    settings.simulation_enabled = True
    async with app_client(settings=settings, engine=engine, redis=fake_redis) as client:
        assert (await login_admin(client, TEST_ADMIN_API_KEY, "Workflow Ops")).status_code == 303
        await _manage_site_and_import_configuration(client)
        await _check_admin_status_pages(client)

    async with sessionmaker() as session:
        assert await session.get(Site, "imported-site") is not None
