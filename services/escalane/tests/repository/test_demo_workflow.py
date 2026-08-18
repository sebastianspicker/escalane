"""Offline tests for the documented demo preparation and capture scripts."""

from __future__ import annotations

try:
    from tests.assertions import expect
except ModuleNotFoundError:
    from assertions import expect

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[4]
pytestmark = pytest.mark.repository
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import capture_screenshots_isolated, demo_capture_runtime  # noqa: E402
from scripts.demo_capture import (  # noqa: E402
    SHOT_FILENAMES,
    CaptureConfig,
    DemoCaptureError,
    _http_json,
    run_capture,
)
from scripts.demo_prepare import (  # noqa: E402
    DemoPrepareError,
    HttpResult,
    _request_json,
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
            expect(body is None)
            return HttpResult(200, '{"ok":"true"}', {"ok": "true"})
        if url.endswith("/v1/admin/seed"):
            expect(headers["X-Admin-Key"] == "dev-admin-key")
            expect(headers["Content-Type"] == "application/x-yaml")
            expect(body == seed_file.read_bytes())
            return HttpResult(200, '{"ok":"true"}', {"ok": "true"})
        if url.endswith("/v1/simulation/notifications/clear"):
            expect(headers["X-Admin-Key"] == "dev-admin-key")
            expect(body == b"{}")
            return HttpResult(200, '{"status":"ok"}', {"status": "ok"})
        raise AssertionError(f"Unexpected URL in test: {url}")

    result = run_prepare(
        base_url="http://localhost:8080/",
        admin_key="dev-admin-key",
        seed_file=seed_file,
        timeout_seconds=5.0,
        request_func=fake_request,
    )

    expect(result["base_url"] == "http://localhost:8080")
    expect(result["ready_status"] == 200)
    expect(result["seed_status"] == 200)
    expect(result["clear_status"] == 200)
    expect([method for method, _ in calls] == ["GET", "POST", "POST"])


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
    expect("SIMULATION_ENABLED=true" in str(exc.value))


@pytest.mark.unit
def test_demo_prepare_rejects_non_http_url() -> None:
    with pytest.raises(DemoPrepareError):
        _request_json("GET", "file:///etc/passwd")


@pytest.mark.unit
def test_resolve_admin_key_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADMIN_API_KEY", "env-admin")
    expect(_resolve_admin_key(None) == "env-admin")


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

    expect([path.name for path in created] == SHOT_FILENAMES)
    for path in created:
        expect(path.exists())
        expect(path.stat().st_size > 0)


@pytest.mark.unit
def test_demo_capture_requires_admin_key() -> None:
    with pytest.raises(DemoCaptureError):
        from scripts.demo_capture import _resolve_admin_key

        _resolve_admin_key("")


@pytest.mark.unit
def test_demo_capture_rejects_non_http_url() -> None:
    with pytest.raises(DemoCaptureError):
        _http_json("GET", "file:///etc/passwd")


@pytest.mark.unit
def test_demo_capture_transitions_preserve_lifecycle_api_mapping() -> None:
    ack = demo_capture_runtime._ACK_TRANSITION
    resolve = demo_capture_runtime._RESOLVE_TRANSITION

    expect(ack.action == "ack")
    expect(
        ack.payload()
        == {
            "acked_by": "Demo Operator",
            "note": "Screenshot flow acknowledgment",
        }
    )
    expect(resolve.action == "resolve")
    expect(
        resolve.payload()
        == {
            "actor": "Demo Script",
            "note": "Screenshot flow resolve",
        }
    )


@pytest.mark.unit
def test_demo_capture_transition_uses_value_object_for_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, dict[str, str] | None, bytes | None, float]] = []

    def fake_http_json(
        method: str,
        url: str,
        headers: dict[str, str] | None = None,
        body: bytes | None = None,
        timeout: float = 10.0,
    ) -> HttpResult:
        calls.append((method, url, headers, body, timeout))
        return HttpResult(204, "", None)

    monkeypatch.setattr(demo_capture_runtime, "_http_json", fake_http_json)
    demo_capture_runtime._transition_alarm(
        "http://demo.test",
        "demo-key",
        "alarm-42",
        2.5,
        demo_capture_runtime._ACK_TRANSITION,
    )

    expect(
        calls
        == [
            (
                "POST",
                "http://demo.test/v1/alarms/alarm-42/ack",
                {"X-Admin-Key": "demo-key", "Content-Type": "application/json"},
                b'{"acked_by": "Demo Operator", "note": "Screenshot flow acknowledgment"}',
                2.5,
            )
        ]
    )


@pytest.mark.unit
def test_isolated_capture_worker_context_uses_default_tls_verification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    class FakeAsyncClient:
        def __init__(self, *args: object, **kwargs: object) -> None:
            calls.append((args, kwargs))

    monkeypatch.setattr(capture_screenshots_isolated.httpx, "AsyncClient", FakeAsyncClient)
    context = capture_screenshots_isolated._worker_context(object(), object(), object())

    expect(isinstance(context["http"], FakeAsyncClient))
    expect(calls == [((), {})])
