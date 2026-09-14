"""Upgrade a populated previous revision without changing legacy delivery payloads."""

import uuid
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from unittest.mock import patch

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, select, text

from escalane.persistence.base import Base
from escalane.persistence.models import Alarm, AlarmNotification


def _migration():
    path = (
        Path(__file__).resolve().parents[2]
        / "migrations/versions/0008_delivery_dashboard_revision.py"
    )
    spec = spec_from_file_location("optimization_migration", path)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_populated_previous_revision_backfill_and_rollback(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'upgrade.db'}")
    migration = _migration()
    with engine.begin() as connection:
        Base.metadata.create_all(connection)
        with patch.object(migration, "op", Operations(MigrationContext.configure(connection))):
            migration.downgrade()
        # Equivalent 0007 audit layout, populated by the previous application's
        # writer (no logical key). Cross the backfill's 1,000-record boundary.
        alarm_id = uuid.uuid4()
        connection.execute(
            Alarm.__table__.insert().values(id=alarm_id, source="synthetic", event="migration")
        )
        payloads = [
            {"action": "create_ticket", "ticket_id": 42, "delivery_id": "preserved"},
            {"action": "ack_update", "ticket_id": 42},
            {"state": "resolved"},
            {"step_no": 3},
            {},
        ]
        import json

        rows = [
            {
                "id": uuid.UUID(int=index + 1).hex,
                "alarm": alarm_id.hex,
                "payload": json.dumps(payloads[index % 5]),
            }
            for index in range(1005)
        ]
        connection.execute(
            text(
                "INSERT INTO alarm_notifications (id, alarm_id, channel, payload, result) "
                "VALUES (:id, :alarm, 'zammad', :payload, 'ok')"
            ),
            rows,
        )
        with patch.object(migration, "op", Operations(MigrationContext.configure(connection))):
            migration.upgrade()
        audits = connection.execute(
            select(AlarmNotification.payload, AlarmNotification.logical_delivery_key).order_by(
                AlarmNotification.id
            )
        ).all()
        assert len(audits) == 1005
        assert [row[1] for row in audits[:5]] == [
            "create_ticket",
            "ack_update:42",
            "state:resolved",
            "step:3",
            None,
        ]
        assert [row[0] for row in audits[:5]] == payloads
        before = connection.scalar(text("SELECT version FROM dashboard_revision WHERE id = 1"))
        connection.execute(text("UPDATE alarms SET severity = 'P2'"))
        assert (
            connection.scalar(text("SELECT version FROM dashboard_revision WHERE id = 1")) > before
        )
        with patch.object(migration, "op", Operations(MigrationContext.configure(connection))):
            migration.downgrade()
        assert "logical_delivery_key" not in {
            column["name"] for column in inspect(connection).get_columns("alarm_notifications")
        }
        assert connection.scalar(text("SELECT count(*) FROM alarm_notifications")) == 1005
    engine.dispose()
