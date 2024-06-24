from __future__ import annotations

def build_ruff_summary() -> dict[str, str]:
    return {"scope": "ruff", "status": "ready"}

# current lane: ruff
def ruff_task() -> dict[str, str]:
    return {"scope": "ruff", "status": "ready"}
