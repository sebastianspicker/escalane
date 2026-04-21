from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.demo_capture import (  # noqa: E402
    SHOT_FILENAMES,
    CaptureConfig,
    DemoCaptureError,
    run_capture,
)
from scripts.demo_prepare import (  # noqa: E402
    DemoPrepareError,
    HttpResult,
    _resolve_admin_key,
    run_prepare,
)


@pytest.mark.unit
def test_demo_prepare_success_sequence(tmp_path: Path) -> None:
    seed_file = tmp_path / "simulation_seed.yaml"
    seed_file.write_text("sites: []\n", encoding="utf-8")
    calls: list[tuple[str, str]] = []

    def fake_request(
        method: str,
        url: str,
        headers: dict[str, str],
        body: bytes | None,
        timeout: float,  # noqa: ARG001
    ) -> HttpResult:
        calls.append((method, url))
        if url.endswith("/readyz"):
            assert body is None
            return HttpResult(200, '{"ok":"true"}', {"ok": "true"})
        if url.endswith("/v1/admin/seed"):
            assert headers["X-Admin-Key"] == "dev-admin-key"
            assert headers["Content-Type"] == "application/x-yaml"
            assert body == seed_file.read_bytes()
            return HttpResult(200, '{"ok":"true"}', {"ok": "true"})
        if url.endswith("/v1/simulation/notifications/clear"):
            assert headers["X-Admin-Key"] == "dev-admin-key"
            assert body == b"{}"
            return HttpResult(200, '{"status":"ok"}', {"status": "ok"})
        raise AssertionError(f"Unexpected URL in test: {url}")

    result = run_prepare(
        base_url="http://localhost:8080/",
        admin_key="dev-admin-key",
        seed_file=seed_file,
        timeout_seconds=5.0,
        request_func=fake_request,
    )

    assert result["base_url"] == "http://localhost:8080"
    assert result["ready_status"] == 200
    assert result["seed_status"] == 200
    assert result["clear_status"] == 200
    assert [method for method, _ in calls] == ["GET", "POST", "POST"]


@pytest.mark.unit
def test_demo_prepare_handles_simulation_disabled(tmp_path: Path) -> None:
    seed_file = tmp_path / "simulation_seed.yaml"
    seed_file.write_text("sites: []\n", encoding="utf-8")

    def fake_request(
        method: str,  # noqa: ARG001
        url: str,
        headers: dict[str, str],  # noqa: ARG001
        body: bytes | None,  # noqa: ARG001
        timeout: float,  # noqa: ARG001
    ) -> HttpResult:
        if url.endswith("/readyz"):
            return HttpResult(200, '{"ok":"true"}', {"ok": "true"})
        if url.endswith("/v1/admin/seed"):
            return HttpResult(200, '{"ok":"true"}', {"ok": "true"})
        if url.endswith("/v1/simulation/notifications/clear"):
            return HttpResult(404, '{"detail":"Simulation endpoint not found"}', None)
        raise AssertionError(f"Unexpected URL in test: {url}")

    with pytest.raises(DemoPrepareError) as exc:
        run_prepare(
            base_url="http://localhost:8080",
            admin_key="dev-admin-key",
            seed_file=seed_file,
            timeout_seconds=5.0,
            request_func=fake_request,
        )
    assert "SIMULATION_ENABLED=true" in str(exc.value)


@pytest.mark.unit
def test_resolve_admin_key_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADMIN_API_KEY", "env-admin")
    assert _resolve_admin_key(None) == "env-admin"


@pytest.mark.unit
def test_resolve_admin_key_missing_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ADMIN_API_KEY", raising=False)
    with pytest.raises(DemoPrepareError):
        _resolve_admin_key(None)


@pytest.mark.unit
def test_demo_capture_mock_mode_creates_expected_files(tmp_path: Path) -> None:
    config = CaptureConfig(
        base_url="http://localhost:8080",
        admin_key="dev-admin-key",
        output_dir=tmp_path,
        seed_file=tmp_path / "simulation_seed.yaml",
        timeout_seconds=5.0,
        wait_seconds=5.0,
        headless=True,
        skip_prepare=True,
        mock_screens=True,
    )

    created = run_capture(config)

    assert [path.name for path in created] == SHOT_FILENAMES
    for path in created:
        assert path.exists()
        assert path.stat().st_size > 0


@pytest.mark.unit
def test_demo_capture_requires_admin_key() -> None:
    with pytest.raises(DemoCaptureError):
        from scripts.demo_capture import _resolve_admin_key

        _resolve_admin_key("")
