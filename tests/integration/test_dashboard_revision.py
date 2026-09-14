"""Transactional invalidation and cache race regressions."""

import uuid
from unittest.mock import patch

from sqlalchemy import delete, update

from escalane.operations.dashboard import dashboard_counts, dashboard_revision
from escalane.persistence.models import Alarm, Person, Room


async def test_revision_tracks_core_writes_and_rollbacks(sessionmaker, seeded_db):
    async with sessionmaker() as session:
        before = await dashboard_revision(session)
        alarm = Alarm(source="synthetic", event="revision", person_id="ma-012", room_id="bg-1.23")
        session.add(alarm)
        await session.flush()
        inserted = await dashboard_revision(session)
        assert inserted != before
        await session.rollback()
        assert await dashboard_revision(session) == before
        session.add(Alarm(source="synthetic", event="revision"))
        await session.commit()
        previous = await dashboard_revision(session)
        for model, values in (
            (Alarm, {"severity": "P1"}),
            (Alarm, {"acked_by": "Synthetic Operator"}),
            (Person, {"display_name": "Synthetic Person"}),
            (Room, {"label": "Synthetic Room"}),
        ):
            await session.execute(update(model).values(**values))
            await session.commit()
            current = await dashboard_revision(session)
            assert current != previous
            previous = current
        await session.execute(update(Alarm).values(meta={"diagnostic": "synthetic"}))
        await session.commit()
        assert await dashboard_revision(session) == previous
        await session.execute(delete(Alarm))
        await session.commit()
        assert await dashboard_revision(session) != previous


async def test_count_cache_epoch_expiry_and_redis_loss(sessionmaker, fake_redis):
    async with sessionmaker() as session:
        counts = await dashboard_counts(session, fake_redis)
        assert counts["triggered"] == 0
        key = f"escalane:dashboard:counts:{await dashboard_revision(session)}"
        assert await fake_redis.get(key)
        session.add(Alarm(source="synthetic", event="counts"))
        await session.commit()
        assert (await dashboard_counts(session, fake_redis))["triggered"] == 1
        fake_redis.advance(61)
        assert await fake_redis.get(key) is None
        with (
            patch.object(fake_redis, "get", side_effect=ConnectionError),
            patch.object(fake_redis, "set", side_effect=ConnectionError),
        ):
            assert (await dashboard_counts(session, fake_redis))["triggered"] == 1


async def test_count_race_does_not_publish_under_stale_revision(sessionmaker, fake_redis):
    from escalane.operations.dashboard import visible_counts

    async with sessionmaker() as session:
        previous = await dashboard_revision(session)

        async def racing_counts(current_session):
            counts = await visible_counts(current_session)
            current_session.add(Alarm(id=uuid.uuid4(), source="synthetic", event="race"))
            await current_session.commit()
            return counts

        with patch("escalane.operations.dashboard.visible_counts", side_effect=racing_counts):
            assert (await dashboard_counts(session, fake_redis))["triggered"] == 0
        assert await fake_redis.get(f"escalane:dashboard:counts:{previous}") is None
        assert (await dashboard_counts(session, fake_redis))["triggered"] == 1


async def test_metadata_create_is_idempotent(engine, sessionmaker):
    from escalane.persistence.base import Base

    async with sessionmaker() as session:
        previous = await dashboard_revision(session)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with sessionmaker() as session:
        assert await dashboard_revision(session) == previous
