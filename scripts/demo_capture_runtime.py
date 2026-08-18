"""Capture deterministic local demo screenshots for Mock University."""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib import parse

try:
    from scripts.demo_prepare import (
        DemoPrepareError,
        HttpResult,
        _request_json,
        run_prepare,
    )
except ModuleNotFoundError:  # pragma: no cover - direct script execution fallback
    from demo_prepare import DemoPrepareError, HttpResult, _request_json, run_prepare

SHOT_FILENAMES: list[str] = [
    "01-admin-overview.png",
    "02-admin-triggered-alarm.png",
    "03-admin-search-filter.png",
    "04-admin-alarm-detail.png",
    "05-admin-quick-acknowledged.png",
    "06-ack-page-triggered-mobile.png",
    "07-ack-page-acknowledged-mobile.png",
    "08-admin-resolved-state.png",
    "09-simulation-feed.png",
    "10-simulation-feed-after-clear.png",
]

DEMO_TOKENS: dict[str, str] = {
    "primary": "demo2",
    "secondary": "demo4",
}

_ONE_PIXEL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAusB9Qn7B8gAAAAASUVORK5CYII="
)


class DemoCaptureError(RuntimeError):
    """Typed error for demo capture failures."""


@dataclass(frozen=True)
class CaptureConfig:
    """Collect immutable inputs for one deterministic screenshot run."""

    base_url: str
    admin_key: str
    output_dir: Path
    seed_file: Path
    timeout_seconds: float
    wait_seconds: float
    headless: bool
    skip_prepare: bool
    mock_screens: bool


@dataclass(frozen=True)
class AlarmTransition:
    """Describe one alarm lifecycle action used to stage the demo gallery."""

    action: str
    actor_field: str
    actor: str
    note: str

    def payload(self) -> dict[str, str]:
        """Build the API body while retaining the action-specific actor field."""
        return {self.actor_field: self.actor, "note": self.note}


_RESOLVE_TRANSITION = AlarmTransition(
    action="resolve",
    actor_field="actor",
    actor="Demo Script",
    note="Screenshot flow resolve",
)
_ACK_TRANSITION = AlarmTransition(
    action="ack",
    actor_field="acked_by",
    actor="Demo Operator",
    note="Screenshot flow acknowledgment",
)


def _normalize_base_url(base_url: str) -> str:
    """Remove a trailing slash so constructed route URLs stay canonical."""
    return base_url.rstrip("/")


def _resolve_admin_key(cli_value: str | None) -> str:
    """Resolve the admin key and fail before making requests when it is blank."""
    key = (cli_value or os.getenv("ADMIN_API_KEY") or "").strip()
    if not key:
        raise DemoCaptureError(
            "Missing admin key. Set ADMIN_API_KEY in environment or pass --admin-key."
        )
    return key


def _http_json(
    method: str,
    url: str,
    headers: dict[str, str] | None = None,
    body: bytes | None = None,
    timeout: float = 10.0,
) -> HttpResult:
    """Translate shared preparation transport errors into capture-specific failures."""
    try:
        return _request_json(method, url, headers, body, timeout)
    except DemoPrepareError as exc:
        raise DemoCaptureError(str(exc)) from exc


def _extract_detail(payload: dict[str, Any] | list[Any] | None) -> str | None:
    """Extract FastAPI's human-readable error detail when one is available."""
    if isinstance(payload, dict):
        detail = payload.get("detail")
        if isinstance(detail, str):
            return detail
    return None


def _ensure_output_dir(output_dir: Path) -> None:
    """Create the capture directory because Playwright does not create parents."""
    output_dir.mkdir(parents=True, exist_ok=True)


def _create_placeholder_screens(output_dir: Path) -> list[Path]:
    """Create test-only one-pixel fixtures that must never be published."""
    _ensure_output_dir(output_dir)
    created: list[Path] = []
    for filename in SHOT_FILENAMES:
        path = output_dir / filename
        path.write_bytes(_ONE_PIXEL_PNG)
        created.append(path)
    return created


def _admin_headers(admin_key: str) -> dict[str, str]:
    """Build the authenticated JSON headers used by administrative API calls."""
    return {"X-Admin-Key": admin_key, "Content-Type": "application/json"}


def _login_admin_ui(page: Any, base_url: str, admin_key: str) -> None:
    """Establish a named browser session before visiting protected operator pages."""
    page.goto(f"{base_url}/admin/login", wait_until="networkidle")
    page.fill("#operator_name", "Demo Operator")
    page.fill("#admin_key", admin_key)
    page.click(".auth-panel button[type='submit']")
    page.wait_for_url(f"{base_url}/admin*")
    page.wait_for_selector("#search")
    _wait_for_motion(page)


def _wait_for_motion(page: Any) -> None:
    """Wait for the deterministic UI settle marker before recording evidence."""
    page.locator("body.motion-settled").wait_for()
    targets = page.locator("[data-motion]:visible")
    for index in range(targets.count()):
        targets.nth(index).scroll_into_view_if_needed()
    page.wait_for_timeout(650)
    page.mouse.move(2, 2)


def _require_ok(result: HttpResult, message: str) -> None:
    """Raise a contextual capture error for unsuccessful HTTP responses."""
    if result.status_code >= 400:
        detail = _extract_detail(result.json_body) or result.body
        raise DemoCaptureError(f"{message} (HTTP {result.status_code}): {detail}")


def _trigger_alarm(base_url: str, token: str, timeout: float) -> str:
    """Trigger a seeded device alarm and return the identifier used by later scenes."""
    query = parse.urlencode({"token": token})
    result = _http_json("GET", f"{base_url}/v1/yealink/alarm?{query}", timeout=timeout)
    _require_ok(result, "Trigger request failed")
    if not isinstance(result.json_body, dict) or "alarm_id" not in result.json_body:
        raise DemoCaptureError("Trigger response did not contain alarm_id.")
    return str(result.json_body["alarm_id"])


def _ack_token_from_simulation(
    base_url: str, admin_key: str, alarm_id: str, timeout: float
) -> str:
    """Recover an alarm's capability token from the simulated notification stream."""
    result = _http_json(
        "GET",
        f"{base_url}/v1/simulation/notifications",
        headers=_admin_headers(admin_key),
        timeout=timeout,
    )
    _require_ok(result, "Failed to read simulation notifications")
    payload = result.json_body if isinstance(result.json_body, dict) else {}
    notifications = payload.get("notifications", [])
    for notification in notifications if isinstance(notifications, list) else []:
        encoded = json.dumps(notification)
        if alarm_id not in encoded:
            continue
        match = re.search(r"/a/([A-Za-z0-9_-]+)", encoded)
        if match:
            return match.group(1)
    raise DemoCaptureError(
        "Simulation notifications did not contain the alarm ACK link."
    )


def _transition_alarm(
    base_url: str,
    admin_key: str,
    alarm_id: str,
    timeout: float,
    transition: AlarmTransition,
) -> None:
    """Set one demo alarm lifecycle state through the administrative API."""
    result = _http_json(
        "POST",
        f"{base_url}/v1/alarms/{alarm_id}/{transition.action}",
        headers=_admin_headers(admin_key),
        body=json.dumps(transition.payload()).encode("utf-8"),
        timeout=timeout,
    )
    if result.status_code not in (200, 204):
        detail = _extract_detail(result.json_body) or result.body
        raise DemoCaptureError(
            f"{transition.action.title()} for alarm {alarm_id} failed (HTTP {result.status_code}): {detail}"
        )


def _resolve_alarm(
    base_url: str, admin_key: str, alarm_id: str, timeout: float
) -> None:
    """Resolve one demo alarm so the gallery includes the terminal lifecycle state."""
    _transition_alarm(
        base_url,
        admin_key,
        alarm_id,
        timeout,
        _RESOLVE_TRANSITION,
    )


def _ack_alarm(base_url: str, admin_key: str, alarm_id: str, timeout: float) -> None:
    """Acknowledge one demo alarm through the API for deterministic state setup."""
    _transition_alarm(
        base_url,
        admin_key,
        alarm_id,
        timeout,
        _ACK_TRANSITION,
    )


def _resolve_all_triggered(base_url: str, admin_key: str, timeout: float) -> None:
    """Clear pre-existing triggered alarms so repeated capture runs remain stable."""
    result = _http_json(
        "GET",
        f"{base_url}/v1/alarms?status=triggered&limit=200",
        headers=_admin_headers(admin_key),
        timeout=timeout,
    )
    _require_ok(result, "Failed to list triggered alarms for baseline cleanup")
    if not isinstance(result.json_body, list):
        return
    alarm_ids = [
        item.get("id")
        for item in result.json_body
        if isinstance(item, dict) and item.get("id")
    ]
    if not alarm_ids:
        return
    payload = {
        "alarm_ids": alarm_ids,
        "actor": "Demo Baseline Reset",
        "note": "Resolve pre-existing triggered alarms before screenshot run.",
    }
    resolve_result = _http_json(
        "POST",
        f"{base_url}/v1/alarms/bulk/resolve",
        headers=_admin_headers(admin_key),
        body=json.dumps(payload).encode("utf-8"),
        timeout=timeout,
    )
    _require_ok(resolve_result, "Failed to bulk-resolve baseline triggered alarms")


def _wait_for_simulation_notifications(
    base_url: str,
    admin_key: str,
    timeout_seconds: float,
    poll_interval: float = 0.5,
) -> int:
    """Poll until the worker emits evidence needed for notification screenshots."""
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        result = _http_json(
            "GET",
            f"{base_url}/v1/simulation/status",
            headers=_admin_headers(admin_key),
            timeout=max(2.0, poll_interval + 1),
        )
        _require_ok(result, "Failed to query simulation status")
        if isinstance(result.json_body, dict):
            total = int(result.json_body.get("total_notifications", 0))
            if total > 0:
                return total
        time.sleep(poll_interval)
    raise DemoCaptureError(
        "No simulation notifications observed within timeout. "
        "Ensure worker is running and SIMULATION_ENABLED=true."
    )


def _prepare_real_capture(
    config: CaptureConfig,
) -> tuple[str, list[Path], str, str, str]:
    """Seed and normalize application state before launching a real browser."""
    base_url = _normalize_base_url(config.base_url)
    _ensure_output_dir(config.output_dir)

    if not config.skip_prepare:
        try:
            run_prepare(
                base_url=base_url,
                admin_key=config.admin_key,
                seed_file=config.seed_file,
                timeout_seconds=config.timeout_seconds,
            )
        except DemoPrepareError as exc:
            raise DemoCaptureError(str(exc)) from exc

    _resolve_all_triggered(base_url, config.admin_key, config.timeout_seconds)

    output_paths = [config.output_dir / filename for filename in SHOT_FILENAMES]
    alarm_primary = _trigger_alarm(
        base_url, DEMO_TOKENS["primary"], config.timeout_seconds
    )
    alarm_secondary = _trigger_alarm(
        base_url, DEMO_TOKENS["secondary"], config.timeout_seconds
    )
    _wait_for_simulation_notifications(base_url, config.admin_key, config.wait_seconds)
    ack_token_secondary = _ack_token_from_simulation(
        base_url, config.admin_key, alarm_secondary, config.timeout_seconds
    )
    return base_url, output_paths, alarm_primary, alarm_secondary, ack_token_secondary


def _open_first_alarm_detail(page: Any) -> None:
    """Open the first visible alarm while failing clearly on an empty worklist."""
    details = page.get_by_role(
        "link", name=re.compile(r"^Open alarm ", re.IGNORECASE)
    ).first
    if details.count() == 0:
        raise DemoCaptureError("No alarm rows found to open detail page.")
    details.click()
    page.wait_for_selector(".detail-heading")


def _capture_desktop_alarm_views(
    page: Any,
    *,
    admin_url: str,
    output_paths: list[Path],
) -> None:
    """Capture the curated desktop worklist, filter, and detail scenes."""
    page.goto(f"{admin_url}&status=triggered", wait_until="networkidle")
    page.wait_for_selector("#search")
    _wait_for_motion(page)
    page.screenshot(path=str(output_paths[0]), full_page=True)

    page.goto(f"{admin_url}&status=triggered", wait_until="networkidle")
    page.wait_for_selector("a[href*='/admin/alarms/']")
    _wait_for_motion(page)
    page.screenshot(path=str(output_paths[1]), full_page=True)

    page.fill("#search", "library")
    page.click(".filters button[type='submit']")
    page.wait_for_load_state("networkidle")
    _wait_for_motion(page)
    page.screenshot(path=str(output_paths[2]), full_page=True)

    _open_first_alarm_detail(page)
    _wait_for_motion(page)
    page.screenshot(path=str(output_paths[3]), full_page=True)


def _capture_mobile_ack_views(
    *,
    browser: Any,
    base_url: str,
    ack_token: str,
    output_paths: list[Path],
) -> Any:
    """Capture responder acknowledgement before and after submission on mobile."""
    mobile = browser.new_context(viewport={"width": 390, "height": 844})
    ack_page = mobile.new_page()
    ack_page.goto(f"{base_url}/a/{ack_token}", wait_until="networkidle")
    ack_page.wait_for_selector("form")
    _wait_for_motion(ack_page)
    ack_page.screenshot(path=str(output_paths[5]), full_page=True)

    ack_page.fill("#acked_by", "Demo Nurse")
    ack_page.fill("#note", "Taking over response.")
    ack_page.locator("#acknowledge-form button[type='submit']").click()
    ack_page.wait_for_load_state("networkidle")
    ack_page.wait_for_selector(".status-acknowledged, .completion-message")
    _wait_for_motion(ack_page)
    ack_page.screenshot(path=str(output_paths[6]), full_page=True)
    return mobile


def _capture_simulation_views(
    page: Any,
    *,
    base_url: str,
    config: CaptureConfig,
    output_paths: list[Path],
) -> None:
    """Capture the simulation feed before and after its explicit clear action."""
    _wait_for_simulation_notifications(base_url, config.admin_key, config.wait_seconds)
    page.goto(f"{base_url}/admin/simulation", wait_until="networkidle")
    page.wait_for_selector("h1")
    _wait_for_motion(page)
    page.screenshot(path=str(output_paths[8]), full_page=True)

    clear_result = _http_json(
        "POST",
        f"{base_url}/v1/simulation/notifications/clear",
        headers=_admin_headers(config.admin_key),
        body=b"{}",
        timeout=config.timeout_seconds,
    )
    _require_ok(clear_result, "Failed to clear simulation notifications")

    page.goto(f"{base_url}/admin/simulation", wait_until="networkidle")
    page.wait_for_selector("h1")
    _wait_for_motion(page)
    page.screenshot(path=str(output_paths[9]), full_page=True)


def _capture_real_screens(config: CaptureConfig) -> list[Path]:
    """Drive Chromium through the complete real-UI gallery sequence."""
    try:
        from playwright.sync_api import (
            sync_playwright,  # type: ignore[import-not-found]
        )
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise DemoCaptureError(
            "Playwright is not installed. Install with `pip install playwright` and "
            "`playwright install chromium`."
        ) from exc

    base_url, output_paths, alarm_primary, alarm_secondary, ack_token_secondary = (
        _prepare_real_capture(config)
    )
    admin_url = f"{base_url}/admin?lang=en"

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=config.headless)

        desktop = browser.new_context(viewport={"width": 1440, "height": 900})
        page = desktop.new_page()
        _login_admin_ui(page, base_url, config.admin_key)

        _capture_desktop_alarm_views(
            page, admin_url=admin_url, output_paths=output_paths
        )

        _ack_alarm(base_url, config.admin_key, alarm_primary, config.timeout_seconds)
        page.goto(f"{admin_url}&status=acknowledged", wait_until="networkidle")
        page.wait_for_selector("a[href*='/admin/alarms/']")
        _wait_for_motion(page)
        page.screenshot(path=str(output_paths[4]), full_page=True)

        mobile = _capture_mobile_ack_views(
            browser=browser,
            base_url=base_url,
            ack_token=ack_token_secondary,
            output_paths=output_paths,
        )

        _resolve_alarm(
            base_url, config.admin_key, alarm_secondary, config.timeout_seconds
        )
        page.goto(f"{admin_url}&status=resolved", wait_until="networkidle")
        page.wait_for_selector("a[href*='/admin/alarms/']")
        _wait_for_motion(page)
        page.screenshot(path=str(output_paths[7]), full_page=True)

        _capture_simulation_views(
            page,
            base_url=base_url,
            config=config,
            output_paths=output_paths,
        )

        mobile.close()
        desktop.close()
        browser.close()

    return output_paths


def run_capture(config: CaptureConfig) -> list[Path]:
    """Run either the real capture path or the explicitly test-only fixture path."""
    if config.mock_screens:
        return _create_placeholder_screens(config.output_dir)
    return _capture_real_screens(config)


def _build_parser() -> argparse.ArgumentParser:
    """Define the command-line contract shared by local and hosted capture runs."""
    parser = argparse.ArgumentParser(
        description="Capture local Mock University demo screenshots."
    )
    parser.add_argument("--base-url", default="http://localhost:8080")
    parser.add_argument("--admin-key", default=None)
    parser.add_argument("--output-dir", default="docs/assets/screenshots/generated")
    parser.add_argument("--seed-file", default="deploy/simulation_seed.yaml")
    parser.add_argument("--timeout-seconds", type=float, default=10.0)
    parser.add_argument("--wait-seconds", type=float, default=20.0)
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--skip-prepare", action="store_true")
    parser.add_argument("--mock-screens", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Execute screenshot capture and return a shell-friendly status code."""
    args = _build_parser().parse_args(argv)

    try:
        created = run_capture(
            CaptureConfig(
                base_url=args.base_url,
                admin_key=_resolve_admin_key(args.admin_key),
                output_dir=Path(args.output_dir),
                seed_file=Path(args.seed_file),
                timeout_seconds=args.timeout_seconds,
                wait_seconds=args.wait_seconds,
                headless=not args.headed,
                skip_prepare=args.skip_prepare,
                mock_screens=args.mock_screens,
            )
        )
    except DemoCaptureError as exc:
        print(f"[demo-capture] ERROR: {exc}", file=sys.stderr)
        return 1

    print("[demo-capture] Screenshots created:")
    for path in created:
        print(f"  - {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
