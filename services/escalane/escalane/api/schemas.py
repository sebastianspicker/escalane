"""Define validated API request and response payloads."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, model_validator

from escalane.constants import PRIORITY_ALL
from escalane.db.models import AlarmStatus


# Return the durable alarm identity and state created by a trigger.
class TriggerResponse(BaseModel):
    ok: bool = True
    alarm_id: uuid.UUID
    status: AlarmStatus


# Serialize alarm lifecycle, enrichment, and operational metadata for admin APIs.
class AlarmOut(BaseModel):
    id: uuid.UUID
    status: AlarmStatus
    source: str
    event: str
    created_at: datetime
    person_id: str | None
    room_id: str | None
    site_id: str | None
    device_id: str | None
    severity: str
    silent: bool
    zammad_ticket_id: int | None
    acked_at: datetime | None
    acked_by: str | None
    resolved_at: datetime | None
    resolved_by: str | None
    cancelled_at: datetime | None
    cancelled_by: str | None
    meta: dict[str, Any] = Field(default_factory=dict)


class AlarmDetailOut(AlarmOut):
    """Extended alarm output that includes the ack_token for privileged endpoints."""

    ack_token: str | None


# Validate optional responder identity and acknowledgement note.
class AckIn(BaseModel):
    acked_by: str | None = Field(default=None, max_length=120)
    note: str | None = Field(default=None, max_length=2000)


# Validate the actor and note attached to a lifecycle transition.
class TransitionIn(BaseModel):
    actor: str | None = Field(default=None, max_length=120)
    note: str | None = Field(default=None, max_length=2000)


# Validate device registration and optional person/room associations.
class DeviceUpsertIn(BaseModel):
    id: str | None = None
    vendor: str = "yealink"
    model_family: str = "T5"
    mac: str | None = None
    account_ext: str | None = None
    device_token: str
    person_id: str | None = None
    room_id: str | None = None


# Describe one addressable escalation destination.
class TargetIn(BaseModel):
    id: str
    label: str
    channel: Literal["email", "sms", "signal", "webhook"]
    address: str
    enabled: bool = True


# Describe one escalation delay and its target set.
class StepIn(BaseModel):
    step_no: int = Field(ge=0)
    after_seconds: int = Field(ge=0)
    target_ids: list[str] = Field(min_length=1)


# Validate a complete versioned escalation policy replacement.
class EscalationPolicyIn(BaseModel):
    policy_id: str = "default"
    name: str = "Default"
    targets: list[TargetIn] = Field(default_factory=list)
    steps: list[StepIn] = Field(default_factory=list)


class AlarmNoteIn(BaseModel):
    """Input schema for creating an alarm note."""

    note: str = Field(..., min_length=1, max_length=5000)
    created_by: str | None = Field(default=None, max_length=120)


class AlarmNoteOut(BaseModel):
    """Output schema for alarm notes."""

    id: uuid.UUID
    alarm_id: uuid.UUID
    created_at: datetime
    created_by: str | None
    note: str
    note_type: str

    model_config = {"from_attributes": True}


# Validate a bounded set of alarms for bulk acknowledgement.
class BulkAckIn(BaseModel):
    alarm_ids: list[uuid.UUID] = Field(min_length=1, max_length=500)
    acked_by: str | None = Field(default=None, max_length=120)
    note: str | None = Field(default=None, max_length=2000)


# Validate a bounded set of alarms for a bulk state transition.
class BulkTransitionIn(BaseModel):
    alarm_ids: list[uuid.UUID] = Field(min_length=1, max_length=500)
    actor: str | None = Field(default=None, max_length=120)
    note: str | None = Field(default=None, max_length=2000)


# Summarize changed, unchanged, and missing alarms after a bulk command.
class BulkOperationOut(BaseModel):
    requested: int
    changed: int
    unchanged: int
    missing: list[uuid.UUID] = Field(default_factory=list)


class ExportFormat(StrEnum):
    """Supported export formats."""

    JSON = "json"
    CSV = "csv"


class AlarmFilterIn(BaseModel):
    """Common optional filters for list and export alarm requests."""

    status: AlarmStatus | None = None
    severity: str | None = None
    person_id: str | None = None
    room_id: str | None = None
    site_id: str | None = None
    device_id: str | None = None
    source: str | None = None
    created_after: datetime | None = None
    created_before: datetime | None = None


class AlarmExportIn(AlarmFilterIn):
    """Filter options for alarm export."""

    format: ExportFormat = ExportFormat.JSON
    limit: int = Field(default=1000, ge=1, le=10000)


class AlarmPatchSchema(BaseModel):
    """Schema for partial alarm updates."""

    title: str | None = Field(default=None, max_length=500)
    description: str | None = Field(default=None, max_length=5000)
    severity: str | None = None
    tags: list[Annotated[str, Field(max_length=100)]] | None = Field(default=None, max_length=20)

    @model_validator(mode="after")
    def validate_fields(self):
        """Reject priorities outside the canonical application priority set."""
        if self.severity and self.severity not in PRIORITY_ALL:
            raise ValueError(f"Invalid severity. Must be one of: {PRIORITY_ALL}")
        return self
