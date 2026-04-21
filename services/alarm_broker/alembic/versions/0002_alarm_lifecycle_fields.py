"""add lifecycle fields to alarms

Revision ID: 0002_alarm_lifecycle_fields
Revises: 0001_initial_schema
Create Date: 2026-02-24
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0002_alarm_lifecycle_fields"
down_revision = "0001_initial_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("alarms", sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("alarms", sa.Column("resolved_by", sa.String(), nullable=True))
    op.add_column("alarms", sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("alarms", sa.Column("cancelled_by", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("alarms", "cancelled_by")
    op.drop_column("alarms", "cancelled_at")
    op.drop_column("alarms", "resolved_by")
    op.drop_column("alarms", "resolved_at")
