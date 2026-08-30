"""Small assertion helpers that keep tests runnable outside pytest's rewrite path."""

from __future__ import annotations

from typing import Any


def expect(condition: Any, message: Any | None = None) -> None:
    """Raise ``AssertionError`` without relying on pytest assertion rewriting."""
    if condition:
        return
    if message is None:
        raise AssertionError("expectation failed")
    raise AssertionError(message)
