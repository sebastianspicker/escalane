"""Alarm severity vocabulary shared by intake, notifications, and the web schemas."""

from __future__ import annotations

# Alarm severities, from most to least urgent; new alarms default to the most urgent.
PRIORITY_CRITICAL = "P0"
PRIORITY_HIGH = "P1"
PRIORITY_MEDIUM = "P2"
PRIORITY_LOW = "P3"
PRIORITY_ALL = [PRIORITY_CRITICAL, PRIORITY_HIGH, PRIORITY_MEDIUM, PRIORITY_LOW]
DEFAULT_SEVERITY = PRIORITY_CRITICAL
