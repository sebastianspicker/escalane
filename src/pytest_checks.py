from __future__ import annotations

def build_pytest_summary() -> dict[str, str]:
    return {"scope": "pytest", "status": "ready"}

# current lane: pytest
def pytest_task() -> dict[str, str]:
    return {"scope": "pytest", "status": "ready"}
