"""Bounded keyset paging, cursor pagination, and progressive navigation contracts."""

from __future__ import annotations

import html
import re
import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
from sqlalchemy import delete, event

from escalane.alarms.history import (
    history_page,
    parse_history_cursor,
)
from escalane.persistence.models import Alarm, AlarmNote, AlarmNotification, Site
from tests.support.admin import logged_in_admin_client, login_admin
from tests.support.clients import app_client
from tests.support.constants import TEST_ADMIN_API_KEY, value_for_test
from tests.support.factories import make_alarm

pytestmark = pytest.mark.integration


def _next(page: str, attribute: str = "data-older-activity") -> str:
    if attribute == "data-older-activity":
        match = re.search(r'href="([^"]+)" data-older-activity', page)
    else:
        match = re.search(r'href="([^"]+)"[^>]*>Next page', page)
    assert match
    return html.unescape(match.group(1))


async def test_configuration_keyset_is_bounded_and_survives_cursor_deletion(
    engine, sessionmaker, fake_redis, settings
):
    async with sessionmaker() as session:
        session.add_all(Site(id=f"site-{i:03d}", name=f"Site {i}") for i in range(105))
        await session.commit()
    async with logged_in_admin_client(
        settings=settings,
        engine=engine,
        redis=fake_redis,
        admin_key=TEST_ADMIN_API_KEY,
        operator_name="Operator",
    ) as client:
        first = await client.get("/admin/configuration/sites?lang=en")
        assert first.text.count('class="resource-record"') == 50
        assert 'name="version" value="1"' in first.text
        assert "/save?lang=en" in first.text
        url = _next(first.text, "next")
        assert "after=site-049" in url and "lang=en" in url
        async with sessionmaker() as session:
            await session.execute(delete(Site).where(Site.id == "site-049"))
            session.add(Site(id="site-999", name="Appended"))
            await session.commit()
        second = await client.get(url)
        assert second.text.count('class="resource-record"') == 50
        assert 'value="site-050"' in second.text and 'value="site-099"' in second.text
        third = await client.get(_next(second.text, "next"))
        assert third.text.count('class="resource-record"') == 6
        assert "First page" in third.text and "Next page" not in third.text
        for query in ("limit=0", "limit=101", "after=" + "x" * 201):
            assert (await client.get("/admin/configuration/sites?" + query)).status_code == 422
        maximum = await client.get("/admin/configuration/sites?limit=100")
        assert maximum.text.count('class="resource-record"') == 100
        empty = await client.get("/admin/configuration/sites?after=zzz&lang=en")
        assert "No records" in empty.text


async def test_history_cursor_ties_deletion_and_append_use_bounded_projection(engine, sessionmaker):
    alarm = make_alarm(person_id=None, room_id=None, site_id=None, device_id=None)
    timestamp = datetime(2026, 1, 2, tzinfo=UTC)
    async with sessionmaker() as session:
        session.add(alarm)
        await session.flush()
        for i in range(65):
            session.add(
                AlarmNote(
                    id=uuid.UUID(int=i + 1),
                    alarm_id=alarm.id,
                    created_at=timestamp,
                    note=f"Note {i}",
                )
            )
            session.add(
                AlarmNotification(
                    id=uuid.UUID(int=i + 1),
                    alarm_id=alarm.id,
                    created_at=timestamp,
                    channel="sms",
                    payload={},
                    result="ok",
                )
            )
        await session.commit()
    statements = []

    def capture(connection, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", capture)
    try:
        async with sessionmaker() as session:
            first, cursor, creation = await history_page(session, alarm.id, None)
            assert len(first) == 50 and cursor and not creation
            boundary = parse_history_cursor(cursor)
            assert boundary and boundary.kind == "notification"
            await session.execute(
                delete(AlarmNotification).where(AlarmNotification.id == boundary.id)
            )
            session.add(
                AlarmNote(alarm_id=alarm.id, created_at=timestamp + timedelta(days=1), note="New")
            )
            await session.commit()
            all_events = list(first)
            while cursor:
                older, cursor, creation = await history_page(
                    session, alarm.id, parse_history_cursor(cursor)
                )
                assert len(older) + creation <= 50
                all_events = older + all_events
            assert creation
            keys = [item["key"] for item in all_events]
            assert len(keys) == len(set(keys)) == 130
            assert not any(item["description"].endswith(": New") for item in all_events)
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", capture)
    reads = [sql for sql in statements if sql.startswith("SELECT")]
    assert reads and all(
        "LIMIT" in sql and "OFFSET" not in sql.replace("OFFSET ?", "") for sql in reads
    )
    assert all("payload" not in sql for sql in reads)


async def test_history_routes_keep_creation_on_earliest_page_and_authentication(
    engine, sessionmaker, fake_redis, settings
):
    alarm = make_alarm(person_id=None, room_id=None, site_id=None, device_id=None)
    async with sessionmaker() as session:
        session.add(alarm)
        await session.flush()
        session.add_all(
            AlarmNote(
                alarm_id=alarm.id,
                created_at=datetime(2020, 1, 1, tzinfo=UTC),
                note=f"Historical {i}",
            )
            for i in range(50)
        )
        await session.commit()
    path = f"/admin/alarms/{alarm.id}"
    async with logged_in_admin_client(
        settings=settings,
        engine=engine,
        redis=fake_redis,
        admin_key=TEST_ADMIN_API_KEY,
        operator_name="Operator",
    ) as client:
        first = await client.get(path + "?lang=en")
        assert first.text.count("data-history-event=") == 50
        assert "Alarm created" not in first.text
        last = await client.get(_next(first.text))
        assert last.text.count("data-history-event=") == 1
        assert "Alarm created" in last.text and "data-older-activity" not in last.text
        assert "Latest activity" in last.text
        for suffix in ("", "/drawer", "/history"):
            invalid = await client.get(path + suffix + "?before=bad")
            assert invalid.status_code == 422
            assert "text/html" in invalid.headers["content-type"]
            assert "invalid_history_cursor" in invalid.text
        fragment = await client.get(path + "/history?lang=de")
        assert "Ältere Aktivitäten" in fragment.text
        await client.post("/admin/logout")
        client.cookies.clear()
        assert (await client.get(path + "/history")).status_code == 401


def _alarm(*, alarm_id: int, severity: str, source: str) -> Alarm:
    return make_alarm(
        alarm_id=uuid.UUID(int=alarm_id),
        source=source,
        severity=severity,
        ack_token=value_for_test(f"admin-page-{alarm_id}"),
    )


def _alarm_ids(page: str) -> list[str]:
    return list(dict.fromkeys(re.findall(r'href="/admin/alarms/([0-9a-f-]+)\?lang=', page)))


def _next_page_url(page: str) -> str:
    match = re.search(r'<a class="button" href="([^"]+)">Next page\s*<span', page)
    assert match is not None
    return html.unescape(match.group(1))


async def test_admin_cursor_uses_selected_sort_and_preserves_query(
    engine, sessionmaker, seeded_db, fake_redis, settings
) -> None:
    settings.admin_api_key = TEST_ADMIN_API_KEY
    source = "pagination&special"
    alarms = [
        _alarm(alarm_id=2, severity="P0", source=source),
        _alarm(alarm_id=4, severity="P1", source=source),
        _alarm(alarm_id=1, severity="P2", source=source),
    ]
    async with sessionmaker() as session:
        session.add_all(alarms)
        await session.commit()

    async with app_client(settings=settings, engine=engine, redis=fake_redis) as client:
        assert (await login_admin(client, TEST_ADMIN_API_KEY, "Pagination Ops")).status_code == 303
        first = await client.get(
            "/admin",
            params={
                "search": source,
                "sort_by": "severity",
                "order": "asc",
                "limit": 2,
                "lang": "en",
            },
        )
        next_url = _next_page_url(first.text)
        second = await client.get(next_url)

    assert _alarm_ids(first.text) == [str(alarms[0].id), str(alarms[1].id)]
    assert _alarm_ids(second.text) == [str(alarms[2].id)]
    params = parse_qs(urlsplit(next_url).query)
    assert params == {
        "search": [source],
        "sort_by": ["severity"],
        "order": ["asc"],
        "limit": ["2"],
        "lang": ["en"],
        "cursor": [str(alarms[1].id)],
    }


async def test_configuration_edit_forms_show_stored_values(
    engine, sessionmaker, fake_redis, settings
):
    async with sessionmaker() as session:
        session.add(Site(id="site-north", name="North Campus"))
        await session.commit()
    async with logged_in_admin_client(
        settings=settings,
        engine=engine,
        redis=fake_redis,
        admin_key=TEST_ADMIN_API_KEY,
        operator_name="Operator",
    ) as client:
        page = await client.get("/admin/configuration/sites?lang=en")
        assert 'id="site-north-name" name="name" value="North Campus"' in page.text
