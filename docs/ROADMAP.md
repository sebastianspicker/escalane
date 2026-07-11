# Roadmap

## Guiding Principles

- No breaking changes to existing public endpoints.
- Stability and API consistency take precedence over feature breadth.
- UI improvements are additive and compatible with existing paths.

## Backlog

1. Streaming export — `GET /v1/alarms/export` currently buffers up to 2,000 rows in memory before writing the response. Implement true server-side streaming to handle larger exports without memory pressure.
2. Runtime verification — keep Docker image build, PostgreSQL/Alembic smoke checks, served HTTP E2E, lint, type checking, security audit, and package build green for release-candidate handoff.
3. Operator hardening — continue tightening health, notification, and webhook observability so operators can distinguish full success from best-effort or partial-failure paths.
4. Browser release matrix — execute the committed Playwright flows in Chromium, Firefox, and WebKit and complete manual VoiceOver/Safari and NVDA/Firefox checks.

## Completed in the 2026-07-11 console hardening

- Bilingual Jinja operator shell and mobile responder flow with same-origin packaged assets and strict CSP.
- Named Redis sessions, CSRF-protected browser mutations, deep-linkable alarm detail, safe polling, bulk/export workflows, and localized recovery pages.
- Versioned master data, masked sensitive edits, redacted audit events, default-policy and transactional import workflows, plus dedicated System, Simulation, and Activity views.
- Headless Chrome rendering at mobile and desktop viewports with no page overflow, CSP violations, external requests, or console errors.
- Public-repository hygiene enforcement for credentials, local tooling workspaces, generated browser output, build artifacts, and machine-specific paths.

## Current local verification status

The 2026-07-11 hardening pass completed Ruff, strict mypy, Bandit,
project-scoped dependency audit, the served HTTP E2E path, wheel packaging, and
the non-E2E suite at 93.86% coverage. Repository hygiene, package-resource, and
documentation-link checks also pass on the cleaned local candidate.

Docker build, PostgreSQL migration smoke, the three-engine Playwright matrix,
and manual screen-reader checks remain external or environment-dependent release
evidence; they are not implied by the local checks above.

## Release closure criteria

- Consolidated documentation in `docs/` with `docs/README.md` as index.
- Unified Notes route and stable simulation endpoints with tests.
- UI flows without known runtime errors in core paths.
- Full green quality gates (lint + tests + mypy + coverage ≥ 93%).
- Clean layer boundaries (zero backwards imports).
- Zero deprecation warnings in test output.
- Public hygiene check passes with no tracked private or generated artifacts.
