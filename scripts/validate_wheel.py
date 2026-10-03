"""Validate that a built Escalane wheel contains only the current package surface."""

from __future__ import annotations

import argparse
import importlib
import sys
from importlib.resources import files
from pathlib import Path
from zipfile import ZipFile

REQUIRED_MEMBERS = frozenset(
    {
        "escalane/web/templates/base.html",
        "escalane/web/templates/ack.html",
        "escalane/web/assets/ui.css",
        "escalane/web/assets/ui.js",
        "escalane/web/assets/escalane-mark.svg",
        "escalane/web/assets/atkinson-hyperlegible-next.woff2",
        "escalane/web/assets/atkinson-hyperlegible-mono.woff2",
        "escalane/web/assets/fonts-OFL.txt",
    }
)
FORBIDDEN_MEMBERS = frozenset(
    {
        "escalane/alarms/publisher.py",
        "escalane/notifications/webhook.py",
        "escalane/web/routes/admin_configuration.py",
        "escalane/worker/task_workflows.py",
        "escalane/config/constants.py",
        "escalane/persistence/json_merge.py",
        "escalane/persistence/telemetry.py",
        "escalane/operations/metrics.py",
        "escalane/operations/dashboard.py",
        "escalane/web/alarm_history.py",
        "escalane/web/routes/admin_console.py",
        "escalane/alarms/contracts.py",
    }
)
FORBIDDEN_PREFIXES = ("escalane/contracts/",)
# Import-time smoke targets that need no environment variables or live services.
IMPORT_SMOKE_MODULES = (
    "escalane.web.routes",
    "escalane.web.console",
    "escalane.worker.tasks",
)


def validate(wheel: Path) -> list[str]:
    """Return deterministic packaging errors for one wheel."""
    with ZipFile(wheel) as archive:
        members = frozenset(archive.namelist())

    errors = [f"missing required wheel member: {path}" for path in REQUIRED_MEMBERS - members]
    errors.extend(
        f"obsolete wheel member is present: {path}" for path in FORBIDDEN_MEMBERS & members
    )
    errors.extend(
        f"obsolete wheel member is present: {path}"
        for path in members
        if path.startswith(FORBIDDEN_PREFIXES)
    )
    return sorted(errors)


def validate_installed() -> list[str]:
    """Return errors for the Escalane package installed in the running interpreter.

    Run this with the interpreter of a clean virtual environment that has only the
    built wheel installed; the package must resolve from that environment.
    """
    import escalane

    errors: list[str] = []
    origin = Path(escalane.__file__).resolve()
    if not origin.is_relative_to(Path(sys.prefix).resolve()):
        errors.append(f"escalane is not imported from the active environment: {origin}")
    root = files("escalane.web")
    for member in sorted(REQUIRED_MEMBERS):
        relative = member.removeprefix("escalane/web/")
        if not root.joinpath(*relative.split("/")).is_file():
            errors.append(f"missing packaged resource: {member}")
    for module in IMPORT_SMOKE_MODULES:
        try:
            importlib.import_module(module)
        except Exception as exc:
            errors.append(f"cannot import {module}: {type(exc).__name__}: {exc}")
    return errors


def main() -> int:
    """Validate the wheel, or the installed package with ``--installed``."""
    parser = argparse.ArgumentParser()
    parser.add_argument("wheel", type=Path, nargs="?")
    parser.add_argument(
        "--installed",
        action="store_true",
        help="check packaged resources of the installed package instead of a wheel file",
    )
    args = parser.parse_args()

    if args.installed:
        errors = validate_installed()
        if errors:
            print("Installed package validation failed:")
            print("\n".join(errors))
            return 1
        print("Installed package validation passed")
        return 0
    if args.wheel is None:
        parser.error("a wheel path is required unless --installed is given")

    errors = validate(args.wheel)
    if errors:
        print("Wheel validation failed:")
        print("\n".join(errors))
        return 1
    print(f"Wheel validation passed: {args.wheel.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
