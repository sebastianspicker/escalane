"""Regression coverage for versioned master-data console writes."""

from __future__ import annotations

import json

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from escalane.persistence.models import AdminAuditEvent, Device, EscalationPolicy, Site
from tests.support.admin_test_helpers import csrf_token, logged_in_admin_client
from tests.support.constants import TEST_ADMIN_API_KEY, value_for_test

pytestmark = pytest.mark.integration


async def _configuration_csrf(client: AsyncClient, resource: str) -> str:
    return csrf_token((await client.get(f"/admin/configuration/{resource}")).text)


async def _save_resource(client: AsyncClient, resource: str, data: dict) -> object:
    return await client.post(
        f"/admin/configuration/{resource}/save", data=data, follow_redirects=False
    )


async def _resource_action(
    client: AsyncClient, resource: str, resource_id: str, action: str, csrf_token: str, version: int
) -> object:
    return await client.post(
        f"/admin/configuration/{resource}/{resource_id}/{action}",
        data={"csrf_token": csrf_token, "version": str(version)},
        follow_redirects=False,
    )


async def _save_site(
    client: AsyncClient, csrf_token: str, site_id: str, name: str, version: int | None = None
) -> object:
    data = {"csrf_token": csrf_token, "resource_id": site_id, "name": name, "active": "on"}
    if version is not None:
        data["version"] = str(version)
    return await _save_resource(client, "sites", data)


async def _save_policy(client: AsyncClient, csrf_token: str, policy: str, version: int) -> object:
    return await client.post(
        "/admin/configuration/escalation",
        data={"csrf_token": csrf_token, "policy_json": policy, "version": str(version)},
        follow_redirects=False,
    )


async def _admin_api_post(client: AsyncClient, path: str, payload: dict) -> object:
    return await client.post(path, headers={"X-Admin-Key": TEST_ADMIN_API_KEY}, json=payload)


async def _save_device(
    client: AsyncClient, csrf_token: str, token: str, version: int | None = None
) -> object:
    data = {
        "csrf_token": csrf_token,
        "resource_id": "api-race-device",
        "vendor": "yealink",
        "model_family": "T5",
        "device_token": token,
        "active": "on",
    }
    if version is not None:
        data["version"] = str(version)
    return await _save_resource(client, "devices", data)


async def _exercise_replayed_versioned_writes(client: AsyncClient) -> None:
    csrf_token = await _configuration_csrf(client, "sites")
    assert (await _save_site(client, csrf_token, "race-site", "Initial Site")).status_code == 303
    winning_save = await _save_site(client, csrf_token, "race-site", "Winning Site", version=1)
    stale_save = await _save_site(client, csrf_token, "race-site", "Stale Site", version=1)
    assert winning_save.status_code == 303
    assert stale_save.status_code == 409
    winning_deactivate = await _resource_action(
        client, "sites", "race-site", "deactivate", csrf_token, 2
    )
    stale_deactivate = await _resource_action(
        client, "sites", "race-site", "deactivate", csrf_token, 2
    )
    assert winning_deactivate.status_code == 303
    assert stale_deactivate.status_code == 409
    assert (
        await _resource_action(client, "sites", "race-site", "delete", csrf_token, 2)
    ).status_code == 409
    assert (
        await _resource_action(client, "sites", "race-site", "delete", csrf_token, 3)
    ).status_code == 303
    policy = '{"policy_id":"default","name":"Default","targets":[],"steps":[]}'
    policy_responses = [
        await _save_policy(client, csrf_token, policy, version) for version in (0, 0, 1, 1)
    ]
    assert [response.status_code for response in policy_responses] == [303, 409, 303, 409]


async def _assert_api_write_versions(sessionmaker) -> None:
    async with sessionmaker() as session:
        device = await session.get(Device, "api-race-device")
        persisted_policy = await session.get(EscalationPolicy, "default")
        assert device is not None
        assert device.version == 2
        assert device.vendor == "poly"
        assert persisted_policy is not None
        assert persisted_policy.version == 2
        assert persisted_policy.name == "API Policy v2"


async def _stale_device_save_after_api_write(client: AsyncClient, device_token: str) -> object:
    device_csrf = await _configuration_csrf(client, "devices")
    assert (await _save_device(client, device_csrf, device_token)).status_code == 303
    stale_device_page = await client.get("/admin/configuration/devices")
    api_device = await _admin_api_post(
        client,
        "/v1/admin/devices",
        {"device_token": device_token, "vendor": "poly", "model_family": "VVX"},
    )
    assert api_device.status_code == 201
    return await _save_device(client, csrf_token(stale_device_page.text), device_token, version=1)


async def test_stale_save_does_not_recreate_deleted_master_data(
    engine, sessionmaker, fake_redis, settings
) -> None:
    settings.admin_api_key = TEST_ADMIN_API_KEY
    async with logged_in_admin_client(
        settings=settings,
        engine=engine,
        redis=fake_redis,
        admin_key=TEST_ADMIN_API_KEY,
        operator_name="Config Ops",
    ) as client:
        form_csrf = await _configuration_csrf(client, "sites")
        created = await _save_site(client, form_csrf, "deleted-site", "Deleted Site")
        assert created.status_code == 303
        deactivated = await _resource_action(
            client, "sites", "deleted-site", "deactivate", form_csrf, 1
        )
        assert deactivated.status_code == 303
        deleted = await _resource_action(client, "sites", "deleted-site", "delete", form_csrf, 2)
        assert deleted.status_code == 303
        stale_save = await _save_site(
            client, form_csrf, "deleted-site", "Recreated Site", version=2
        )
        assert stale_save.status_code == 409

    async with sessionmaker() as session:
        assert await session.get(Site, "deleted-site") is None


async def test_replayed_versions_are_rejected_without_extra_audit_events(
    engine, sessionmaker, fake_redis, settings
) -> None:
    settings.admin_api_key = TEST_ADMIN_API_KEY
    async with logged_in_admin_client(
        settings=settings,
        engine=engine,
        redis=fake_redis,
        admin_key=TEST_ADMIN_API_KEY,
        operator_name="Config Ops",
    ) as client:
        await _exercise_replayed_versioned_writes(client)

    async with sessionmaker() as session:
        assert await session.get(Site, "race-site") is None
        policy = await session.get(EscalationPolicy, "default")
        assert policy is not None
        assert policy.version == 2
        events = list(
            (
                await session.scalars(
                    select(AdminAuditEvent).where(
                        AdminAuditEvent.resource_id.in_(("race-site", "default"))
                    )
                )
            ).all()
        )
        assert sorted((event.resource_id, event.action) for event in events) == [
            ("default", "update"),
            ("default", "update"),
            ("race-site", "create"),
            ("race-site", "deactivate"),
            ("race-site", "delete"),
            ("race-site", "update"),
        ]


async def test_api_writes_advance_versions_and_invalidate_open_browser_forms(
    engine, sessionmaker, fake_redis, settings
) -> None:
    """API updates must cause an already-loaded browser form to fail closed."""
    settings.admin_api_key = TEST_ADMIN_API_KEY
    device_token = value_for_test("versioned-api-device")
    policy = {"policy_id": "default", "name": "API Policy", "targets": [], "steps": []}

    async with logged_in_admin_client(
        settings=settings,
        engine=engine,
        redis=fake_redis,
        admin_key=TEST_ADMIN_API_KEY,
        operator_name="Config Ops",
    ) as client:
        stale_device_save = await _stale_device_save_after_api_write(client, device_token)
        assert stale_device_save.status_code == 409

        policy_csrf = await _configuration_csrf(client, "escalation")
        api_policy = await _admin_api_post(
            client,
            "/v1/admin/escalation-policy",
            policy,
        )
        assert api_policy.status_code == 201
        stale_policy_save = await _save_policy(client, policy_csrf, json.dumps(policy), 0)
        assert stale_policy_save.status_code == 409

        api_policy_update = await _admin_api_post(
            client,
            "/v1/admin/escalation-policy",
            {**policy, "name": "API Policy v2"},
        )
        assert api_policy_update.status_code == 201

    await _assert_api_write_versions(sessionmaker)


async def _assert_inactive_site_blocks_room(client: AsyncClient) -> None:
    csrf_token = await _configuration_csrf(client, "sites")
    created_site = await _save_site(client, csrf_token, "inactive-parent-site", "Inactive Parent")
    assert created_site.status_code == 303
    deactivated_site = await _resource_action(
        client, "sites", "inactive-parent-site", "deactivate", csrf_token, 1
    )
    assert deactivated_site.status_code == 303
    browser_child = await _save_resource(
        client,
        "rooms",
        {
            "csrf_token": csrf_token,
            "resource_id": "blocked-room",
            "site_id": "inactive-parent-site",
            "label": "Blocked Room",
            "active": "on",
        },
    )
    assert browser_child.status_code == 409


async def _assert_inactive_person_blocks_device(client: AsyncClient, device_token: str) -> None:
    csrf_token = await _configuration_csrf(client, "people")
    created_person = await _save_resource(
        client,
        "people",
        {
            "csrf_token": csrf_token,
            "resource_id": "inactive-parent-person",
            "display_name": "Inactive Parent",
            "active": "on",
        },
    )
    assert created_person.status_code == 303
    deactivated_person = await _resource_action(
        client, "people", "inactive-parent-person", "deactivate", csrf_token, 1
    )
    assert deactivated_person.status_code == 303
    api_child = await _admin_api_post(
        client,
        "/v1/admin/devices",
        {"device_token": device_token, "person_id": "inactive-parent-person"},
    )
    assert api_child.status_code == 409


async def test_active_children_cannot_reference_deactivated_parents(
    engine, fake_redis, settings
) -> None:
    """Both browser and API child writers fail closed on inactive parents."""
    settings.admin_api_key = TEST_ADMIN_API_KEY
    device_token = value_for_test("inactive-parent-device")

    async with logged_in_admin_client(
        settings=settings,
        engine=engine,
        redis=fake_redis,
        admin_key=TEST_ADMIN_API_KEY,
        operator_name="Config Ops",
    ) as client:
        await _assert_inactive_site_blocks_room(client)
        await _assert_inactive_person_blocks_device(client, device_token)
