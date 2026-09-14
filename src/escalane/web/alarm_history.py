"""Bounded, stable keyset reads for the operator alarm timeline."""

from __future__ import annotations

import base64
import binascii
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy import and_, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from escalane.persistence.models import AlarmNote, AlarmNotification

HISTORY_LIMIT = 50


@dataclass(frozen=True, order=True)
class HistoryKey:
    """A total order across independent history tables, without offset drift."""

    at: datetime
    kind: str
    id: uuid.UUID

    def encode(self) -> str:
        payload = json.dumps([self.at.isoformat(), self.kind, str(self.id)]).encode()
        return base64.urlsafe_b64encode(payload).decode().rstrip("=")


def parse_history_cursor(value: str | None) -> HistoryKey | None:
    """Reject malformed cursors before issuing history reads."""
    if value is None:
        return None
    try:
        if not value or len(value) > 300:
            raise ValueError
        parts = json.loads(
            base64.b64decode(value + "=" * (-len(value) % 4), altchars=b"-_", validate=True)
        )
        if (
            not isinstance(parts, list)
            or len(parts) != 3
            or not all(isinstance(p, str) for p in parts)
        ):
            raise ValueError
        at, kind, identifier = parts
        timestamp = datetime.fromisoformat(at)
        if timestamp.tzinfo is None or kind not in {"note", "notification"}:
            raise ValueError
        return HistoryKey(timestamp.astimezone(UTC), kind, uuid.UUID(identifier))
    except (ValueError, TypeError, OverflowError, binascii.Error) as exc:
        raise HTTPException(status_code=422, detail="invalid_history_cursor") from exc


def _utc(value: datetime) -> datetime:
    """SQLite returns naive UTC while PostgreSQL preserves timezone metadata."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


async def _source_events(
    session: AsyncSession, alarm_id: uuid.UUID, kind: str, before: HistoryKey | None
) -> list[tuple[HistoryKey, dict[str, str]]]:
    model: Any = AlarmNote if kind == "note" else AlarmNotification
    fields = (model.created_by, model.note) if kind == "note" else (model.channel, model.result)
    query = select(model.id, model.created_at, *fields).where(model.alarm_id == alarm_id)
    if before is not None:
        tie = model.id < before.id if kind == before.kind else literal(kind < before.kind)
        query = query.where(
            or_(model.created_at < before.at, and_(model.created_at == before.at, tie))
        )
    rows = (
        await session.execute(
            query.order_by(model.created_at.desc(), model.id.desc()).limit(HISTORY_LIMIT + 1)
        )
    ).all()
    result = []
    for identifier, at, actor, description in rows:
        key = HistoryKey(_utc(at), kind, identifier)
        text = (
            f"{actor or 'System'}: {description}"
            if kind == "note"
            else f"{actor}: {description or 'pending'}"
        )
        result.append(
            (
                key,
                {
                    "at": at.isoformat(timespec="minutes"),
                    "at_iso": at.isoformat(),
                    "description": text,
                    "key": f"{kind}:{identifier}",
                },
            )
        )
    return result


async def history_page(
    session: AsyncSession, alarm_id: uuid.UUID, before: HistoryKey | None
) -> tuple[list[dict[str, str]], str | None, bool]:
    """Return at most 50 events and reserve creation for the earliest page.

    Creation is an implicit earliest sentinel, even for imported records whose
    timestamps precede the alarm. It occupies one slot and is never a cursor.
    Each source reads at most 51 projected rows; payloads are never hydrated.
    """
    rows = await _source_events(session, alarm_id, "note", before)
    rows.extend(await _source_events(session, alarm_id, "notification", before))
    rows.sort(key=lambda item: item[0], reverse=True)
    selected = rows[:HISTORY_LIMIT]
    include_creation = len(rows) < HISTORY_LIMIT
    cursor = selected[-1][0].encode() if selected and not include_creation else None
    return [event for _, event in reversed(selected)], cursor, include_creation
