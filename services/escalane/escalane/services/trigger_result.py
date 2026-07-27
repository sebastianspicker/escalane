"""Value object returned by the alarm trigger service."""

from __future__ import annotations

import uuid

from escalane.db.models import AlarmStatus


class TriggerResult:
    """Outcome of a trigger operation."""

    def __init__(
        self,
        *,
        success: bool = True,
        alarm_id: uuid.UUID | None = None,
        status: AlarmStatus | None = None,
        is_duplicate: bool = False,
        error_code: int | None = None,
        error_message: str | None = None,
    ) -> None:
        """Store either a successful alarm outcome or a stable failure contract."""
        self.success = success
        self.alarm_id = alarm_id
        self.status = status
        self.is_duplicate = is_duplicate
        self.error_code = error_code
        self.error_message = error_message

    @classmethod
    def ok(
        cls, alarm_id: uuid.UUID, status: AlarmStatus, is_duplicate: bool = False
    ) -> TriggerResult:
        """Create a successful result, including duplicate-trigger state."""
        return cls(success=True, alarm_id=alarm_id, status=status, is_duplicate=is_duplicate)

    @classmethod
    def error(cls, code: int, message: str) -> TriggerResult:
        """Create a failed result without raising across the ingress boundary."""
        return cls(success=False, error_code=code, error_message=message)
