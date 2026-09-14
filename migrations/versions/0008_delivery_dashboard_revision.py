"""Add logical audit keys and transactional dashboard revision.

Revision ID: 0008
Revises: 0007
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

_VISIBLE_FIELDS = (
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


def _logical_key(payload: dict) -> str | None:
    if not isinstance(payload, dict):
        return None
    action = payload.get("action")
    if action == "create_ticket":
        return "create_ticket"
    if action == "ack_update" and payload.get("ticket_id") is not None:
        return f"ack_update:{payload['ticket_id']}"
    if payload.get("state") is not None:
        return f"state:{payload['state']}"
    if payload.get("step_no") is not None:
        return f"step:{payload['step_no']}"
    return None


def _backfill() -> None:
    if op.get_context().as_sql:
        # Offline PostgreSQL scripts retain bounded batches on the server.
        op.execute("""
            DO $$ DECLARE changed integer; BEGIN LOOP
                UPDATE alarm_notifications SET logical_delivery_key = CASE
                    WHEN payload->>'action' = 'create_ticket' THEN 'create_ticket'
                    WHEN payload->>'action' = 'ack_update' AND payload->>'ticket_id' IS NOT NULL
                        THEN 'ack_update:' || (payload->>'ticket_id')
                    WHEN payload->>'state' IS NOT NULL THEN 'state:' || (payload->>'state')
                    ELSE 'step:' || (payload->>'step_no') END
                WHERE id IN (
                    SELECT id FROM alarm_notifications WHERE logical_delivery_key IS NULL AND (
                        payload->>'action' = 'create_ticket' OR
                        (payload->>'action' = 'ack_update' AND payload->>'ticket_id' IS NOT NULL) OR
                        payload->>'state' IS NOT NULL OR payload->>'step_no' IS NOT NULL
                    ) ORDER BY id LIMIT 1000
                );
                GET DIAGNOSTICS changed = ROW_COUNT;
                EXIT WHEN changed = 0;
            END LOOP; END $$
        """)
        return
    connection = op.get_bind()
    audit = sa.table(
        "alarm_notifications",
        sa.column("id", sa.Uuid()),
        sa.column("payload", sa.JSON()),
        sa.column("logical_delivery_key", sa.String()),
    )
    cursor = None
    while True:
        query = sa.select(audit.c.id, audit.c.payload).order_by(audit.c.id).limit(1000)
        if cursor is not None:
            query = query.where(audit.c.id > cursor)
        rows = connection.execute(query).all()
        if not rows:
            return
        values = [{"row_id": row.id, "key": _logical_key(row.payload or {})} for row in rows]
        connection.execute(
            audit.update()
            .where(audit.c.id == sa.bindparam("row_id"))
            .values(logical_delivery_key=sa.bindparam("key")),
            values,
        )
        cursor = rows[-1].id


def _triggers() -> None:
    connection = op.get_bind()
    bump = "UPDATE dashboard_revision SET version = version + 1 WHERE id = 1;"
    if connection.dialect.name == "postgresql":
        op.execute(
            "CREATE FUNCTION escalane_bump_dashboard() RETURNS trigger LANGUAGE plpgsql AS $$ "
            f"BEGIN {bump} RETURN NULL; END $$"
        )
        op.execute(
            "CREATE TRIGGER dashboard_alarm_insert_delete AFTER INSERT OR DELETE ON alarms "
            "FOR EACH STATEMENT EXECUTE FUNCTION escalane_bump_dashboard()"
        )
        changed = " OR ".join(
            f"OLD.{field} IS DISTINCT FROM NEW.{field}" for field in _VISIBLE_FIELDS
        )
        op.execute(
            f"CREATE TRIGGER dashboard_alarm_update AFTER UPDATE ON alarms "
            f"FOR EACH ROW WHEN ({changed}) EXECUTE FUNCTION escalane_bump_dashboard()"
        )
        for table, field in (("persons", "display_name"), ("rooms", "label")):
            op.execute(
                f"CREATE TRIGGER dashboard_{table}_update AFTER UPDATE ON {table} "
                f"FOR EACH ROW WHEN (OLD.{field} IS DISTINCT FROM NEW.{field}) "
                "EXECUTE FUNCTION escalane_bump_dashboard()"
            )
    else:
        for action in ("INSERT", "DELETE"):
            op.execute(
                f"CREATE TRIGGER dashboard_alarm_{action.lower()} AFTER {action} ON alarms "
                f"BEGIN {bump} END"
            )
        changed = " OR ".join(f"OLD.{field} IS NOT NEW.{field}" for field in _VISIBLE_FIELDS)
        op.execute(
            f"CREATE TRIGGER dashboard_alarm_update AFTER UPDATE ON alarms WHEN {changed} "
            f"BEGIN {bump} END"
        )
        for table, field in (("persons", "display_name"), ("rooms", "label")):
            op.execute(
                f"CREATE TRIGGER dashboard_{table}_update AFTER UPDATE ON {table} "
                f"WHEN OLD.{field} IS NOT NEW.{field} BEGIN {bump} END"
            )


def upgrade() -> None:
    op.add_column("alarm_notifications", sa.Column("logical_delivery_key", sa.String()))
    _backfill()
    op.create_index(
        "ix_alarm_notifications_logical_delivery_key",
        "alarm_notifications",
        ["logical_delivery_key"],
    )
    op.create_table(
        "dashboard_revision",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("epoch", sa.String(32), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False, server_default="0"),
        sa.CheckConstraint("id = 1", name="dashboard_revision_singleton"),
    )
    op.bulk_insert(
        sa.table(
            "dashboard_revision",
            sa.column("id", sa.Integer()),
            sa.column("epoch", sa.String()),
            sa.column("version", sa.BigInteger()),
        ),
        [{"id": 1, "epoch": uuid.uuid4().hex, "version": 0}],
    )
    _triggers()


def downgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP FUNCTION escalane_bump_dashboard() CASCADE")
    else:
        for name in (
            "alarm_insert",
            "alarm_delete",
            "alarm_update",
            "persons_update",
            "rooms_update",
        ):
            op.execute(f"DROP TRIGGER dashboard_{name}")
    op.drop_table("dashboard_revision")
    op.drop_index("ix_alarm_notifications_logical_delivery_key", table_name="alarm_notifications")
    op.drop_column("alarm_notifications", "logical_delivery_key")
