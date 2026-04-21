"""Add deleted_at and deleted_by fields to alarms table for soft-delete.

Revision ID: 0004
Revises: 0003
Create Date: 2026-02-27

"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add deleted_at column
    op.add_column(
        "alarms",
        sa.Column(
            "deleted_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )

    # Add deleted_by column
    op.add_column(
        "alarms",
        sa.Column(
            "deleted_by",
            sa.String(),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("alarms", "deleted_by")
    op.drop_column("alarms", "deleted_at")
