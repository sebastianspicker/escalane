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
- Moved migrations to a repository-level directory and made the
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

- The worklist search now treats `%` and `_` as plain characters. Searching
  `door_` no longer also lists `dooropen`.
- `make package-check` now stops when it cannot create its temporary
  directory, instead of building into the repository root.
- A short provider outage no longer drops a notification. The worker now
  retries a failed delivery up to 12 times over about six minutes instead of
  giving up after about 15 seconds. Initial notifications, like escalation
  steps, stop once the alarm has been acknowledged, resolved or cancelled; the
  Zammad ticket is still created.
- A re-armed acknowledgement note now reaches Zammad within about ten
  minutes. ARQ kept each finished event job's result for an hour under the
  same job ID and refused the re-armed event until it expired.
- Acknowledgement links opened at the same time no longer invalidate each
  other. Each link now keeps its own `ack_csrf` cookie, scoped to that link.
- An invalid entry in `YELK_IP_ALLOWLIST` or `TRUSTED_PROXY_CIDRS` now stops
  the API, worker and migration at startup and names the entry. Before, one
  bad allowlist entry made every device trigger fail with 403, and a bad
  proxy entry was silently ignored.
- The seed import now rejects an escalation target whose `channel` is not
  `email`, `sms`, `signal` or `webhook`, like the API and the console. Before,
  such a target was saved and then skipped at alarm time. Targets saved earlier
  with an unknown channel are now recorded as `permanent_error` instead of
  `skipped`.
- A non-ASCII admin key, CSRF value or import hash now gets the normal
  rejection instead of a server error. A wrong admin key with such characters
  now also counts toward the sign-in limit.
- Device triggers with an unknown token and with an inactive device now get
  the same 404 text, `Unknown or inactive device`, so a caller cannot tell
  them apart. The log keeps the exact reason.
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
- Live Signal delivery now uses the signal-cli-rest-api `/v2/send` contract
  (`number` and `recipients`). The new required `SIGNAL_SENDER_NUMBER` setting
  names the registered sender account.
- Zammad requests now authenticate with `Authorization: Token token=…`, the
  scheme Zammad uses for access tokens.
- `LOG_LEVEL` is now applied. API and worker events are written to stderr as
  JSON lines that keep their context fields, such as `request_id` and
  `alarm_id`; INFO events were previously dropped.
- The documented Compose commands and the Makefile docker targets now pass
  `--env-file .env`, so Compose reads the root `.env` instead of failing with
  "POSTGRES_PASSWORD must be set".
- `make bootstrap` accepts any CPython 3.14 release and only warns when it is
  not 3.14.7, the CI and container version. `.python-version` now names 3.14.7
  instead of 3.14.6.
- Simulated deliveries now appear on the console's simulation page and in
  `/v1/simulation/notifications` when the API and worker run in separate
  containers. Simulation records and the mock ticket counter are kept in Redis
  (newest 500) instead of process memory.
- Responders and operators now see the localized error page instead of raw
  JSON when an action conflicts with the alarm's current state (for example
  acknowledging an alarm that was already resolved), when submitted data is
  invalid, or when a form or query parameter fails validation. API routes keep
  their JSON errors. Error pages for failed console actions link back to the
  worklist.
- Acknowledging, resolving and cancelling an alarm now adds an entry with
  the actor and any note or cancel reason to the alarm's activity. These
  entries also appear in the alarm notes API with note types `ack`,
  `resolve` and `cancel`.
- Operator sessions no longer end 60 minutes after sign-in while the
  operator is active: console page views now renew the session cookie along
  with the server-side session. An idle console still signs out after 60
  minutes, and the worklist reloads to the sign-in prompt instead of showing
  stale data.
- Applying a configuration import now confirms the import and keeps the
  selected language.
- The responder link limit now applies per link (10 requests per minute per
  client address) with a ceiling of 120 per minute per address, so responders
  behind one NAT or proxy no longer share a single 10-per-minute budget.
- Outside simulation, `ADMIN_API_KEY` must now have at least 32 characters,
  and five failed `X-Admin-Key` attempts per client address within a minute
  are answered with HTTP 429, matching the console sign-in limit.
- Email escalation targets now send the message to the target's address as an
  outgoing email article on the alarm's Zammad ticket, instead of opening a
  separate ticket with an internal note that mailed nobody.

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
