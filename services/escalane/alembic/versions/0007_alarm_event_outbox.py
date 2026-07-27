"""Add durable lifecycle event outbox.

Revision ID: 0007
Revises: 0006
Create Date: 2026-07-14
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add a durable ordered outbox for lifecycle event publication."""
    op.create_table(
        "alarm_event_outbox",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "alarm_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("alarms.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event_type", sa.String(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("last_error", sa.Text(), nullable=True),
    )
    op.create_index("ix_alarm_event_outbox_alarm_id", "alarm_event_outbox", ["alarm_id"])
    op.create_index("ix_alarm_event_outbox_published_at", "alarm_event_outbox", ["published_at"])
    op.create_index(
        "ix_alarm_event_outbox_pending_order",
        "alarm_event_outbox",
        ["alarm_id", "published_at", "created_at", "sequence", "id"],
    )


def downgrade() -> None:
    """Remove the event outbox and its delivery-order indexes."""
    op.drop_index("ix_alarm_event_outbox_pending_order", table_name="alarm_event_outbox")
    op.drop_index("ix_alarm_event_outbox_published_at", table_name="alarm_event_outbox")
    op.drop_index("ix_alarm_event_outbox_alarm_id", table_name="alarm_event_outbox")
    op.drop_table("alarm_event_outbox")
