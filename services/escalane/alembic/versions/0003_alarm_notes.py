"""Add alarm_notes table for timeline entries.

Revision ID: 0003
Revises: 0002_alarm_lifecycle_fields
Create Date: 2026-02-24

"""

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "0003"
down_revision = "0002_alarm_lifecycle_fields"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add operator timeline notes with alarm-scoped lookup support."""
    # Create alarm_notes table
    op.create_table(
        "alarm_notes",
        sa.Column(
            "id",
            sa.Uuid(),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
            primary_key=True,
        ),
        sa.Column(
            "alarm_id",
            sa.Uuid(),
            sa.ForeignKey("alarms.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("created_by", sa.String(), nullable=True),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column(
            "note_type",
            sa.String(),
            nullable=False,
            server_default="manual",
        ),
    )

    # Create index on alarm_id for faster lookups
    op.create_index(
        "ix_alarm_notes_alarm_id",
        "alarm_notes",
        ["alarm_id"],
    )


def downgrade() -> None:
    """Remove the alarm-note index and table."""
    op.drop_index("ix_alarm_notes_alarm_id", table_name="alarm_notes")
    op.drop_table("alarm_notes")
