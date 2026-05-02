# Roadmap History

This file keeps completed implementation history out of the active roadmap while
preserving useful release context for maintainers.

## Completed Phases

### Phase 0: Baseline
- Lint and test baseline established.
- API and UI regressions covered by dedicated tests.

### Phase 1: Bug Fixes
- Fixed connector retry multiplication.
- Fixed Signal dispatch routing through the SMS handler.
- Fixed hardcoded Zammad values.
- Removed unused TypeVar.

### Phase 2: Repository and Documentation Cleanup
- Removed tracked screenshots from git and ignored regenerated screenshots.
- Fixed the `.DS_Store` ignore pattern.
- Consolidated setup/development documentation.
- Translated code comments and docstrings to English.

### Phase 3: Code Deduplication
- Unified connector naming.
- Removed a forwarding wrapper around escalation dispatch.
- Deduplicated Zammad ticket payload construction.
- Fixed a latent `ticket_id=` keyword argument bug.

### Phase 4: Settings Refactoring
- Removed unused grouped settings and derived properties.
- Flattened `Settings` while keeping validators and convenience methods.

### Phase 5: Code Quality
- Moved a function-local `httpx` import to module scope.
- Replaced a manual webhook retry loop with `tenacity`.

### Phase 6: UI Improvements
- Unified ACK page styling.
- Replaced prompt-based confirmation with an inline modal.
- Added loading states, success toast, and dashboard empty state.

### Phase 7: Final Validation
- Baseline was green with 49 tests and ruff clean.
- Added a connector retry regression test.

### Phase 8: Code Quality and Refactoring
- Split the large alarms route module into focused modules.
- Added typed notification/enrichment payloads.
- Moved service-layer errors away from FastAPI exceptions.

### Phase 9: Testing and Coverage
- Added pytest coverage tooling.
- Added 65 tests and raised coverage threshold to 70%.

### Phase 10: Security and Type Safety
- Fixed mypy errors and made type checking fail CI.
- Bandit and pip-audit were clean at the time.

### Phase 11: Layer Violation Fixes
- Moved alarm message formatting into the service layer.
- Decoupled metrics from DB models.
- Clarified accepted policy-service schema coupling.

### Phase 12: Coverage Deep Dive
- Added tests for notification, worker, and connector paths.
- Raised coverage threshold to 75%.

### Phase 13: Security Hardening
- Added CSV formula-injection protection.
- Tightened alarm patch input validation.

### Phase 14: Database and Performance
- Added performance indexes via Alembic migration 0005.
- Replaced hardcoded admin UI status strings with the enum.

### Phase 15: API Polish
- Create endpoints return `201 Created`.
- Added endpoint docstrings.
- Fixed export response `Content-Disposition`.

### Phase 16: Coverage and Quality
- Added tests for bulk operations, cursor pagination, CSV sanitization,
  dashboard rendering, and health checks.
- Raised coverage threshold to 80%.

### Phase 17: Test Robustness
- Extracted shared test helpers.
- Tests run from both repo root and `services/alarm_broker/`.
- Suppressed pytest-asyncio loop-scope deprecation noise.

### Phase 18: Code Simplification
- Applied ruff SIM simplifications.
- Documented intentional German localization strings.

### Phase 19: Coverage Push
- Fixed async coverage measurement.
- Added 28 tests and raised coverage threshold to 85%.

### Phase 20: Soft-Delete Filtering
- Added `Alarm.deleted_at.is_(None)` filtering across query entry points.

### Phase 21: Coverage to 92%
- Added worker task tests for alarm creation, escalation, and ACK handling.
- Raised coverage threshold to 90%.

### Phase 22: Full Improvement Sweep
- Added broad tests for errors, event enqueueing, notification dispatch,
  trigger internals, and worker branches.
- Raised coverage threshold to 93%.
- Propagated request IDs into trigger-created alarm metadata.
- Added slow-query logging.
- Added database pool tuning settings and documentation.
