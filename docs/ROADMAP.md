# Roadmap

## Guiding Principles

- No breaking changes to existing public endpoints.
- Stability and API consistency take precedence over feature breadth.
- UI improvements are additive and compatible with existing paths.

## Backlog

1. Streaming export — `GET /v1/alarms/export` currently buffers up to 2,000 rows in memory before writing the response. Implement true server-side streaming to handle larger exports without memory pressure.
2. Runtime verification — keep Docker image build, PostgreSQL/Alembic smoke checks, served HTTP E2E, lint, type checking, security audit, and package build green for release-candidate handoff.
3. Operator hardening — continue tightening health, notification, and webhook observability so operators can distinguish full success from best-effort or partial-failure paths.

## Definition of Done

- Consolidated documentation in `docs/` with `docs/README.md` as index.
- Unified Notes route and stable simulation endpoints with tests.
- UI flows without known runtime errors in core paths.
- Full green quality gates (lint + tests + mypy + coverage ≥ 93%).
- Clean layer boundaries (zero backwards imports).
- Zero deprecation warnings in test output.
