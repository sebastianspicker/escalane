"""Validate that a built Escalane wheel contains only the current package surface."""

from __future__ import annotations

import argparse
from pathlib import Path
from zipfile import ZipFile

REQUIRED_MEMBERS = frozenset(
    {
        "escalane/web/templates/base.html",
        "escalane/web/templates/ack.html",
        "escalane/web/assets/ui.css",
        "escalane/web/assets/ui.js",
        "escalane/web/assets/escalane-mark.svg",
    }
)
FORBIDDEN_MEMBERS = frozenset(
    {
        "escalane/alarms/publisher.py",
        "escalane/notifications/webhook.py",
        "escalane/web/routes/admin_configuration.py",
        "escalane/worker/task_workflows.py",
    }
)


def validate(wheel: Path) -> list[str]:
    """Return deterministic packaging errors for one wheel."""
    with ZipFile(wheel) as archive:
        members = frozenset(archive.namelist())

    errors = [f"missing required wheel member: {path}" for path in REQUIRED_MEMBERS - members]
    errors.extend(
        f"obsolete wheel member is present: {path}" for path in FORBIDDEN_MEMBERS & members
    )
    return sorted(errors)


def main() -> int:
    """Validate the wheel named on the command line."""
    parser = argparse.ArgumentParser()
    parser.add_argument("wheel", type=Path)
    args = parser.parse_args()

    errors = validate(args.wheel)
    if errors:
        print("Wheel validation failed:")
        print("\n".join(errors))
        return 1
    print(f"Wheel validation passed: {args.wheel.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
