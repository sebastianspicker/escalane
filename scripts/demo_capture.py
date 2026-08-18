"""Compatibility entry point for deterministic Mock University demo captures."""

from __future__ import annotations

if __package__:
    from scripts import demo_capture_runtime as _runtime
else:  # pragma: no cover - direct script execution fallback
    import demo_capture_runtime as _runtime

SHOT_FILENAMES = _runtime.SHOT_FILENAMES
DEMO_TOKENS = _runtime.DEMO_TOKENS
DemoCaptureError = _runtime.DemoCaptureError
CaptureConfig = _runtime.CaptureConfig
run_capture = _runtime.run_capture
main = _runtime.main


def __getattr__(name: str) -> object:
    """Delegate legacy implementation lookups to the runtime module."""
    return getattr(_runtime, name)


if __name__ == "__main__":
    raise SystemExit(main())
