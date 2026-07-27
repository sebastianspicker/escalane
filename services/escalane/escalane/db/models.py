"""SQLAlchemy models for alarm state, master data, escalation, and audit logs."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String, Text, func
from sqlalchemy.ext.mutable import MutableDict
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql.sqltypes import JSON
from sqlalchemy.types import Uuid

from escalane.db.base import Base


# Enumerate the only persisted alarm lifecycle states.
class AlarmStatus(enum.StrEnum):
    TRIGGERED = "triggered"
    ACKNOWLEDGED = "acknowledged"
    RESOLVED = "resolved"
    CANCELLED = "cancelled"


# Represent a top-level physical site containing rooms.
class Site(Base):
    __tablename__ = "sites"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")

    rooms: Mapped[list[Room]] = relationship(back_populates="site")


# Represent an addressable room within a site.
class VersionedResourceMixin:
    """Columns shared by mutable master-data resources."""

    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")


class Room(VersionedResourceMixin, Base):
    __tablename__ = "rooms"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    site_id: Mapped[str] = mapped_column(ForeignKey("sites.id"), nullable=False)
    label: Mapped[str] = mapped_column(String, nullable=False)
    floor: Mapped[str | None] = mapped_column(String, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    site: Mapped[Site] = relationship(back_populates="rooms")


# Represent a person associated with devices and alarm context.
class Person(VersionedResourceMixin, Base):
    __tablename__ = "persons"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    display_name: Mapped[str] = mapped_column(String, nullable=False)
    role: Mapped[str | None] = mapped_column(String, nullable=True)
    phone_mobile: Mapped[str | None] = mapped_column(String, nullable=True)
    phone_ext: Mapped[str | None] = mapped_column(String, nullable=True)


# Represent a trigger-capable device and its enrichment relationships.
class Device(VersionedResourceMixin, Base):
    __tablename__ = "devices"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    vendor: Mapped[str] = mapped_column(
        String, nullable=False, default="yealink", server_default="yealink"
    )
    model_family: Mapped[str] = mapped_column(
        String, nullable=False, default="T5", server_default="T5"
    )
    mac: Mapped[str | None] = mapped_column(String, nullable=True)
    account_ext: Mapped[str | None] = mapped_column(String, nullable=True)
    device_token: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    person_id: Mapped[str | None] = mapped_column(ForeignKey("persons.id"), nullable=True)
    room_id: Mapped[str | None] = mapped_column(ForeignKey("rooms.id"), nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    person: Mapped[Person | None] = relationship()
    room: Mapped[Room | None] = relationship()


# Represent one enabled or disabled notification destination.
class EscalationTarget(Base):
    __tablename__ = "escalation_targets"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    label: Mapped[str] = mapped_column(String, nullable=False)
    channel: Mapped[str] = mapped_column(String, nullable=False)  # sms|signal|email|...
    address: Mapped[str] = mapped_column(String, nullable=False)
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )


# Represent the versioned root of an escalation schedule.
class EscalationPolicy(Base):
    __tablename__ = "escalation_policy"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")


class AdminAuditEvent(Base):
    """Immutable, redacted record of an administrative master-data action."""

    __tablename__ = "admin_audit_events"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )
    operator_name: Mapped[str] = mapped_column(String, nullable=False)
    action: Mapped[str] = mapped_column(String, nullable=False)
    resource_type: Mapped[str] = mapped_column(String, nullable=False)
    resource_id: Mapped[str] = mapped_column(String, nullable=False)
    changed_fields: Mapped[dict[str, Any]] = mapped_column(
        MutableDict.as_mutable(JSON), nullable=False, default=dict
    )
    request_id: Mapped[str | None] = mapped_column(String, nullable=True)


# Map a policy step and delay to one target.
class EscalationStep(Base):
    __tablename__ = "escalation_steps"

    policy_id: Mapped[str] = mapped_column(ForeignKey("escalation_policy.id"), primary_key=True)
    step_no: Mapped[int] = mapped_column(Integer, primary_key=True)
    after_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    target_id: Mapped[str] = mapped_column(ForeignKey("escalation_targets.id"), primary_key=True)

    target: Mapped[EscalationTarget] = relationship()


class Alarm(Base):
    """Persisted alarm lifecycle row.

    `meta` holds operational metadata such as request IDs, idempotency details,
    and event-delivery recovery state. Long-lived operator notes belong in
    `AlarmNote`; notification attempts belong in `AlarmNotification`.
    """

    __tablename__ = "alarms"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    status: Mapped[AlarmStatus] = mapped_column(
        Enum(
            AlarmStatus,
            name="alarm_status",
            values_callable=lambda x: [e.value for e in x],
        ),
        nullable=False,
        default=AlarmStatus.TRIGGERED,
        server_default=AlarmStatus.TRIGGERED.value,
        index=True,
    )
    source: Mapped[str] = mapped_column(String, nullable=False)
    event: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )

    person_id: Mapped[str | None] = mapped_column(ForeignKey("persons.id"), nullable=True)
    room_id: Mapped[str | None] = mapped_column(ForeignKey("rooms.id"), nullable=True)
    site_id: Mapped[str | None] = mapped_column(ForeignKey("sites.id"), nullable=True)
    device_id: Mapped[str | None] = mapped_column(ForeignKey("devices.id"), nullable=True)

    severity: Mapped[str] = mapped_column(
        String, nullable=False, default="P0", server_default="P0", index=True
    )
    silent: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )

    zammad_ticket_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ack_token: Mapped[str | None] = mapped_column(String, unique=True, nullable=True)
    acked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    acked_by: Mapped[str | None] = mapped_column(String, nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_by: Mapped[str | None] = mapped_column(String, nullable=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    cancelled_by: Mapped[str | None] = mapped_column(String, nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_by: Mapped[str | None] = mapped_column(String, nullable=True)
    meta: Mapped[dict[str, Any]] = mapped_column(
        MutableDict.as_mutable(JSON), nullable=False, default=dict
    )


class AlarmRecordMixin:
    """Durable records that belong to an alarm and retain creation order."""

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    alarm_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("alarms.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class AlarmEventOutbox(AlarmRecordMixin, Base):
    """Durable lifecycle event awaiting publication to the worker queue."""

    __tablename__ = "alarm_event_outbox"

    event_type: Mapped[str] = mapped_column(String, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(
        MutableDict.as_mutable(JSON), nullable=False, default=dict
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    published_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


# Record one durable notification attempt and its outcome.
class AlarmNotification(AlarmRecordMixin, Base):
    __tablename__ = "alarm_notifications"

    channel: Mapped[str] = mapped_column(String, nullable=False, index=True)
    target_id: Mapped[str | None] = mapped_column(
        ForeignKey("escalation_targets.id"), nullable=True
    )
    payload: Mapped[dict[str, Any]] = mapped_column(
        MutableDict.as_mutable(JSON), nullable=False, default=dict
    )
    result: Mapped[str | None] = mapped_column(
        String, nullable=True
    )  # ok|error|permanent_error|timeout|skipped
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class AlarmNote(AlarmRecordMixin, Base):
    """Timeline notes for alarms.

    Allows adding notes to an alarm without changing its status.
    Useful for tracking response actions, communications, etc.
    """

    __tablename__ = "alarm_notes"

    created_by: Mapped[str | None] = mapped_column(String, nullable=True)
    note: Mapped[str] = mapped_column(Text, nullable=False)
    note_type: Mapped[str] = mapped_column(
        String, nullable=False, default="manual", server_default="manual"
    )  # manual, system, escalation
