# Roadmap

## Guiding Principles

- No breaking changes to existing public endpoints.
- Stability and API consistency take precedence over feature breadth.
- UI improvements are additive and compatible with existing paths.

## Completed

### Phase 0: Baseline
- Lint and test baseline established.
- API and UI regressions covered by dedicated tests.

### Phase 1: Bug Fixes
- Fixed double-retry in connector base (3x3=9 retries -> 3).
- Fixed Signal dispatch routing through SMS handler.
- Fixed hardcoded Zammad values; now read from connector config.
- Removed unused TypeVar.

### Phase 2: Repository & Documentation Cleanup
- Removed tracked screenshots from git, added to `.gitignore`.
- Fixed `.DS_Store` gitignore pattern.
- Consolidated docs: merged INSTALL+DEVELOPMENT into SETUP, removed DATA_MODEL/DEMO_SCREENSHOTS/TROUBLESHOOTING.
- Translated German code comments and docstrings to English.

### Phase 3: Code Deduplication
- Unified connector naming: renamed `*Connector` classes to canonical `*Client`, removed backward-compat aliases.
- Removed `send_escalation_step` forwarding wrapper; callers use `send()` directly.
- Deduplicated Zammad ticket payload construction via `_build_notification_payload`.
- Fixed latent `ticket_id=` keyword arg bug in `_log_notification_result` call.

### Phase 4: Settings Refactoring
- Removed 9 unused group setting classes and 8 `@property` methods.
- Flat `Settings` class with validators and convenience `is_*_enabled()` methods.
- Reduced from ~397 lines to ~121 lines.

### Phase 5: Code Quality
- Moved `import httpx` from function-level to module-level in notification service.
- Replaced manual webhook retry loop with `tenacity` decorator.

### Phase 6: UI Improvements
- Unified ACK page design system (Inter + JetBrains Mono fonts, consistent CSS variables, aligned colors).
- Replaced `window.prompt()` with accessible inline confirmation modal (name + optional note).
- Added loading states on action buttons (disable + "..." text during async calls).
- Added success toast with auto-reload after 1.5s.
- Added empty state message when search filter matches no alarms.

### Phase 7: Final Validation
- All 49 tests pass, ruff clean.
- Added targeted regression test: connector retry count (exactly 3 attempts).

### Phase 8: Code Quality & Refactoring
- Split `alarms.py` (781 lines) into three focused modules: `alarms.py` (268), `alarm_operations.py` (357), `alarm_notes.py` (59).
- Added `EnrichedAlarmContext` and `NotificationPayload` TypedDicts in `types.py`.
- Decoupled service layer from FastAPI: replaced all `HTTPException` in services with domain errors (`ConflictError`, `NotFoundError`, `ValidationError`).

### Phase 9: Testing & Coverage
- Added `pytest-cov` to dev dependencies with coverage configuration.
- Added 65 new tests across 6 test files (49 → 114 total).
- Achieved 71% coverage (up from 65% baseline).
- Enforced 70% coverage threshold in CI.

### Phase 10: Security & Type Safety
- Fixed all 54 mypy type errors across 20 files.
- Added `[tool.mypy]` configuration with strict checks.
- Removed `|| true` from CI mypy step — type errors now fail the build.
- Bandit and pip-audit remain clean.

### Phase 11: Layer Violation Fixes (Round 2)
- Moved `format_alarm_message` from `worker/message.py` to `services/message_formatter.py`.
- Decoupled `core/metrics.py` from `db/models` — DB queries extracted to `services/metrics_queries.py`.
- Documented `policy_service.py` schema import as acceptable coupling.
- Removed misleading "deprecated" labels from event_service convenience wrappers.

### Phase 12: Coverage Deep Dive (Round 2)
- Added 24 new tests (114 → 138 total) across 3 test files.
- `notification_service.py` coverage: 36% → 71%.
- `worker/tasks.py` coverage: 37% → 58%.
- Connector coverage: sendxms 100%, signal 100%, zammad 94%.
- Raised coverage threshold from 70% to 75% (actual: 77%).

### Phase 13: Security Hardening (Round 2)
- CSV export formula injection protection (`_sanitize_csv_value`).
- Input validation tightening: `AlarmPatchSchema` title (500), description (5000), tags (20 items).

### Phase 14: Database & Performance (Round 3)
- Added Alembic migration 0005 with indexes on alarms.status, alarms.created_at, alarms.severity, alarm_notifications.alarm_id, alarm_notifications.channel.
- Fixed hardcoded status strings in admin_ui.py to use AlarmStatus enum.

### Phase 15: API Polish (Round 3)
- POST create endpoints now return 201 Created.
- Added missing docstrings to all API endpoints.
- Fixed export_alarms Content-Disposition header on StreamingResponse.

### Phase 16: Coverage & Quality (Round 3)
- Added 22 new tests (138 → 160 total) across 3 test files.
- Coverage: 77% → 81%. Threshold raised to 80%.
- Bulk operations, cursor pagination, CSV sanitization, admin dashboard, health checks now tested.

### Phase 17: Test Robustness (Round 4)
- Extracted FakeRedis and trigger_alarm to `tests/helpers.py` with try/except import pattern.
- Tests now pass from both `services/alarm_broker/` and repo root.
- Suppressed pytest-asyncio deprecation warning via `asyncio_default_fixture_loop_scope`.

### Phase 18: Code Simplification (Round 4)
- Applied ruff SIM rules: ternary operators, `any()` generator.
- Documented German localization strings as intentional customer requirement.

### Phase 19: Coverage Push (Round 4)
- Fixed coverage measurement: added `concurrency = ["greenlet", "thread"]` to trace async handlers.
- 28 new tests (160 → 188 total). Coverage: 81% → 89%. Threshold: 85%.
- admin_ui.py 43%→97%, alarm_operations.py 52%→97%, alarms.py 59%→98%.

### Phase 20: Soft-Delete Filtering (Round 5)
- Added `Alarm.deleted_at.is_(None)` filter to all query entry points (list, export, dashboard, bulk ops).
- Soft-deleted alarms no longer appear in any query results.

### Phase 21: Coverage to 92% (Round 5)
- 9 new tests targeting worker/tasks.py (58%→89%): alarm_created flow, escalate, alarm_acked.
- Coverage: 89% → 92%. Threshold raised to 90%.

### Phase 22: Full Improvement Sweep (Round 6)

#### Coverage push (89.72% → 93.48%)
- Added `test_core_utils.py` error-class tests: covers all constructors and `to_dict()` variants.
- Created `test_event_service.py`: 9 tests for all three enqueue wrappers (success + exception branches).
- Created `test_notification_dispatch_extended.py`: 22 tests for channel dispatch (disabled targets, SSRF, exception paths, `_build_*` helpers).
- Created `test_trigger_service_unit.py`: 31 unit tests for TriggerService internals (idempotency, rate-limit, device validation, lock release, retry exhaustion, process_trigger orchestration).
- Created `test_worker_tasks_extended.py`: 13 tests for worker task branches (resolved/cancelled events, missing alarm_id/event_type, alarm not in DB, SSRF-blocked webhook, webhook-disabled early return).
- Coverage threshold raised from 89% → 93%.

#### Distributed tracing (Phase F)
- `process_trigger` and `create_alarm` in `trigger_service.py` now accept `request_id` parameter.
- `request_id` from API middleware is stored in `alarm.meta["request_id"]` at trigger time.
- Yealink route passes `request.state.request_id` to `process_trigger`.

#### Query performance logging (Phase G)
- Added `SLOW_QUERY_LOG_MS` setting (default 200 ms).
- `db/engine.py` installs SQLAlchemy `before_cursor_execute`/`after_cursor_execute` listeners emitting `slow_query` WARNING log when threshold is exceeded.

#### Connection pool tuning (Phase I)
- Added `DB_POOL_SIZE`, `DB_MAX_OVERFLOW`, `DB_POOL_TIMEOUT`, `DB_POOL_RECYCLE` settings.
- Wired into `create_async_engine` in `db/engine.py`.
- Documented with production-safe defaults in `.env.example`.

## Backlog

1. ~~Extended search/filter options for admin operations.~~ ✅ Already implemented (person_id, room_id, device_id, created_after, created_before, severity filters on `GET /v1/alarms`).
2. ~~Distributed tracing (propagate request_id to worker tasks).~~ ✅ Phase 22.
3. ~~Query performance logging.~~ ✅ Phase 22.
4. ~~Connection pool tuning for production deployments.~~ ✅ Phase 22.
5. Streaming export — `GET /v1/alarms/export` currently buffers up to 2 000 rows in memory before writing the response. Implement true server-side streaming to handle larger exports without memory pressure.
6. ~~IP allowlist hardening — `YELK_IP_ALLOWLIST` defaults to blank (all IPs accepted). Add a startup `UserWarning` in `Settings` when the allowlist is empty and `simulation_enabled` is `False`.~~ ✅ Implemented (`warn_empty_ip_allowlist` model validator in `settings.py`).
7. ~~Trigger response normalisation — unknown-token (404) vs. incomplete-mapping (409) leaks token validity to callers. Normalise both to a single 404 to prevent token-probing.~~ ✅ Implemented (always returns 404 in `trigger_service.py`).

## Definition of Done

- Consolidated documentation in `docs/` with `docs/README.md` as index.
- Unified Notes route and stable simulation endpoints with tests.
- UI flows without known runtime errors in core paths.
- Full green quality gates (lint + tests + mypy + coverage ≥ 93%).
- Clean layer boundaries (zero backwards imports).
- Zero deprecation warnings in test output.
