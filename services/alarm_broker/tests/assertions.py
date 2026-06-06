from __future__ import annotations

from typing import Any


def expect(condition: Any, message: Any | None = None) -> None:
    if condition:
        return
    if message is None:
        raise AssertionError("expectation failed")
    raise AssertionError(message)
