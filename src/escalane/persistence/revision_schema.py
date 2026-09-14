"""SQLite metadata-fixture equivalent of the production dashboard triggers."""

from __future__ import annotations

import uuid

from sqlalchemy import event

from escalane.persistence.base import Base

VISIBLE_ALARM_FIELDS = (
    "status",
    "severity",
    "source",
    "event",
    "created_at",
    "person_id",
    "room_id",
    "acked_by",
    "acked_at",
    "resolved_at",
    "cancelled_at",
    "deleted_at",
)


def install_sqlite_revision(connection) -> None:
    """Install transaction-bound invalidation even for Core and bulk writers."""
    connection.exec_driver_sql(
        "INSERT OR IGNORE INTO dashboard_revision (id, epoch, version) VALUES (1, ?, 0)",
        (uuid.uuid4().hex,),
    )
    bump = "UPDATE dashboard_revision SET version = version + 1 WHERE id = 1;"
    for action in ("INSERT", "DELETE"):
        connection.exec_driver_sql(
            f"CREATE TRIGGER IF NOT EXISTS dashboard_alarm_{action.lower()} "
            f"AFTER {action} ON alarms "
            f"BEGIN {bump} END"
        )
    changed = " OR ".join(f"OLD.{field} IS NOT NEW.{field}" for field in VISIBLE_ALARM_FIELDS)
    connection.exec_driver_sql(
        "CREATE TRIGGER IF NOT EXISTS dashboard_alarm_update AFTER UPDATE ON alarms "
        f"WHEN {changed} "
        f"BEGIN {bump} END"
    )
    for table, field in (("persons", "display_name"), ("rooms", "label")):
        connection.exec_driver_sql(
            f"CREATE TRIGGER IF NOT EXISTS dashboard_{table}_update AFTER UPDATE ON {table} "
            f"WHEN OLD.{field} IS NOT NEW.{field} BEGIN {bump} END"
        )


@event.listens_for(Base.metadata, "after_create")
def _sqlite_revision_after_create(target, connection, **kwargs) -> None:
    if connection.dialect.name == "sqlite":
        install_sqlite_revision(connection)
