"""Add performance indexes for dashboard queries, filtering, and metrics.

Revision ID: 0005
Revises: 0004
Create Date: 2026-03-22

"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add indexes for the dashboard, filters, notification lookups, and metrics."""
    op.create_index("ix_alarms_status", "alarms", ["status"])
    # ix_alarms_created_at intentionally omitted: created_at is already indexed
    # as idx_alarms_created_at from migration 0001.
    op.create_index("ix_alarms_severity", "alarms", ["severity"])
    op.create_index("ix_alarm_notifications_alarm_id", "alarm_notifications", ["alarm_id"])
    op.create_index("ix_alarm_notifications_channel", "alarm_notifications", ["channel"])


def downgrade() -> None:
    """Remove only the performance indexes introduced by this revision."""
    op.drop_index("ix_alarm_notifications_channel", table_name="alarm_notifications")
    op.drop_index("ix_alarm_notifications_alarm_id", table_name="alarm_notifications")
    op.drop_index("ix_alarms_severity", table_name="alarms")
    op.drop_index("ix_alarms_status", table_name="alarms")
