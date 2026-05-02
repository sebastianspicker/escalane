"""Redis key helpers for fixed-window request rate limiting."""

from __future__ import annotations

import hashlib
import time


def minute_bucket(now_epoch_seconds: int | None = None) -> int:
    """Return the current minute bucket for fixed-window counters."""
    if now_epoch_seconds is None:
        now_epoch_seconds = int(time.time())
    return now_epoch_seconds // 60


def rate_limit_key(token: str, bucket: int) -> str:
    """Hash the sensitive token/IP portion before storing the counter key."""
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    return f"rl:{token_hash}:{bucket}"
