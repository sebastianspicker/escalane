"""Console seed-import apply path and policy-editor save error branches."""

from __future__ import annotations

import hashlib
import json

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from escalane.persistence.models import (
    AdminAuditEvent,
    Device,
    EscalationPolicy,
    Person,
    Room,
    Site,
)
from tests.support.admin import csrf_token, logged_in_admin_client
from tests.support.assertions import expect
from tests.support.constants import TEST_ADMIN_API_KEY, value_for_test

pytestmark = [pytest.mark.integration]

OPERATOR = "Import Ops"
DEVICE_TOKEN = value_for_test("import-device")
SEED = f"""\
sites:
  - id: import-site
    name: Import Site
rooms:
  - id: import-room
    site_id: import-site
    label: Import Room
    floor: "2"
persons:
  - id: import-person
    display_name: Import Person
    role: Staff
    active: true
devices:
  - id: import-device
    vendor: yealink
    model_family: T5
    account_ext: "20001"
    device_token: {DEVICE_TOKEN}
    person_id: import-person
    room_id: import-room
"""


async def _import_csrf(client: AsyncClient) -> str:
    return csrf_token((await client.get("/admin/configuration/import")).text)


async def _policy_csrf(client: AsyncClient) -> str:
    return csrf_token((await client.get("/admin/configuration/escalation")).text)


async def test_import_apply_creates_master_data_and_audit_event(
    engine, sessionmaker, fake_redis, settings
) -> None:
    settings.admin_api_key = TEST_ADMIN_API_KEY
    digest = hashlib.sha256(SEED.encode()).hexdigest()

    async with logged_in_admin_client(
        settings=settings,
        engine=engine,
        redis=fake_redis,
        admin_key=TEST_ADMIN_API_KEY,
        operator_name=OPERATOR,
    ) as client:
        token = await _import_csrf(client)
        preview = await client.post(
            "/admin/configuration/import",
            data={"seed_text": SEED, "action": "preview", "csrf_token": token},
        )
        expect(preview.status_code == 200, preview.text)
        expect(digest in preview.text)
        async with sessionmaker() as session:
            expect(await session.get(Site, "import-site") is None, "preview must not write")

        response = await client.post(
            "/admin/configuration/import",
            data={
                "seed_text": SEED,
                "action": "apply",
                "content_hash": digest,
                "csrf_token": token,
            },
            follow_redirects=False,
        )
        expect(response.status_code == 303, response.text)
        expect(response.headers["location"] == "/admin/configuration/import")

    async with sessionmaker() as session:
        site = await session.get(Site, "import-site")
        room = await session.get(Room, "import-room")
        person = await session.get(Person, "import-person")
        device = await session.get(Device, "import-device")
        events = list((await session.scalars(select(AdminAuditEvent))).all())
    expect(site is not None and site.name == "Import Site" and site.active)
    expect(room is not None and room.site_id == "import-site" and room.floor == "2")
    expect(person is not None and person.display_name == "Import Person")
    expect(device is not None and device.device_token == DEVICE_TOKEN)
    expect(device is not None and device.person_id == "import-person")
    expect(device is not None and device.room_id == "import-room")
    expect(len(events) == 1)
    expect(events[0].operator_name == OPERATOR)
    expect((events[0].action, events[0].resource_type) == ("import", "configuration"))
    expect(events[0].resource_id == digest)
    expect(events[0].changed_fields["content_hash"] == digest)
    expect(events[0].changed_fields["sections"] == ["devices", "persons", "rooms", "sites"])
    # The audit record must not carry the seed body or the device credential.
    expect(DEVICE_TOKEN not in json.dumps(events[0].changed_fields))


async def test_import_apply_rejects_changed_seed_and_missing_hash(
    engine, sessionmaker, fake_redis, settings
) -> None:
    settings.admin_api_key = TEST_ADMIN_API_KEY
    digest = hashlib.sha256(SEED.encode()).hexdigest()

    async with logged_in_admin_client(
        settings=settings,
        engine=engine,
        redis=fake_redis,
        admin_key=TEST_ADMIN_API_KEY,
        operator_name=OPERATOR,
    ) as client:
        token = await _import_csrf(client)
        changed = await client.post(
            "/admin/configuration/import",
            data={
                "seed_text": SEED + "\n# edited after preview\n",
                "action": "apply",
                "content_hash": digest,
                "csrf_token": token,
            },
        )
        no_hash = await client.post(
            "/admin/configuration/import",
            data={"seed_text": SEED, "action": "apply", "csrf_token": token},
        )
        bad_csrf = await client.post(
            "/admin/configuration/import",
            data={
                "seed_text": SEED,
                "action": "apply",
                "content_hash": digest,
                "csrf_token": "wrong",
            },
        )
    expect(changed.status_code == 409 and "import_preview_is_stale" in changed.text)
    expect(no_hash.status_code == 409 and "import_preview_is_stale" in no_hash.text)
    expect(bad_csrf.status_code == 403, bad_csrf.text)
    async with sessionmaker() as session:
        expect(await session.get(Site, "import-site") is None)
        expect(list((await session.scalars(select(AdminAuditEvent))).all()) == [])


async def test_import_apply_with_invalid_structure_is_rejected_without_changes(
    engine, sessionmaker, fake_redis, settings
) -> None:
    settings.admin_api_key = TEST_ADMIN_API_KEY
    bad_seed = "sites:\n  - name: no id\n"
    digest = hashlib.sha256(bad_seed.encode()).hexdigest()

    async with logged_in_admin_client(
        settings=settings,
        engine=engine,
        redis=fake_redis,
        admin_key=TEST_ADMIN_API_KEY,
        operator_name=OPERATOR,
    ) as client:
        token = await _import_csrf(client)
        response = await client.post(
            "/admin/configuration/import",
            data={
                "seed_text": bad_seed,
                "action": "apply",
                "content_hash": digest,
                "csrf_token": token,
            },
            follow_redirects=False,
        )
    expect(response.status_code == 400, response.text)
    async with sessionmaker() as session:
        expect(list((await session.scalars(select(Site))).all()) == [])
        expect(list((await session.scalars(select(AdminAuditEvent))).all()) == [])


def _policy(**overrides: object) -> str:
    payload: dict[str, object] = {
        "policy_id": "default",
        "name": "Default",
        "targets": [],
        "steps": [],
    }
    payload.update(overrides)
    return json.dumps(payload)


@pytest.mark.parametrize(
    ("policy_json", "detail"),
    [
        ("{not json", "invalid_policy"),
        (_policy(targets="nope"), "invalid_policy"),
        (
            _policy(steps=[{"step_no": -1, "after_seconds": 0, "target_ids": ["t"]}]),
            "invalid_policy",
        ),
        (_policy(policy_id="other"), "only_default_policy_is_editable"),
        (
            _policy(
                targets=[
                    {
                        "id": "fresh",
                        "label": "Fresh",
                        "channel": "sms",
                        "address": "",
                        "enabled": True,
                    }
                ]
            ),
            "new_target_address_required",
        ),
    ],
)
async def test_policy_save_rejects_invalid_payload_with_422(
    engine, sessionmaker, fake_redis, settings, policy_json, detail
) -> None:
    settings.admin_api_key = TEST_ADMIN_API_KEY

    async with logged_in_admin_client(
        settings=settings,
        engine=engine,
        redis=fake_redis,
        admin_key=TEST_ADMIN_API_KEY,
        operator_name=OPERATOR,
    ) as client:
        token = await _policy_csrf(client)
        response = await client.post(
            "/admin/configuration/escalation",
            data={"csrf_token": token, "policy_json": policy_json, "version": "0"},
            follow_redirects=False,
        )
    expect(response.status_code == 422, response.text)
    expect(detail in response.text, response.text)
    async with sessionmaker() as session:
        expect(await session.get(EscalationPolicy, "default") is None)
        expect(list((await session.scalars(select(AdminAuditEvent))).all()) == [])


async def test_policy_save_with_unknown_step_target_is_rejected(
    engine, sessionmaker, fake_redis, settings
) -> None:
    settings.admin_api_key = TEST_ADMIN_API_KEY
    policy = _policy(steps=[{"step_no": 1, "after_seconds": 60, "target_ids": ["missing"]}])

    async with logged_in_admin_client(
        settings=settings,
        engine=engine,
        redis=fake_redis,
        admin_key=TEST_ADMIN_API_KEY,
        operator_name=OPERATOR,
    ) as client:
        token = await _policy_csrf(client)
        response = await client.post(
            "/admin/configuration/escalation",
            data={"csrf_token": token, "policy_json": policy, "version": "0"},
            follow_redirects=False,
        )
    expect(response.status_code == 400, response.text)
    async with sessionmaker() as session:
        expect(await session.get(EscalationPolicy, "default") is None)
