# Changelog

This file records notable changes to Escalane. It follows
[Keep a Changelog](https://keepachangelog.com/en/1.0.0/) and
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- Redesigned the operator console, responder page and static demo
  ("dark cockpit"). Chrome is neutral, red marks
  only triggered alarms, amber only acknowledged ones, and closed alarms stay
  unlit. The interface uses self-hosted Atkinson Hyperlegible fonts (SIL
  OFL), and phone layouts were reworked for the worklist and acknowledgement.
- Moved the application to a root `src/escalane` package with explicit
  packages for configuration, persistence, security, providers, features,
  web routes, and workers.
- Moved migrations and tests to repository-level directories and made the
  root package the basis for CI, Docker builds, type checking, import-boundary
  checks, and release validation.
- Decoupled feature inputs from HTTP requests by using application commands.
  Moved notification workflows and webhook transport into dedicated modules.
- Moved alarm, master-data, policy, audit, and readiness queries and
  transactions out of the web routes and into the `alarms`, `configuration`,
  and `operations` features. The architecture check now rejects SQL, session
  calls, HTTP framework imports outside `web`, and imports of private names
  across modules.
- Target webhooks and signed state webhooks now share address resolution and
  one pinned-address transport. Each path keeps its own audit, metric, and
  retry behavior. The escalation-step and acknowledgement-note rules moved
  from the worker into `notifications`.
- Replaced the `contracts` package and `config/constants.py` with types and
  constants owned by their features. Moved the metrics registry into a
  `telemetry` package.
- CI, pre-commit, and the installed-wheel smoke check now run the Makefile
  targets, so local and CI verification share one definition. Pull requests
  also build and validate the Pages demo.
- Updated pinned `pyjwt` to 2.15.1 and `urllib3` to 2.8.0 to clear
  pip-audit advisories.

### Removed

- Removed the nested service package, duplicate webhook implementation,
  obsolete worker and route facades, and unused internal exports.
- Removed four exception classes that nothing raised (`ConnectorError`,
  `RateLimitError`, `AuthenticationError`, `AuthorizationError`), along with
  their unreachable handlers, two unused page-size constants, and the
  unreferenced `design-preview/` directory.

### Fixed

- Fixed worker startup when ARQ reads Redis settings directly from the worker
  class. Settings still load lazily and use the configured Redis connection.
- Configuration edit forms now show stored values. They previously rendered
  empty, so saving a record could clear its optional fields.
- Unknown alarms and other missing records on console and responder routes
  now render the localized error page instead of raw JSON. API routes keep
  JSON errors.
- Acknowledge, resolve, cancel and note confirmations now appear on the alarm
  detail page instead of on the next worklist visit. Bulk, note and
  acknowledgement messages no longer show raw message keys.

## [0.4.0-alpha.1]

### Added

- English and German operator and responder pages, rendered on the server,
  with Redis sessions and CSRF protection. Added configuration, system,
  simulation, and activity views.
- Versioned master data with redacted administrative audit events.
- A transactional outbox that records alarm events, recovers them in order,
  and preserves job identifiers across retries.
- Public contribution, security-reporting, support, and release guidance.

### Changed

- Renamed the product, Python package, container image, logger, metrics, and
  release identifiers from Alarm Broker to Escalane.
- Packaged the Jinja templates and same-origin browser assets in the wheel.
- Made migrations, the API, and the worker use the same image in Compose,
  with optional pinning by digest.
- Made readiness checks fail until PostgreSQL and Redis are available and
  the database matches the packaged Alembic migration head.

### Security

- Restricted seed placeholders and rejected YAML aliases, cycles, excessive
  nesting, and excessive node counts.
- Required validated HTTPS endpoints for enabled Zammad, SendXMS, and signed
  callback delivery, with exact webhook host allowlisting.
- Restricted `BASE_URL`, hardened trusted-proxy handling, and prevented raw
  provider errors or credential-bearing URLs from entering logs and audit
  records.
- Added compare-and-set lifecycle transitions, safe keyset pagination,
  token-independent device identifiers, versioned administration writes, and
  atomic soft deletion.

### Removed

- Removed legacy package, image-variable, logger, and metric aliases.
- Removed unused connector settings, obsolete template loaders, and dead event
  publisher branches.

## [0.2.0] - 2026-04-19

### Added

- Database pool and slow-query settings.
- Request ID propagation from HTTP responses into alarm metadata.
- CSV export formula-injection protection.
- A multi-stage runtime image and PostgreSQL/Alembic package smoke checks.

### Changed

- Added optimistic lifecycle and trigger-idempotency handling.
- Moved browser session state to Redis and made cookie security settings
  follow the request’s HTTP or HTTPS scheme.
- Applied validation limits to editable alarm fields and enforced type checks
  in CI.

### Fixed

- Excluded soft-deleted alarms from queries, exports, dashboards, and bulk
  operations.
- Corrected response headers, status serialization, and packaged template
  discovery.

## [0.1.0] - 2024-01-15

### Added

- Alarm intake and lifecycle APIs, an operator interface, responder
  acknowledgement, SendXMS, Signal, Zammad and webhook delivery, rate limiting,
  source allowlisting, seed imports, health checks, and simulation support.
