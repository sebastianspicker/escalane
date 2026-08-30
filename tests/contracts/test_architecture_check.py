"""Mutation-style contracts for Escalane's package-boundary checker."""

from __future__ import annotations

from pathlib import Path

from scripts import check_architecture


def _write_module(tmp_path: Path, package: str, name: str, source: str) -> Path:
    package_root = tmp_path / "src" / "escalane"
    module = package_root / package / name
    module.parent.mkdir(parents=True, exist_ok=True)
    module.write_text(source, encoding="utf-8")
    return package_root


def _check(tmp_path: Path) -> list[str]:
    return check_architecture.check(tmp_path / "src" / "escalane", tmp_path)


def test_check_allows_intentional_alarm_to_persistence_edge(tmp_path: Path) -> None:
    _write_module(
        tmp_path,
        "alarms",
        "lifecycle.py",
        "from escalane.persistence.models import Alarm\n",
    )

    assert _check(tmp_path) == []


def test_check_rejects_forbidden_persistence_to_feature_edge(tmp_path: Path) -> None:
    _write_module(
        tmp_path,
        "persistence",
        "models.py",
        "from escalane.alarms.lifecycle import create_alarm\n",
    )

    assert _check(tmp_path) == [
        "src/escalane/persistence/models.py:1: package 'persistence' may not import 'alarms' "
        "(escalane.alarms.lifecycle)"
    ]


def test_check_rejects_removed_namespace_import(tmp_path: Path) -> None:
    _write_module(
        tmp_path,
        "alarms",
        "legacy.py",
        "from escalane.services.alarm_service import create_alarm\n",
    )

    assert _check(tmp_path) == [
        "src/escalane/alarms/legacy.py:1: imports removed namespace escalane.services "
        "(escalane.services.alarm_service)"
    ]


def test_check_reports_package_cycle_when_cross_feature_edges_are_forbidden(tmp_path: Path) -> None:
    _write_module(
        tmp_path,
        "alarms",
        "outbox.py",
        "from escalane.notifications.delivery import completed_notification\n",
    )
    _write_module(
        tmp_path,
        "notifications",
        "delivery.py",
        "from escalane.alarms.outbox import enqueue_alarm_created_event\n",
    )

    assert _check(tmp_path) == [
        "src/escalane/alarms/outbox.py:1: package 'alarms' may not import 'notifications' "
        "(escalane.notifications.delivery)",
        "src/escalane/notifications/delivery.py:1: package 'notifications' may not import 'alarms' "
        "(escalane.alarms.outbox)",
        "import cycle: alarms -> notifications -> alarms",
    ]
