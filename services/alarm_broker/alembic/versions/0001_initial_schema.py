"""initial schema

Revision ID: 0001_initial_schema
Revises:
Create Date: 2026-02-24
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sites",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
    )

    op.create_table(
        "rooms",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("site_id", sa.String(), sa.ForeignKey("sites.id"), nullable=False),
        sa.Column("label", sa.String(), nullable=False),
        sa.Column("floor", sa.String(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
    )

    op.create_table(
        "persons",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("display_name", sa.String(), nullable=False),
        sa.Column("role", sa.String(), nullable=True),
        sa.Column("phone_mobile", sa.String(), nullable=True),
        sa.Column("phone_ext", sa.String(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )

    op.create_table(
        "devices",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("vendor", sa.String(), nullable=False, server_default=sa.text("'yealink'")),
        sa.Column("model_family", sa.String(), nullable=False, server_default=sa.text("'T5'")),
        sa.Column("mac", sa.String(), nullable=True),
        sa.Column("account_ext", sa.String(), nullable=True),
        sa.Column("device_token", sa.String(), nullable=False, unique=True),
        sa.Column("person_id", sa.String(), sa.ForeignKey("persons.id"), nullable=True),
        sa.Column("room_id", sa.String(), sa.ForeignKey("rooms.id"), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("idx_devices_token", "devices", ["device_token"])

    op.create_table(
        "escalation_targets",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("label", sa.String(), nullable=False),
        sa.Column("channel", sa.String(), nullable=False),
        sa.Column("address", sa.String(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )

    op.create_table(
        "escalation_policy",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
    )

    op.create_table(
        "escalation_steps",
        sa.Column(
            "policy_id", sa.String(), sa.ForeignKey("escalation_policy.id"), primary_key=True
        ),
        sa.Column("step_no", sa.Integer(), primary_key=True),
        sa.Column("after_seconds", sa.Integer(), nullable=False),
        sa.Column(
            "target_id", sa.String(), sa.ForeignKey("escalation_targets.id"), primary_key=True
        ),
    )

    op.create_table(
        "alarms",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "status",
            sa.Enum("triggered", "acknowledged", "resolved", "cancelled", name="alarm_status"),
            nullable=False,
            server_default=sa.text("'triggered'"),
        ),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("event", sa.String(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("person_id", sa.String(), sa.ForeignKey("persons.id"), nullable=True),
        sa.Column("room_id", sa.String(), sa.ForeignKey("rooms.id"), nullable=True),
        sa.Column("site_id", sa.String(), sa.ForeignKey("sites.id"), nullable=True),
        sa.Column("device_id", sa.String(), sa.ForeignKey("devices.id"), nullable=True),
        sa.Column("severity", sa.String(), nullable=False, server_default=sa.text("'P0'")),
        sa.Column("silent", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("zammad_ticket_id", sa.Integer(), nullable=True),
        sa.Column("ack_token", sa.String(), unique=True, nullable=True),
        sa.Column("acked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acked_by", sa.String(), nullable=True),
        sa.Column("meta", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
    )
    op.create_index("idx_alarms_created_at", "alarms", ["created_at"])

    op.create_table(
        "alarm_notifications",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "alarm_id",
            sa.Uuid(),
            sa.ForeignKey("alarms.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("channel", sa.String(), nullable=False),
        sa.Column("target_id", sa.String(), sa.ForeignKey("escalation_targets.id"), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")),
        sa.Column("result", sa.String(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("alarm_notifications")
    op.drop_index("idx_alarms_created_at", table_name="alarms")
    op.drop_table("alarms")
    op.drop_table("escalation_steps")
    op.drop_table("escalation_policy")
    op.drop_table("escalation_targets")
    op.drop_index("idx_devices_token", table_name="devices")
    op.drop_table("devices")
    op.drop_table("persons")
    op.drop_table("rooms")
    op.drop_table("sites")
    op.execute("DROP TYPE IF EXISTS alarm_status")
