"""Redis idempotency helpers for inbound alarm triggers."""

from __future__ import annotations

import hashlib
import time


def bucket_10s(now_epoch_seconds: int | None = None) -> int:
    """Return the 10-second time bucket used to collapse rapid duplicate triggers."""
    if now_epoch_seconds is None:
        now_epoch_seconds = int(time.time())
    return now_epoch_seconds // 10


def idempotency_key(token: str, bucket: int) -> str:
    """Hash the device token and bucket into a Redis-safe idempotency key body."""
    raw = f"{token}:{bucket}".encode()
    return hashlib.sha256(raw).hexdigest()
