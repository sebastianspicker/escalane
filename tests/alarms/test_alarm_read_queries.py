"""Tests for escalane.alarms.queries."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from escalane.alarms.queries import (
    AlarmFilters,
    SortField,
    SortOrder,
    alarm_display_labels,
    alarm_statistics,
    list_alarm_page,
    list_alarms_for_export,
    list_worklist_page,
)
from escalane.persistence.models import Alarm, AlarmStatus
from tests.support.factories import make_alarm

pytestmark = [pytest.mark.unit]

_BASE = datetime(2026, 1, 1, tzinfo=UTC)


async def _persist(sessionmaker: async_sessionmaker, *alarms: Alarm) -> None:
    async with sessionmaker() as session:
        session.add_all(alarms)
        await session.commit()


def _alarm(minute: int, **overrides: object) -> Alarm:
    return make_alarm(created_at=_BASE + timedelta(minutes=minute), **overrides)


async def test_alarm_page_filters_sorts_and_walks_keyset(sessionmaker, seeded_db):
    alarms = [_alarm(i, severity="P1" if i % 2 else "P0") for i in range(5)]
    deleted = _alarm(9, severity="P1", deleted_at=_BASE)
    await _persist(sessionmaker, *alarms, deleted)
    expected = [alarms[3].id, alarms[1].id]

    async with sessionmaker() as session:
        filters = AlarmFilters(severity="P1")
        first = await list_alarm_page(
            session,
            filters,
            cursor=None,
            sort_by=SortField.CREATED_AT,
            sort_order=SortOrder.DESC,
            limit=1,
        )
        assert [alarm.id for alarm in first.alarms] == expected[:1]
        assert first.next_cursor == expected[0]
        second = await list_alarm_page(
            session,
            filters,
            cursor=first.next_cursor,
            sort_by=SortField.CREATED_AT,
            sort_order=SortOrder.DESC,
            limit=1,
        )
        assert [alarm.id for alarm in second.alarms] == expected[1:]
        assert second.next_cursor is None
        ascending = await list_alarm_page(
            session,
            AlarmFilters(),
            cursor=None,
            sort_by=SortField.CREATED_AT,
            sort_order=SortOrder.ASC,
            limit=10,
        )
        assert [alarm.id for alarm in ascending.alarms] == [alarm.id for alarm in alarms]


async def test_alarm_page_ignores_cursor_that_no_longer_matches_filters(sessionmaker, seeded_db):
    p0 = _alarm(0, severity="P0")
    p1_old, p1_new = _alarm(1, severity="P1"), _alarm(2, severity="P1")
    await _persist(sessionmaker, p0, p1_old, p1_new)

    async with sessionmaker() as session:
        page = await list_alarm_page(
            session,
            AlarmFilters(severity="P1"),
            cursor=p0.id,
            sort_by=SortField.CREATED_AT,
            sort_order=SortOrder.DESC,
            limit=10,
        )

    assert [alarm.id for alarm in page.alarms] == [p1_new.id, p1_old.id]


async def test_alarm_page_breaks_sort_ties_by_id(sessionmaker, seeded_db):
    alarms = [_alarm(i, status=AlarmStatus.ACKNOWLEDGED) for i in range(3)]
    await _persist(sessionmaker, *alarms)
    by_id = sorted(alarm.id for alarm in alarms)

    async with sessionmaker() as session:
        first = await list_alarm_page(
            session,
            AlarmFilters(),
            cursor=None,
            sort_by=SortField.STATUS,
            sort_order=SortOrder.ASC,
            limit=2,
        )
        rest = await list_alarm_page(
            session,
            AlarmFilters(),
            cursor=first.next_cursor,
            sort_by=SortField.STATUS,
            sort_order=SortOrder.ASC,
            limit=2,
        )

    assert [alarm.id for alarm in first.alarms] + [alarm.id for alarm in rest.alarms] == by_id
    assert rest.next_cursor is None


async def test_export_selection_is_filtered_newest_first_and_bounded(sessionmaker, seeded_db):
    alarms = [_alarm(i, source="export" if i < 3 else "other") for i in range(5)]
    await _persist(sessionmaker, *alarms)

    async with sessionmaker() as session:
        selected = await list_alarms_for_export(
            session,
            AlarmFilters(source="export", created_after=_BASE + timedelta(minutes=1)),
            limit=5,
        )
        bounded = await list_alarms_for_export(session, AlarmFilters(), limit=2)

    assert [alarm.id for alarm in selected] == [alarms[2].id, alarms[1].id]
    assert [alarm.id for alarm in bounded] == [alarms[4].id, alarms[3].id]


async def test_worklist_page_filters_search_and_labels(sessionmaker, seeded_db):
    labelled = _alarm(0, severity="P2")
    unlabelled = _alarm(1, severity="P2", person_id=None, room_id=None, event="needle")
    other = _alarm(2, severity="P3")
    await _persist(sessionmaker, labelled, unlabelled, other)

    async with sessionmaker() as session:
        page = await list_worklist_page(
            session,
            status_filter="not-a-status",
            severity_filter="P2",
            search=None,
            sort_by=SortField.CREATED_AT,
            sort_order=SortOrder.DESC,
            cursor=None,
            limit=1,
        )
        assert [(row.alarm.id, row.person, row.room) for row in page.rows] == [
            (unlabelled.id, None, None)
        ]
        assert page.next_cursor == unlabelled.id
        older = await list_worklist_page(
            session,
            status_filter=None,
            severity_filter="P2",
            search=None,
            sort_by=SortField.CREATED_AT,
            sort_order=SortOrder.DESC,
            cursor=page.next_cursor,
            limit=1,
        )
        assert [(row.alarm.id, row.person, row.room) for row in older.rows] == [
            (labelled.id, "Person X", "Raum 1.23")
        ]
        assert older.next_cursor is None
        ignored_severity = await list_worklist_page(
            session,
            status_filter=None,
            severity_filter="P3",
            search="  Raum 1.23 ",
            sort_by=SortField.CREATED_AT,
            sort_order=SortOrder.ASC,
            cursor=None,
            limit=10,
        )
        assert [row.alarm.id for row in ignored_severity.rows] == [
            labelled.id,
            other.id,
        ]


async def test_statistics_count_only_active_alarms(sessionmaker, seeded_db):
    await _persist(
        sessionmaker,
        _alarm(0),
        _alarm(1, status=AlarmStatus.RESOLVED, severity="P1"),
        _alarm(2, deleted_at=_BASE),
    )

    async with sessionmaker() as session:
        stats = await alarm_statistics(session)

    assert stats.total == 2
    assert stats.by_status == {"triggered": 1, "resolved": 1}
    assert stats.by_severity == {"P0": 1, "P1": 1}


async def test_display_labels_resolve_master_data_or_none(sessionmaker, seeded_db):
    labelled = _alarm(0)
    unlabelled = _alarm(1, person_id=None, room_id=None)
    await _persist(sessionmaker, labelled, unlabelled)

    async with sessionmaker() as session:
        assert await alarm_display_labels(session, labelled.id) == ("Person X", "Raum 1.23")
        assert await alarm_display_labels(session, unlabelled.id) == (None, None)
        assert await alarm_display_labels(session, make_alarm().id) is None
