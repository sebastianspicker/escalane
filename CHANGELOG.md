# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Removed
- Unused settings/env examples for `SENDXMS_MODE`, `WEBHOOK_MAX_RETRIES`, `WEBHOOK_RETRY_DELAY_SECONDS`, and `SIMULATION_SEED_URL`
- Dead `alarm.resolved` / `alarm.cancelled` event publisher helpers and no-op worker dispatch branches

## [0.2.0] - 2026-04-19

### Added in 0.2.0
- `DB_POOL_SIZE`, `DB_MAX_OVERFLOW`, `DB_POOL_TIMEOUT`, `DB_POOL_RECYCLE` settings for connection pool tuning
- `SLOW_QUERY_LOG_MS` setting; SQLAlchemy slow-query listener logs WARNINGs for queries exceeding threshold
- `request_id` propagation: stored in `alarm.meta` at trigger time, readable from `X-Request-ID` response header
- Extended test coverage: 95 new tests targeting errors, event_service, notification_service, trigger_service, and worker/tasks branches
- `.env.example` entries for all new settings
- Externalized Admin UI HTML template for better maintainability
- Dockerfile multi-stage build for optimized image size
- `pytest-cov` for coverage measurement in the Python service
- `EnrichedAlarmContext` and `NotificationPayload` TypedDicts for type-safe internal data
- Expanded test coverage across services, connectors, worker tasks, packaging, and smoke paths
- `[tool.mypy]` configuration with strict checks in pyproject.toml
- CSV export formula injection protection
- `services/message_formatter.py` (canonical location for alarm message formatting)
- `services/metrics_queries.py` (DB queries for Prometheus metrics)

### Changed
- Coverage threshold raised from 89% → 93% (actual: 93.48%)
- `create_async_engine_from_url` accepts pool and slow-query-log parameters
- `process_trigger` and `create_alarm` accept optional `request_id` for tracing
- POST create endpoints (devices, escalation policy) now return 201 Created
- Added docstrings to all API endpoints for OpenAPI documentation
- Split `alarms.py` (781 lines) into `alarms.py`, `alarm_operations.py`, `alarm_notes.py`
- Service layer now uses domain exceptions (`ConflictError`, `NotFoundError`, `ValidationError`) instead of `HTTPException`
- mypy type checking enforced in CI (removed `|| true`)
- `core/metrics.py` decoupled from `db/models` — accepts data as parameters
- Fixed coverage measurement: `concurrency = ["greenlet", "thread"]` for async tracing
- Test imports use try/except pattern for directory-independent execution
- `AlarmPatchSchema` now enforces length limits on title, description, tags
- Improved code deduplication in API routes
- Refactored webhook logic into separate functions for better testability
- CI now includes PostgreSQL + Alembic smoke coverage and a wheel packaging smoke import

### Fixed
- Fixed soft-deleted alarms appearing in list, export, dashboard, and bulk operations
- Fixed export_alarms Content-Disposition header on StreamingResponse
- Fixed hardcoded status strings in admin_ui.py to use AlarmStatus enum
- Fixed 54 mypy type errors across 20 files
- Fixed `seed.py` type inference with entity-specific variable names
- Fixed `settings.py` AnyHttpUrl/str type mismatch
- Fixed `mock.py` class-level type annotations
- Fixed broken documentation links
- Removed duplicate PUT /devices endpoint
- Fixed trigger idempotency races and retry-safe event recovery after alarm creation
- Fixed local HTTP admin/ACK browser flows by making cookie security scheme-aware and moving UI state to Redis
- Fixed packaged wheel imports by shipping admin and ACK HTML templates as package data

## [0.1.0] - 2024-01-15

### Added in 0.1.0
- Initial release
- Alarm management API (create, list, acknowledge)
- Admin UI for alarm management
- Notification connectors: SendXMS (SMS), Signal, Zammad
- Webhook notifications
- Idempotency handling
- Rate limiting
- IP allowlist
- Seed data loading
- Health and readiness checks
- Alarm simulation for testing
- FastAPI REST API with PostgreSQL (SQLAlchemy), Redis, arq worker, Alembic migrations
