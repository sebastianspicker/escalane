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


def test_check_allows_notifications_to_alarms_but_rejects_the_reverse_edge(tmp_path: Path) -> None:
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
        "import cycle: alarms -> notifications -> alarms",
    ]


def test_check_reports_package_cycle_when_both_edges_are_forbidden(tmp_path: Path) -> None:
    _write_module(
        tmp_path,
        "configuration",
        "audit.py",
        "from escalane.operations.queries import get_alarm_counts\n",
    )
    _write_module(
        tmp_path,
        "operations",
        "queries.py",
        "from escalane.configuration.audit import record_audit\n",
    )

    assert _check(tmp_path) == [
        "src/escalane/configuration/audit.py:1: package 'configuration' may not import "
        "'operations' (escalane.operations.queries)",
        "src/escalane/operations/queries.py:1: package 'operations' may not import "
        "'configuration' (escalane.configuration.audit)",
        "import cycle: configuration -> operations -> configuration",
    ]


def test_check_rejects_import_of_dissolved_contracts_namespace(tmp_path: Path) -> None:
    _write_module(
        tmp_path,
        "alarms",
        "legacy.py",
        "from escalane.contracts.alarms import AlarmStatus\n",
    )

    assert _check(tmp_path) == [
        "src/escalane/alarms/legacy.py:1: imports removed namespace escalane.contracts "
        "(escalane.contracts.alarms)"
    ]


def test_check_rejects_http_framework_outside_web(tmp_path: Path) -> None:
    _write_module(tmp_path, "alarms", "lifecycle.py", "from fastapi import HTTPException\n")

    assert _check(tmp_path) == [
        "src/escalane/alarms/lifecycle.py:1: package 'alarms' may not import HTTP framework fastapi"
    ]


def test_check_rejects_sql_and_session_calls_in_web_adapter(tmp_path: Path) -> None:
    _write_module(
        tmp_path,
        "web",
        "routes/notes.py",
        "from sqlalchemy import select\n"
        "from sqlalchemy.ext.asyncio import AsyncSession\n\n"
        "async def handler(session: AsyncSession) -> None:\n"
        "    await session.commit()\n",
    )

    assert _check(tmp_path) == [
        "src/escalane/web/routes/notes.py:1: web adapter imports sqlalchemy; "
        "delegate to a feature module",
        "src/escalane/web/routes/notes.py:5: web adapter calls session.commit(); "
        "delegate to a feature module",
    ]


def test_check_rejects_renamed_async_session_parameter_in_web_adapter(tmp_path: Path) -> None:
    _write_module(
        tmp_path,
        "web",
        "routes/notes.py",
        "import sqlalchemy.ext.asyncio as sa\n"
        "from sqlalchemy.ext.asyncio import AsyncSession\n\n"
        "async def handler(\n"
        "    db: AsyncSession, other: sa.AsyncSession, text: 'AsyncSession'\n"
        ") -> None:\n"
        "    await db.commit()\n"
        "    await other.execute(1)\n"
        "    await text.flush()\n",
    )

    assert _check(tmp_path) == [
        "src/escalane/web/routes/notes.py:7: web adapter calls db.commit(); "
        "delegate to a feature module",
        "src/escalane/web/routes/notes.py:8: web adapter calls other.execute(); "
        "delegate to a feature module",
        "src/escalane/web/routes/notes.py:9: web adapter calls text.flush(); "
        "delegate to a feature module",
    ]


def test_check_rejects_session_data_call_in_worker_adapter(tmp_path: Path) -> None:
    _write_module(
        tmp_path,
        "worker",
        "tasks.py",
        "async def load(session, alarm_id):\n    return await session.get(object, alarm_id)\n",
    )

    assert _check(tmp_path) == [
        "src/escalane/worker/tasks.py:2: worker adapter calls session.get(); "
        "delegate to a feature module"
    ]


def test_check_allows_sqlalchemy_exc_only_in_worker(tmp_path: Path) -> None:
    source = "from sqlalchemy.exc import SQLAlchemyError\n"
    _write_module(tmp_path, "worker", "tasks.py", source)
    _write_module(tmp_path, "web", "errors.py", source)
    _write_module(tmp_path, "worker", "select.py", "from sqlalchemy import select\n")

    assert _check(tmp_path) == [
        "src/escalane/web/errors.py:1: web adapter imports sqlalchemy.exc; "
        "delegate to a feature module",
        "src/escalane/worker/select.py:1: worker adapter imports sqlalchemy; "
        "delegate to a feature module",
    ]


def test_check_rejects_private_cross_module_import(tmp_path: Path) -> None:
    _write_module(tmp_path, "alarms", "triggers.py", "from escalane.alarms.outbox import _job_id\n")

    assert _check(tmp_path) == [
        "src/escalane/alarms/triggers.py:1: imports private name _job_id "
        "from escalane.alarms.outbox"
    ]
