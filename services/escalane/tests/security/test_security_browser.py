"""Browser-facing security regressions for escaped content, headers, and login throttling."""

from __future__ import annotations

import asyncio
import re
import uuid

import pytest

from escalane.db.models import Alarm, AlarmStatus, Person

try:
    from tests.assertions import expect
    from tests.constants import TEST_DEVICE_TOKEN, value_for_test
    from tests.security_test_helpers import security_client
except ModuleNotFoundError:
    from assertions import expect
    from constants import TEST_DEVICE_TOKEN, value_for_test
    from security_test_helpers import security_client

pytestmark = [pytest.mark.security]


async def _trigger_ack_token(client, sessionmaker) -> str:
    """Trigger one alarm and return its persisted responder capability token."""
    response = await client.get("/v1/yealink/alarm", params={"token": TEST_DEVICE_TOKEN})
    expect(response.status_code == 200)
    async with sessionmaker() as session:
        alarm = await session.get(Alarm, uuid.UUID(response.json()["alarm_id"]))
        expect(alarm is not None)
        expect(alarm.ack_token is not None)
        return alarm.ack_token


async def test_ack_page_escapes_untrusted_html(
    engine, sessionmaker, seeded_db, fake_redis, settings
):
    alarm_id = uuid.uuid4()
    ack_token = value_for_test("ack-xss")

    async with sessionmaker() as session:
        person = await session.get(Person, "ma-012")
        expect(person is not None)
        person.display_name = '<script>alert("x")</script>'
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
                ack_token=ack_token,
                meta={},
            )
        )
        await session.commit()

    async with security_client(settings, engine, fake_redis) as client:
        resp = await client.get(f"/a/{ack_token}")

    expect(resp.status_code == 200)
    expect('<script>alert("x")</script>' not in resp.text)
    expect("&lt;script&gt;alert(" in resp.text)
    expect("&lt;/script&gt;" in resp.text)


async def test_ack_page_sets_no_store_and_security_headers(
    engine, sessionmaker, seeded_db, fake_redis, settings
):
    async with security_client(settings, engine, fake_redis) as client:
        ack_token = await _trigger_ack_token(client, sessionmaker)
        resp = await client.get(f"/a/{ack_token}")

    expect(resp.status_code == 200)
    expect(resp.headers.get("Cache-Control") == "no-store")
    expect(resp.headers.get("Pragma") == "no-cache")
    expect(resp.headers.get("X-Content-Type-Options") == "nosniff")
    expect(resp.headers.get("X-Frame-Options") == "DENY")
    expect(resp.headers.get("Referrer-Policy") == "no-referrer")
    csp = resp.headers.get("Content-Security-Policy", "")
    expect("object-src 'none'" in csp)
    expect("base-uri 'self'" in csp)
    expect("form-action 'self'" in csp)
    expect("frame-ancestors 'none'" in csp)


async def test_admin_login_failed_attempts_are_rate_limited(
    engine, seeded_db, fake_redis, settings
):
    statuses: list[int] = []
    async with security_client(settings, engine, fake_redis) as client:
        for _ in range(6):
            resp = await client.post(
                "/admin/login",
                data={"admin_key": "wrong-admin-key"},
                follow_redirects=False,
            )
            statuses.append(resp.status_code)

    expect(statuses == [401, 401, 401, 401, 401, 429])


async def test_admin_login_concurrent_failed_attempts_are_rate_limited(
    engine, seeded_db, fake_redis, settings
) -> None:
    original_get = fake_redis.get

    async def delayed_get(key: str) -> str | None:
        await asyncio.sleep(0)
        return await original_get(key)

    fake_redis.get = delayed_get
    async with security_client(settings, engine, fake_redis) as client:
        responses = await asyncio.gather(
            *(
                client.post(
                    "/admin/login",
                    data={"admin_key": "wrong-admin-key"},
                    follow_redirects=False,
                )
                for _ in range(12)
            )
        )

    statuses = [response.status_code for response in responses]
    expect(statuses.count(401) == 5)
    expect(statuses.count(429) == 7)


async def test_admin_login_success_clears_failed_attempt_counter(
    engine, seeded_db, fake_redis, settings
) -> None:
    async with security_client(settings, engine, fake_redis) as client:
        for _ in range(4):
            resp = await client.post(
                "/admin/login",
                data={"admin_key": "wrong-admin-key"},
                follow_redirects=False,
            )
            expect(resp.status_code == 401)

        ok = await client.post(
            "/admin/login",
            data={"admin_key": settings.admin_api_key},
            follow_redirects=False,
        )
        expect(ok.status_code == 303)

        retry = await client.post(
            "/admin/login",
            data={"admin_key": "wrong-admin-key"},
            follow_redirects=False,
        )

    expect(retry.status_code == 401)


async def test_admin_login_lockout_rejects_correct_key_after_limit(
    engine, seeded_db, fake_redis, settings
) -> None:
    """The rate limit must block authentication, not merely relabel bad guesses."""
    async with security_client(settings, engine, fake_redis) as client:
        for _ in range(5):
            response = await client.post(
                "/admin/login",
                data={"admin_key": "wrong-admin-key"},
                follow_redirects=False,
            )
            expect(response.status_code == 401)

        locked = await client.post(
            "/admin/login",
            data={"admin_key": settings.admin_api_key},
            follow_redirects=False,
        )
        fake_redis.advance(60)
        after_window = await client.post(
            "/admin/login",
            data={"admin_key": settings.admin_api_key},
            follow_redirects=False,
        )

    expect(locked.status_code == 429)
    expect(after_window.status_code == 303)


async def test_ack_form_rejects_oversized_note(
    engine, sessionmaker, seeded_db, fake_redis, settings
):
    async with security_client(settings, engine, fake_redis) as client:
        ack_token = await _trigger_ack_token(client, sessionmaker)
        get_resp = await client.get(f"/a/{ack_token}")
        expect(get_resp.status_code == 200)
        match = re.search(r'name="csrf_token"\s+value="([^"]+)"', get_resp.text)
        csrf_value = match.group(1) if match else ""

        resp = await client.post(
            f"/a/{ack_token}",
            data={"acked_by": "Tester", "note": "x" * 2001, "csrf_token": csrf_value},
        )

    expect(resp.status_code == 422)
