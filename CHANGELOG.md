# Changelog

This file records notable changes to Escalane. It follows
[Keep a Changelog](https://keepachangelog.com/en/1.0.0/) and
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- Moved the application to a root `src/escalane` package with explicit
  packages for configuration, persistence, security, providers, features,
  web routes, and workers.
- Moved migrations and tests to repository-level directories and made the
  root package the basis for CI, Docker builds, type checking, import-boundary
  checks, and release validation.
- Decoupled feature inputs from HTTP requests by using application commands.
  Moved notification workflows and webhook transport into dedicated modules.

### Removed

- Removed the nested service package, duplicate webhook implementation,
  obsolete worker and route facades, and unused internal exports.

### Fixed

- Fixed worker startup when ARQ reads Redis settings directly from the worker
  class. Settings still load lazily and use the configured Redis connection.

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
