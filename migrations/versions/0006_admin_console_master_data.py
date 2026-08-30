"""Add admin-console master-data lifecycle and audit fields.

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-11
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def _add_lifecycle_columns(table_name: str) -> None:
    """Add active/version fields needed for safe operator edits."""
    op.add_column(
        table_name,
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
    )
    op.add_column(
        table_name,
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
    )


def _drop_lifecycle_columns(table_name: str) -> None:
    """Remove lifecycle fields in the reverse order used by downgrade."""
    op.drop_column(table_name, "version")
    op.drop_column(table_name, "active")


def upgrade() -> None:
    """Add optimistic versions, activation state, and administrative audit events."""
    for table_name in ("sites", "rooms", "devices"):
        _add_lifecycle_columns(table_name)

    for table_name in ("persons", "escalation_policy"):
        op.add_column(
            table_name,
            sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        )

    op.create_table(
        "admin_audit_events",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("operator_name", sa.String(), nullable=False),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("resource_type", sa.String(), nullable=False),
        sa.Column("resource_id", sa.String(), nullable=False),
        sa.Column(
            "changed_fields", sa.JSON(), nullable=False, server_default=sa.text("'{}'::json")
        ),
        sa.Column("request_id", sa.String(), nullable=True),
    )
    op.create_index("ix_admin_audit_events_created_at", "admin_audit_events", ["created_at"])


def downgrade() -> None:
    """Remove administrative audit and master-data lifecycle support."""
    op.drop_index("ix_admin_audit_events_created_at", table_name="admin_audit_events")
    op.drop_table("admin_audit_events")

    for table_name in ("escalation_policy", "persons"):
        op.drop_column(table_name, "version")

    for table_name in ("devices", "rooms", "sites"):
        _drop_lifecycle_columns(table_name)
