#!/usr/bin/env python3
"""Prepare a local mock-university demo environment.

This script verifies service readiness, loads the simulation seed, and clears
simulation notifications to create a deterministic baseline for screenshots.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib import error, request

TRIGGER_TOKENS: list[tuple[str, str, str]] = [
    (
        "north_ops",
        "MU_YLK_NORTH_OPS_2001",
        "Security Operations Center (North Campus)",
    ),
    (
        "north_library",
        "MU_YLK_NORTH_LIB_2002",
        "Main Library Service Desk (North Campus)",
    ),
    (
        "north_chem_lab",
        "MU_YLK_CHEM_LAB_2003",
        "Chemistry Laboratory Wing C (North Campus)",
    ),
    (
        "medical_or",
        "MU_YLK_MED_OR_2004",
        "Surgical Unit OR Control (Medical Campus)",
    ),
    (
        "medical_dorm_lobby",
        "MU_YLK_DORM_LOBBY_2005",
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


def _request_json(
    method: str,
    url: str,
    headers: dict[str, str] | None = None,
    body: bytes | None = None,
    timeout: float = 10.0,
) -> HttpResult:
    req = request.Request(url=url, data=body, method=method.upper(), headers=headers or {})
    try:
        with request.urlopen(req, timeout=timeout) as response:  # noqa: S310
            raw = response.read().decode("utf-8")
            parsed: dict[str, Any] | list[Any] | None = None
            if raw.strip():
                try:
                    parsed = json.loads(raw)
                except json.JSONDecodeError:
                    parsed = None
            return HttpResult(status_code=response.status, body=raw, json_body=parsed)
    except error.HTTPError as exc:
        raw = exc.read().decode("utf-8")
        parsed: dict[str, Any] | list[Any] | None = None
        if raw.strip():
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                parsed = None
        return HttpResult(status_code=exc.code, body=raw, json_body=parsed)
    except error.URLError as exc:
        raise DemoPrepareError(f"Failed to reach {url}: {exc.reason}") from exc


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

    ready = request_func(
        "GET",
        f"{resolved_base_url}/readyz",
        {},
        None,
        timeout_seconds,
    )
    if ready.status_code != 200:
        raise DemoPrepareError(
            f"Service not ready (HTTP {ready.status_code}) at {resolved_base_url}/readyz."
        )

    seed_payload = seed_file.read_bytes()
    seed_result = request_func(
        "POST",
        f"{resolved_base_url}/v1/admin/seed",
        {
            "X-Admin-Key": admin_key,
            "Content-Type": "application/x-yaml",
        },
        seed_payload,
        timeout_seconds,
    )
    if seed_result.status_code != 200:
        detail = _extract_detail(seed_result.json_body)
        if seed_result.status_code == 401:
            raise DemoPrepareError("Seed request unauthorized (401). Check ADMIN_API_KEY.")
        if seed_result.status_code == 409:
            raise DemoPrepareError(f"Seed request conflict (409): {detail or seed_result.body}")
        raise DemoPrepareError(
            f"Seed request failed (HTTP {seed_result.status_code}): {detail or seed_result.body}"
        )

    clear_result = request_func(
        "POST",
        f"{resolved_base_url}/v1/simulation/notifications/clear",
        {
            "X-Admin-Key": admin_key,
            "Content-Type": "application/json",
        },
        b"{}",
        timeout_seconds,
    )
    if clear_result.status_code != 200:
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
