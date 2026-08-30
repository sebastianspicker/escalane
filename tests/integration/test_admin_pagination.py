"""Cursor pagination tests for ordered administrator alarm lists."""

from __future__ import annotations

import html
import re
import uuid
from urllib.parse import parse_qs, urlsplit

import pytest

from escalane.persistence.models import Alarm
from tests.support.admin_test_helpers import login_admin
from tests.support.api_test_helpers import app_client, make_alarm
from tests.support.constants import TEST_ADMIN_API_KEY, value_for_test

pytestmark = pytest.mark.integration


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
