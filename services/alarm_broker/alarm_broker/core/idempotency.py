from __future__ import annotations

import hashlib
import time


def bucket_10s(now_epoch_seconds: int | None = None) -> int:
    if now_epoch_seconds is None:
        now_epoch_seconds = int(time.time())
    return now_epoch_seconds // 10


def idempotency_key(token: str, bucket: int) -> str:
    raw = f"{token}:{bucket}".encode()
    return hashlib.sha256(raw).hexdigest()
