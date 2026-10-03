"""Console alarm actions driven through the HTTP app with a session cookie and CSRF token."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from escalane.persistence.models import Alarm, AlarmEventOutbox, AlarmNote, AlarmStatus
from tests.support.admin import csrf_token, login_admin
from tests.support.assertions import expect
from tests.support.clients import app_client
from tests.support.constants import TEST_ADMIN_API_KEY
from tests.support.factories import make_alarm
from tests.support.fakes import FakeRedis

pytestmark = [pytest.mark.integration]

OPERATOR = "Leitstelle Nord"


class QueueDownRedis(FakeRedis):
    """FakeRedis whose queue rejects every job, leaving outbox events pending."""

    async def enqueue_job(self, name: str, *args, **kwargs):
        raise ConnectionError("queue unavailable")


async def _seed(sessionmaker, **overrides) -> uuid.UUID:
    alarm = make_alarm(**overrides)
    async with sessionmaker() as session:
        session.add(alarm)
        await session.commit()
    return alarm.id


async def _alarm(sessionmaker, alarm_id: uuid.UUID) -> Alarm:
    async with sessionmaker() as session:
        row = await session.get(Alarm, alarm_id)
        assert row is not None
        return row


async def _outbox(sessionmaker, alarm_id: uuid.UUID) -> list[AlarmEventOutbox]:
    async with sessionmaker() as session:
        return list(
            (
                await session.scalars(
                    select(AlarmEventOutbox)
                    .where(AlarmEventOutbox.alarm_id == alarm_id)
                    .order_by(AlarmEventOutbox.sequence)
                )
            ).all()
        )


async def _console_csrf(client: AsyncClient, alarm_id: uuid.UUID) -> str:
    page = await client.get(f"/admin/alarms/{alarm_id}")
    expect(page.status_code == 200, page.text)
    return csrf_token(page.text)


def _flash(redis: FakeRedis, client: AsyncClient) -> str | None:
    return redis._store.get(f"admin_session:{client.cookies['admin_session']}:flash")


async def _login(client: AsyncClient) -> None:
    expect(
        (await login_admin(client, TEST_ADMIN_API_KEY, OPERATOR)).status_code == 303,
        "login failed",
    )


async def test_ack_records_actor_events_and_success_flash(
    engine, sessionmaker, fake_redis, settings
) -> None:
    settings.admin_api_key = TEST_ADMIN_API_KEY
    alarm_id = await _seed(sessionmaker)

    async with app_client(settings=settings, engine=engine, redis=fake_redis) as client:
        await _login(client)
        token = await _console_csrf(client, alarm_id)
        response = await client.post(
            f"/admin/alarms/{alarm_id}/ack?lang=en",
            data={"csrf_token": token, "note": "on my way"},
            follow_redirects=False,
        )
        expect(response.status_code == 303, response.text)
        expect(response.headers["location"] == f"/admin/alarms/{alarm_id}?lang=en")
        expect(_flash(fake_redis, client) == "success:alarm_acknowledged")

        worklist = await client.get("/admin")
        expect("notice notice-success" in worklist.text, worklist.text)
        expect(_flash(fake_redis, client) is None, "flash must be consumed once shown")

    alarm = await _alarm(sessionmaker, alarm_id)
    expect(alarm.status == AlarmStatus.ACKNOWLEDGED)
    expect(alarm.acked_by == OPERATOR and alarm.acked_at is not None)
    expect(alarm.meta.get("ack_note") == "on my way")
    events = await _outbox(sessionmaker, alarm_id)
    expect([e.event_type for e in events] == ["alarm.acknowledged", "alarm.state_changed"])
    expect(events[0].payload["acknowledged_by"] == OPERATOR)
    expect(all(e.published_at is not None for e in events), "events must be published")
    expect(len(fake_redis.jobs) == 2)


async def test_ack_with_unavailable_queue_flashes_warning_and_keeps_events_pending(
    engine, sessionmaker, settings
) -> None:
    settings.admin_api_key = TEST_ADMIN_API_KEY
    redis = QueueDownRedis()
    alarm_id = await _seed(sessionmaker)

    async with app_client(settings=settings, engine=engine, redis=redis) as client:
        await _login(client)
        token = await _console_csrf(client, alarm_id)
        response = await client.post(
            f"/admin/alarms/{alarm_id}/ack", data={"csrf_token": token}, follow_redirects=False
        )
        expect(response.status_code == 303, response.text)
        expect(_flash(redis, client) == "warning:alarm_acknowledged_delivery_pending")
        worklist = await client.get("/admin")
        expect("notice notice-warning" in worklist.text, worklist.text)

    expect((await _alarm(sessionmaker, alarm_id)).status == AlarmStatus.ACKNOWLEDGED)
    events = await _outbox(sessionmaker, alarm_id)
    expect(len(events) == 2 and all(e.published_at is None for e in events))
    expect(events[0].last_error == "queue unavailable")


async def test_resolve_records_actor_note_and_state_event(
    engine, sessionmaker, fake_redis, settings
) -> None:
    settings.admin_api_key = TEST_ADMIN_API_KEY
    alarm_id = await _seed(sessionmaker)

    async with app_client(settings=settings, engine=engine, redis=fake_redis) as client:
        await _login(client)
        token = await _console_csrf(client, alarm_id)
        response = await client.post(
            f"/admin/alarms/{alarm_id}/resolve?lang=de",
            data={"csrf_token": token, "note": "  false alarm  "},
            follow_redirects=False,
        )
        expect(response.status_code == 303, response.text)
        expect(response.headers["location"] == f"/admin/alarms/{alarm_id}?lang=de")
        expect(_flash(fake_redis, client) == "success:resolved")

    alarm = await _alarm(sessionmaker, alarm_id)
    expect(alarm.status == AlarmStatus.RESOLVED)
    expect(alarm.resolved_by == OPERATOR and alarm.resolved_at is not None)
    expect(alarm.meta.get("resolve_note") == "false alarm")
    events = await _outbox(sessionmaker, alarm_id)
    expect([e.event_type for e in events] == ["alarm.state_changed"])
    expect(events[0].payload == {"old_state": "triggered", "new_state": "resolved"})


async def test_resolve_with_unavailable_queue_flashes_warning(
    engine, sessionmaker, settings
) -> None:
    settings.admin_api_key = TEST_ADMIN_API_KEY
    redis = QueueDownRedis()
    alarm_id = await _seed(sessionmaker)

    async with app_client(settings=settings, engine=engine, redis=redis) as client:
        await _login(client)
        token = await _console_csrf(client, alarm_id)
        await client.post(
            f"/admin/alarms/{alarm_id}/resolve", data={"csrf_token": token}, follow_redirects=False
        )
        expect(_flash(redis, client) == "warning:resolved")


async def test_cancel_requires_reason_and_records_actor(
    engine, sessionmaker, fake_redis, settings
) -> None:
    settings.admin_api_key = TEST_ADMIN_API_KEY
    alarm_id = await _seed(sessionmaker)

    async with app_client(settings=settings, engine=engine, redis=fake_redis) as client:
        await _login(client)
        token = await _console_csrf(client, alarm_id)
        url = f"/admin/alarms/{alarm_id}/cancel"

        missing = await client.post(url, data={"csrf_token": token}, follow_redirects=False)
        expect(missing.status_code == 422, missing.text)
        blank = await client.post(
            url, data={"csrf_token": token, "reason": "   "}, follow_redirects=False
        )
        expect(blank.status_code == 422, blank.text)
        expect("reason_required" in blank.text, blank.text)
        expect((await _alarm(sessionmaker, alarm_id)).status == AlarmStatus.TRIGGERED)
        expect(await _outbox(sessionmaker, alarm_id) == [])

        response = await client.post(
            f"{url}?lang=en",
            data={"csrf_token": token, "reason": " test drill "},
            follow_redirects=False,
        )
        expect(response.status_code == 303, response.text)
        expect(response.headers["location"] == f"/admin/alarms/{alarm_id}?lang=en")
        expect(_flash(fake_redis, client) == "success:cancelled")

    alarm = await _alarm(sessionmaker, alarm_id)
    expect(alarm.status == AlarmStatus.CANCELLED)
    expect(alarm.cancelled_by == OPERATOR and alarm.cancelled_at is not None)
    expect(alarm.meta.get("cancel_note") == "test drill")
    events = await _outbox(sessionmaker, alarm_id)
    expect([e.payload["new_state"] for e in events] == ["cancelled"])


async def test_conflicting_transition_on_closed_alarm_is_rejected(
    engine, sessionmaker, fake_redis, settings
) -> None:
    settings.admin_api_key = TEST_ADMIN_API_KEY
    alarm_id = await _seed(
        sessionmaker, status=AlarmStatus.RESOLVED, resolved_at=datetime.now(UTC), resolved_by="Ops"
    )

    async with app_client(settings=settings, engine=engine, redis=fake_redis) as client:
        await _login(client)
        token = await _console_csrf(client, alarm_id)
        response = await client.post(
            f"/admin/alarms/{alarm_id}/cancel",
            data={"csrf_token": token, "reason": "late"},
            follow_redirects=False,
        )
        expect(response.status_code == 409, response.text)

    alarm = await _alarm(sessionmaker, alarm_id)
    expect(alarm.status == AlarmStatus.RESOLVED and alarm.cancelled_by is None)
    expect(await _outbox(sessionmaker, alarm_id) == [])


async def test_note_is_stripped_attributed_and_does_not_change_state(
    engine, sessionmaker, fake_redis, settings
) -> None:
    settings.admin_api_key = TEST_ADMIN_API_KEY
    alarm_id = await _seed(sessionmaker)

    async with app_client(settings=settings, engine=engine, redis=fake_redis) as client:
        await _login(client)
        token = await _console_csrf(client, alarm_id)
        response = await client.post(
            f"/admin/alarms/{alarm_id}/notes?lang=en",
            data={"csrf_token": token, "note": "  caller reached  \n"},
            follow_redirects=False,
        )
        expect(response.status_code == 303, response.text)
        expect(response.headers["location"] == f"/admin/alarms/{alarm_id}?lang=en")
        expect(_flash(fake_redis, client) == "success:note_added")

    async with sessionmaker() as session:
        notes = list((await session.scalars(select(AlarmNote))).all())
    expect(len(notes) == 1)
    expect(notes[0].note == "caller reached")
    expect(notes[0].created_by == OPERATOR and notes[0].note_type == "manual")
    expect(notes[0].alarm_id == alarm_id)
    expect((await _alarm(sessionmaker, alarm_id)).status == AlarmStatus.TRIGGERED)
    expect(await _outbox(sessionmaker, alarm_id) == [])


@pytest.mark.parametrize(
    ("suffix", "data"),
    [
        ("ack", {}),
        ("resolve", {}),
        ("cancel", {"reason": "because"}),
        ("notes", {"note": "hello"}),
    ],
)
async def test_missing_or_invalid_csrf_is_rejected_without_changes(
    engine, sessionmaker, fake_redis, settings, suffix, data
) -> None:
    settings.admin_api_key = TEST_ADMIN_API_KEY
    alarm_id = await _seed(sessionmaker)

    async with app_client(settings=settings, engine=engine, redis=fake_redis) as client:
        await _login(client)
        url = f"/admin/alarms/{alarm_id}/{suffix}"
        absent = await client.post(url, data=data, follow_redirects=False)
        wrong = await client.post(url, data={**data, "csrf_token": "wrong"}, follow_redirects=False)
        expect(absent.status_code == 403, absent.text)
        expect(wrong.status_code == 403, wrong.text)
        expect(_flash(fake_redis, client) is None)

    expect((await _alarm(sessionmaker, alarm_id)).status == AlarmStatus.TRIGGERED)
    expect(await _outbox(sessionmaker, alarm_id) == [])
    async with sessionmaker() as session:
        expect(list((await session.scalars(select(AlarmNote))).all()) == [])


async def test_action_without_session_is_unauthorized(
    engine, sessionmaker, fake_redis, settings
) -> None:
    settings.admin_api_key = TEST_ADMIN_API_KEY
    alarm_id = await _seed(sessionmaker)
    async with app_client(settings=settings, engine=engine, redis=fake_redis) as client:
        response = await client.post(
            f"/admin/alarms/{alarm_id}/ack", data={"csrf_token": "x"}, follow_redirects=False
        )
    expect(response.status_code == 401, response.text)
    expect((await _alarm(sessionmaker, alarm_id)).status == AlarmStatus.TRIGGERED)


async def test_bulk_mixed_selection_reports_changed_unchanged_and_missing(
    engine, sessionmaker, fake_redis, settings
) -> None:
    settings.admin_api_key = TEST_ADMIN_API_KEY
    first = await _seed(sessionmaker)
    second = await _seed(sessionmaker)
    resolved = await _seed(
        sessionmaker, status=AlarmStatus.RESOLVED, resolved_at=datetime.now(UTC), resolved_by="Ops"
    )
    deleted = await _seed(sessionmaker, deleted_at=datetime.now(UTC), deleted_by="Ops")
    unknown = uuid.uuid4()

    async with app_client(settings=settings, engine=engine, redis=fake_redis) as client:
        await _login(client)
        token = await _console_csrf(client, first)
        response = await client.post(
            "/admin/alarms/bulk?lang=en",
            data={
                "csrf_token": token,
                "action": "resolve",
                "alarm_id": [
                    str(first),
                    str(second),
                    str(resolved),
                    str(unknown),
                    str(deleted),
                    "not-a-uuid",
                ],
            },
            follow_redirects=False,
        )
        expect(response.status_code == 303, response.text)
        expect(response.headers["location"] == "/admin?lang=en")
        # 2 resolved, 1 conflicting (already resolved), 3 missing = unknown + deleted + invalid.
        expect(_flash(fake_redis, client) == "success:bulk_2_1_3")

    for alarm_id in (first, second):
        alarm = await _alarm(sessionmaker, alarm_id)
        expect(alarm.status == AlarmStatus.RESOLVED and alarm.resolved_by == OPERATOR)
        expect(len(await _outbox(sessionmaker, alarm_id)) == 1)
    untouched = await _alarm(sessionmaker, resolved)
    expect(untouched.status == AlarmStatus.RESOLVED and untouched.resolved_by == "Ops")
    expect(await _outbox(sessionmaker, resolved) == [])
    gone = await _alarm(sessionmaker, deleted)
    expect(gone.status == AlarmStatus.TRIGGERED and gone.deleted_by == "Ops")
    expect(await _outbox(sessionmaker, deleted) == [])


async def test_bulk_ack_and_cancel_apply_target_state_with_operator(
    engine, sessionmaker, fake_redis, settings
) -> None:
    settings.admin_api_key = TEST_ADMIN_API_KEY
    to_ack = await _seed(sessionmaker)
    to_cancel = await _seed(sessionmaker)

    async with app_client(settings=settings, engine=engine, redis=fake_redis) as client:
        await _login(client)
        token = await _console_csrf(client, to_ack)
        ack = await client.post(
            "/admin/alarms/bulk",
            data={"csrf_token": token, "action": "ack", "alarm_id": [str(to_ack)]},
            follow_redirects=False,
        )
        expect(ack.status_code == 303, ack.text)
        expect(_flash(fake_redis, client) == "success:bulk_1_0_0")
        cancel = await client.post(
            "/admin/alarms/bulk",
            data={
                "csrf_token": token,
                "action": "cancel",
                "reason": "drill",
                "alarm_id": [str(to_cancel)],
            },
            follow_redirects=False,
        )
        expect(cancel.status_code == 303, cancel.text)

    acked = await _alarm(sessionmaker, to_ack)
    expect(acked.status == AlarmStatus.ACKNOWLEDGED and acked.acked_by == OPERATOR)
    cancelled = await _alarm(sessionmaker, to_cancel)
    expect(cancelled.status == AlarmStatus.CANCELLED and cancelled.cancelled_by == OPERATOR)
    expect(cancelled.meta.get("cancel_note") == "drill")


async def test_bulk_rejects_empty_selection_and_cancel_without_reason(
    engine, sessionmaker, fake_redis, settings
) -> None:
    settings.admin_api_key = TEST_ADMIN_API_KEY
    alarm_id = await _seed(sessionmaker)

    async with app_client(settings=settings, engine=engine, redis=fake_redis) as client:
        await _login(client)
        token = await _console_csrf(client, alarm_id)
        empty = await client.post(
            "/admin/alarms/bulk",
            data={"csrf_token": token, "action": "ack"},
            follow_redirects=False,
        )
        expect(empty.status_code == 422 and "selection_required" in empty.text, empty.text)
        no_reason = await client.post(
            "/admin/alarms/bulk",
            data={"csrf_token": token, "action": "cancel", "alarm_id": [str(alarm_id)]},
            follow_redirects=False,
        )
        expect(no_reason.status_code == 422 and "reason_required" in no_reason.text)
        bad_action = await client.post(
            "/admin/alarms/bulk",
            data={"csrf_token": token, "action": "delete", "alarm_id": [str(alarm_id)]},
            follow_redirects=False,
        )
        expect(bad_action.status_code == 422, bad_action.text)
        bad_csrf = await client.post(
            "/admin/alarms/bulk",
            data={"csrf_token": "wrong", "action": "ack", "alarm_id": [str(alarm_id)]},
            follow_redirects=False,
        )
        expect(bad_csrf.status_code == 403, bad_csrf.text)

    expect((await _alarm(sessionmaker, alarm_id)).status == AlarmStatus.TRIGGERED)
    expect(await _outbox(sessionmaker, alarm_id) == [])


async def test_detail_page_shows_and_consumes_the_action_flash(
    engine, sessionmaker, fake_redis, settings
) -> None:
    settings.admin_api_key = TEST_ADMIN_API_KEY
    alarm_id = await _seed(sessionmaker)

    async with app_client(settings=settings, engine=engine, redis=fake_redis) as client:
        await _login(client)
        token = await _console_csrf(client, alarm_id)
        await client.post(
            f"/admin/alarms/{alarm_id}/ack?lang=en",
            data={"csrf_token": token},
            follow_redirects=False,
        )
        detail = await client.get(f"/admin/alarms/{alarm_id}?lang=en")
        expect("notice notice-success" in detail.text, detail.text)
        expect("Alarm acknowledged. Escalation has stopped." in detail.text, detail.text)
        expect(_flash(fake_redis, client) is None, "flash must be consumed once shown")


async def test_unknown_alarm_renders_the_localized_error_page(
    engine, sessionmaker, fake_redis, settings
) -> None:
    settings.admin_api_key = TEST_ADMIN_API_KEY

    async with app_client(settings=settings, engine=engine, redis=fake_redis) as client:
        await _login(client)
        missing = await client.get(f"/admin/alarms/{uuid.uuid4()}?lang=de")
        expect(missing.status_code == 404, missing.text)
        expect(missing.headers["content-type"].startswith("text/html"), missing.headers)
        expect("Dieser Eintrag existiert nicht" in missing.text, missing.text)
        expect('href="/admin"' in missing.text, missing.text)
        api = await client.get(f"/v1/alarms/{uuid.uuid4()}")
        expect(not api.headers["content-type"].startswith("text/html"), api.headers)
