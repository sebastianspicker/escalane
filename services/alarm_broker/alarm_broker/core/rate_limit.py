from __future__ import annotations

import hashlib
import time


def minute_bucket(now_epoch_seconds: int | None = None) -> int:
    if now_epoch_seconds is None:
        now_epoch_seconds = int(time.time())
    return now_epoch_seconds // 60


def rate_limit_key(token: str, bucket: int) -> str:
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    return f"rl:{token_hash}:{bucket}"
