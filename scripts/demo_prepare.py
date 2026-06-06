#!/usr/bin/env python3
"""Prepare a local mock-university demo environment.

This script verifies service readiness, loads the simulation seed, and clears
simulation notifications to create a deterministic baseline for screenshots.
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib import parse

TRIGGER_TOKENS: list[tuple[str, str, str]] = [
    (
        "north_ops",
        "demo1",
        "Security Operations Center (North Campus)",
    ),
    (
        "north_library",
        "demo2",
        "Main Library Service Desk (North Campus)",
    ),
    (
        "north_chem_lab",
        "demo3",
        "Chemistry Laboratory Wing C (North Campus)",
    ),
    (
        "medical_or",
        "demo4",
        "Surgical Unit OR Control (Medical Campus)",
    ),
    (
        "medical_dorm_lobby",
        "demo5",
        "Residence Hall South Lobby (Medical Campus)",
    ),
]


class DemoPrepareError(RuntimeError):
    """Typed error for demo preparation failures."""


@dataclass(frozen=True)
class HttpResult:
    status_code: int
    body: str
    json_body: dict[str, Any] | list[Any] | None


RequestFunc = Callable[[str, str, dict[str, str], bytes | None, float], HttpResult]


def _parse_http_url(url: str) -> parse.SplitResult:
    parsed = parse.urlsplit(url)
    if parsed.scheme not in {"http", "https"}:
        raise DemoPrepareError(f"URL must use http or https scheme: {url}")
    if not parsed.hostname:
        raise DemoPrepareError(f"URL must include a hostname: {url}")
    return parsed


def _http_target(parsed: parse.SplitResult) -> str:
    path = parsed.path or "/"
    if parsed.query:
        return f"{path}?{parsed.query}"
    return path


def _request_json(
    method: str,
    url: str,
    headers: dict[str, str] | None = None,
    body: bytes | None = None,
    timeout: float = 10.0,
) -> HttpResult:
    parsed = _parse_http_url(url)
    connection_cls = (
        http.client.HTTPSConnection
        if parsed.scheme == "https"
        else http.client.HTTPConnection
    )
    port = parsed.port
    host = parsed.hostname
    if host is None:
        raise DemoPrepareError(f"URL must include a hostname: {url}")
    connection = connection_cls(host, port=port, timeout=timeout)
    try:
        connection.request(
            method.upper(),
            _http_target(parsed),
            body=body,
            headers=headers or {},
        )
        response = connection.getresponse()
        raw = response.read().decode("utf-8")
        parsed_body: dict[str, Any] | list[Any] | None = None
        if raw.strip():
            try:
                parsed_body = json.loads(raw)
            except json.JSONDecodeError:
                parsed_body = None
        return HttpResult(status_code=response.status, body=raw, json_body=parsed_body)
    except OSError as exc:
        raise DemoPrepareError(f"Failed to reach {url}: {exc}") from exc
    finally:
        connection.close()


def _normalize_base_url(base_url: str) -> str:
    return base_url.rstrip("/")


def _resolve_admin_key(cli_value: str | None) -> str:
    key = (cli_value or os.getenv("ADMIN_API_KEY") or "").strip()
    if not key:
        raise DemoPrepareError(
            "Missing admin key. Set ADMIN_API_KEY in environment or pass --admin-key."
        )
    return key


def _extract_detail(payload: dict[str, Any] | list[Any] | None) -> str | None:
    if isinstance(payload, dict):
        detail = payload.get("detail")
        if isinstance(detail, str):
            return detail
    return None


def _require_ready(
    *,
    base_url: str,
    timeout_seconds: float,
    request_func: RequestFunc,
) -> HttpResult:
    ready = request_func("GET", f"{base_url}/readyz", {}, None, timeout_seconds)
    if ready.status_code != 200:
        raise DemoPrepareError(
            f"Service not ready (HTTP {ready.status_code}) at {base_url}/readyz."
        )
    return ready


def _load_seed(
    *,
    base_url: str,
    admin_key: str,
    seed_file: Path,
    timeout_seconds: float,
    request_func: RequestFunc,
) -> HttpResult:
    seed_result = request_func(
        "POST",
        f"{base_url}/v1/admin/seed",
        {
            "X-Admin-Key": admin_key,
            "Content-Type": "application/x-yaml",
        },
        seed_file.read_bytes(),
        timeout_seconds,
    )
    if seed_result.status_code == 200:
        return seed_result

    detail = _extract_detail(seed_result.json_body)
    if seed_result.status_code == 401:
        raise DemoPrepareError("Seed request unauthorized (401). Check ADMIN_API_KEY.")
    if seed_result.status_code == 409:
        raise DemoPrepareError(f"Seed request conflict (409): {detail or seed_result.body}")
    raise DemoPrepareError(
        f"Seed request failed (HTTP {seed_result.status_code}): {detail or seed_result.body}"
    )


def _clear_simulation_notifications(
    *,
    base_url: str,
    admin_key: str,
    timeout_seconds: float,
    request_func: RequestFunc,
) -> HttpResult:
    clear_result = request_func(
        "POST",
        f"{base_url}/v1/simulation/notifications/clear",
        {
            "X-Admin-Key": admin_key,
            "Content-Type": "application/json",
        },
        b"{}",
        timeout_seconds,
    )
    if clear_result.status_code == 200:
        return clear_result

    detail = _extract_detail(clear_result.json_body)
    if clear_result.status_code == 401:
        raise DemoPrepareError("Simulation clear unauthorized (401). Check ADMIN_API_KEY.")
    if clear_result.status_code == 404:
        raise DemoPrepareError(
            "Simulation endpoint not found (404). "
            "Set SIMULATION_ENABLED=true and restart stack."
        )
    raise DemoPrepareError(
        "Simulation clear failed "
        f"(HTTP {clear_result.status_code}): {detail or clear_result.body}"
    )


def run_prepare(
    *,
    base_url: str,
    admin_key: str,
    seed_file: Path,
    timeout_seconds: float = 10.0,
    request_func: RequestFunc = _request_json,
) -> dict[str, Any]:
    resolved_base_url = _normalize_base_url(base_url)
    if not seed_file.exists():
        raise DemoPrepareError(f"Seed file not found: {seed_file}")

    ready = _require_ready(
        base_url=resolved_base_url,
        timeout_seconds=timeout_seconds,
        request_func=request_func,
    )
    seed_result = _load_seed(
        base_url=resolved_base_url,
        admin_key=admin_key,
        seed_file=seed_file,
        timeout_seconds=timeout_seconds,
        request_func=request_func,
    )
    clear_result = _clear_simulation_notifications(
        base_url=resolved_base_url,
        admin_key=admin_key,
        timeout_seconds=timeout_seconds,
        request_func=request_func,
    )

    return {
        "base_url": resolved_base_url,
        "seed_file": str(seed_file),
        "ready_status": ready.status_code,
        "seed_status": seed_result.status_code,
        "clear_status": clear_result.status_code,
        "trigger_tokens": TRIGGER_TOKENS,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Prepare local Mock University demo data and simulation state."
    )
    parser.add_argument(
        "--base-url",
        default="http://localhost:8080",
        help="Alarm Broker base URL (default: http://localhost:8080).",
    )
    parser.add_argument(
        "--admin-key",
        default=None,
        help="Admin API key. Falls back to ADMIN_API_KEY environment variable.",
    )
    parser.add_argument(
        "--seed-file",
        default="deploy/simulation_seed.yaml",
        help="Path to the simulation seed YAML file.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=10.0,
        help="HTTP timeout per request in seconds.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        summary = run_prepare(
            base_url=args.base_url,
            admin_key=_resolve_admin_key(args.admin_key),
            seed_file=Path(args.seed_file),
            timeout_seconds=args.timeout_seconds,
        )
    except DemoPrepareError as exc:
        print(f"[demo-prepare] ERROR: {exc}", file=sys.stderr)
        return 1

    print("[demo-prepare] Ready check: OK")
    print(f"[demo-prepare] Seed loaded from: {summary['seed_file']}")
    print("[demo-prepare] Simulation notifications cleared")
    print("[demo-prepare] Trigger tokens for demo scenes:")
    for token_key, token_value, token_desc in summary["trigger_tokens"]:
        print(f"  - {token_key}: {token_value} ({token_desc})")
    print("[demo-prepare] Next step: run `python scripts/demo_capture.py`")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
