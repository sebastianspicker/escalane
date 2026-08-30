"""Tests for escalane.persistence.engine: connection pool params and slow-query listener."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine

from escalane.persistence.engine import _install_slow_query_listener, create_async_engine_from_url
from tests.support.assertions import expect

pytestmark = [pytest.mark.unit]

_SQLITE_URL = "sqlite+aiosqlite:///:memory:"


class TestCreateAsyncEngineFromUrl:
    def test_slow_query_listener_skipped_when_zero(self):
        """Passing slow_query_log_ms=0 must not install the listener."""
        with (
            patch("escalane.persistence.engine.create_async_engine") as mock_create,
            patch("escalane.persistence.engine._install_slow_query_listener") as mock_install,
        ):
            mock_engine = MagicMock()
            mock_engine.sync_engine = MagicMock()
            mock_create.return_value = mock_engine

            create_async_engine_from_url(_SQLITE_URL, slow_query_log_ms=0)

        mock_install.assert_not_called()

    def test_slow_query_listener_installed_when_positive(self):
        """Passing slow_query_log_ms>0 installs the slow-query listener."""
        with (
            patch("escalane.persistence.engine.create_async_engine") as mock_create,
            patch("escalane.persistence.engine._install_slow_query_listener") as mock_install,
        ):
            mock_engine = MagicMock()
            mock_engine.sync_engine = MagicMock()
            mock_create.return_value = mock_engine

            create_async_engine_from_url(_SQLITE_URL, slow_query_log_ms=100)

        mock_install.assert_called_once_with(mock_engine.sync_engine, 100)

    def test_pool_params_forwarded_to_create_async_engine(self):
        """Pool parameters are forwarded to create_async_engine."""
        with patch("escalane.persistence.engine.create_async_engine") as mock_create:
            mock_engine = MagicMock()
            mock_engine.sync_engine = MagicMock()
            mock_create.return_value = mock_engine

            create_async_engine_from_url(
                _SQLITE_URL,
                pool_size=3,
                max_overflow=5,
                pool_timeout=10,
                pool_recycle=600,
                slow_query_log_ms=0,
            )

        mock_create.assert_called_once_with(
            _SQLITE_URL,
            pool_pre_ping=True,
            pool_size=3,
            max_overflow=5,
            pool_timeout=10,
            pool_recycle=600,
        )

    def test_returns_engine_object(self):
        """create_async_engine_from_url returns the engine produced by create_async_engine."""
        with patch("escalane.persistence.engine.create_async_engine") as mock_create:
            sentinel = MagicMock()
            sentinel.sync_engine = MagicMock()
            mock_create.return_value = sentinel

            result = create_async_engine_from_url(_SQLITE_URL, slow_query_log_ms=0)

        expect(result is sentinel)


class TestInstallSlowQueryListener:
    """Use a real synchronous SQLite engine to exercise the SQLAlchemy event hooks."""

    def _make_sync_engine(self):
        return create_engine("sqlite:///:memory:")

    def _dispatch_query(self, sync_engine, caplog, *, threshold_ms: int):
        """Dispatch a query through the registered timing hooks and return captured logs."""
        _install_slow_query_listener(sync_engine, threshold_ms=threshold_ms)
        mock_conn = MagicMock()
        mock_conn.info = {}
        sync_engine.dispatch.before_cursor_execute(mock_conn, None, "SELECT 1", None, None, False)
        with caplog.at_level(logging.WARNING, logger="escalane"):
            sync_engine.dispatch.after_cursor_execute(
                mock_conn, None, "SELECT 1", None, None, False
            )
        return caplog.records

    def test_slow_query_emits_warning(self, caplog):
        """Queries that exceed threshold_ms=0 always produce a WARNING log."""
        sync_engine = self._make_sync_engine()
        records = self._dispatch_query(sync_engine, caplog, threshold_ms=0)

        expect(any("slow_query" in record.message for record in records))

    def test_fast_query_no_warning(self, caplog):
        """Queries below a very high threshold do not produce a WARNING log."""
        sync_engine = self._make_sync_engine()
        records = self._dispatch_query(sync_engine, caplog, threshold_ms=999_999)

        expect(not any("slow_query" in record.message for record in records))

    def test_empty_start_time_stack_is_safe(self):
        """after_cursor_execute with no matching before event must not raise."""
        sync_engine = self._make_sync_engine()
        _install_slow_query_listener(sync_engine, threshold_ms=0)

        mock_conn = MagicMock()
        mock_conn.info = {}  # no query_start_time key

        # Should not raise
        sync_engine.dispatch.after_cursor_execute(mock_conn, None, "SELECT 1", None, None, False)

    def test_statement_truncated_in_log(self, caplog):
        """Statements longer than 200 chars are truncated in the log."""
        sync_engine = self._make_sync_engine()
        _install_slow_query_listener(sync_engine, threshold_ms=0)

        conn_info = {}
        mock_conn = MagicMock()
        mock_conn.info = conn_info
        long_stmt = "SELECT " + "x" * 300

        sync_engine.dispatch.before_cursor_execute(mock_conn, None, long_stmt, None, None, False)

        with caplog.at_level(logging.WARNING, logger="escalane"):
            sync_engine.dispatch.after_cursor_execute(mock_conn, None, long_stmt, None, None, False)

        warning_records = [r for r in caplog.records if "slow_query" in r.message]
        expect(warning_records)
        logged_stmt = warning_records[0].__dict__.get("statement", "")
        expect(len(logged_stmt) <= 200)
