"""Reusable constants for the alarm-broker project."""

# Alarm priorities
PRIORITY_CRITICAL = "P0"
PRIORITY_HIGH = "P1"
PRIORITY_MEDIUM = "P2"
PRIORITY_LOW = "P3"

PRIORITY_ALL = [PRIORITY_CRITICAL, PRIORITY_HIGH, PRIORITY_MEDIUM, PRIORITY_LOW]

# Notification Tags
TAG_EMERGENCY = "notfall"
TAG_SILENT = "silent"

# API defaults
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200

# Alarm defaults
DEFAULT_SEVERITY = PRIORITY_CRITICAL

# Event types
EVENT_ALARM_CREATED = "alarm.created"
EVENT_ALARM_ACKNOWLEDGED = "alarm.acknowledged"
EVENT_ALARM_RESOLVED = "alarm.resolved"
EVENT_ALARM_CANCELLED = "alarm.cancelled"
EVENT_ALARM_STATE_CHANGED = "alarm.state_changed"
