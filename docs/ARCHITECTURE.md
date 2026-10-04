# Architecture

Escalane ships as one Python package but runs in three roles: an Alembic
migration process, a FastAPI web process, and an ARQ worker. All three use the
same installed package and container image. The application also depends on
PostgreSQL, Redis, and whichever notification providers a deployment enables.

This repository is a reference implementation for alarm intake and escalation.
It is neither a general incident-management platform nor a validated
emergency-response system.

## System context

```mermaid
flowchart LR
    Device[Alarm device] -->|Token-authenticated trigger| API[FastAPI web process]
    Operator[Operator browser] -->|Session and CSRF| API
    Responder[Responder browser] -->|Acknowledgement capability| API
    API <--> PostgreSQL[(PostgreSQL)]
    API <--> Redis[(Redis and ARQ)]
    Redis --> Worker[ARQ worker]
    Worker <--> PostgreSQL
    Worker --> Zammad[Zammad]
    Worker --> SendXMS[SendXMS]
    Worker --> Signal[Signal REST bridge]
    Worker --> Webhook[Signed callback or webhook target]
    StaticDemo[Generated static demo] -. No runtime connection .-> Operator
```

Only the API accepts HTTP requests. The worker creates its own database engine,
HTTP client, provider clients, and Redis connection. In the reference Compose
deployment, the migration process updates the schema before either the API or
worker starts.

## Components

| Component | Responsibility |
|---|---|
| `config/` | Loads environment settings (`settings.py`) and defines the application exceptions that web handlers map to HTTP responses (`errors.py`) |
| `telemetry/` | Holds the process-local metrics registry and Prometheus rendering, and instruments database connection pools |
| `persistence/` | Defines the SQLAlchemy schema (including the persisted `AlarmStatus`), engines, and sessions |
| `security/` | Validates device source IPs, outbound URLs, and webhook host allowlists |
| `runtime/` | Provides atomic Redis primitives and rate-limit keys |
| `providers/` | Transports requests to Zammad, SendXMS, Signal, and pinned webhook addresses, plus the simulation mocks |
| `alarms/` | Owns triggers and idempotency, lifecycle transitions, notes, bulk changes, the ordered outbox and its event types, alarm queries and exports, worklist counts, and history |
| `configuration/` | Owns master data, device upserts, escalation policy, seed import, and redacted administrative audit records |
| `notifications/` | Owns escalation targets, payloads, delivery workflows, the shared webhook address resolution, audit records, retry classification, and recovery |
| `operations/` | Provides readiness probes, metrics aggregates, and worker heartbeat snapshots |
| `web/` | Inbound HTTP adapter: application assembly, error mapping, authentication, routes, console helpers, Jinja rendering, translations, and assets |
| `worker/` | Inbound ARQ adapter: task registration, retry translation, escalation scheduling, and recovery jobs |
| `migrations/` | Stores Alembic schema history; current code expects the packaged migration head |
| `pages/` | Contains the source for the separate, disconnected GitHub Pages demo |

Paths in the table are relative to `src/escalane/` unless shown otherwise.

## Dependency rules

`scripts/check_architecture.py` enforces the package boundaries. It rejects
dependencies that are absent from the table below, imports from removed or
unknown namespaces, and dependency cycles.

| Package | May import from |
|---|---|
| `config`, `runtime`, `security`, `telemetry` | No other Escalane package |
| `persistence` | `config`, `telemetry` |
| `providers` | `security` |
| `configuration` | `config`, `persistence` |
| `operations` | `persistence`, `telemetry` |
| `alarms` | `config`, `persistence`, `runtime`, `telemetry` |
| `notifications` | `alarms`, `config`, `persistence`, `providers`, `security`, `telemetry` |
| `web` | `alarms`, `config`, `configuration`, `operations`, `persistence`, `providers`, `runtime`, `security`, `telemetry` |
| `worker` | `alarms`, `config`, `notifications`, `operations`, `persistence`, `providers`, `security`, `telemetry` |

The checker does not restrict imports within the same package. `web` and
`worker` are the two inbound adapters, so feature packages must not import
either one. The checker also enforces three adapter rules:

- Only `web` may import FastAPI or Starlette.
- `web` and `worker` modules may hold an `AsyncSession` (a parameter annotated
  `AsyncSession` or named `session`), but they may not import SQLAlchemy beyond
  `sqlalchemy.ext.asyncio` (the worker also imports `sqlalchemy.exc` to classify
  retryable errors) or call session data methods such as `execute`, `add`, or
  `commit`. Queries and transactions belong to feature functions.
- No module may import another module's underscore-prefixed names.

`notifications` deliberately writes the delivery state it owns on shared rows,
such as restoring `alarms.zammad_ticket_id` and re-arming outbox ACK events
during recovery; this is notification delivery state, not alarm lifecycle.

Keep HTTP parsing, cookies, rendering, and response formatting in `web`. Keep
ARQ task signatures, retries, and scheduling in `worker`, and keep calls to
external systems in `providers`.

## Alarm and delivery flow

```mermaid
sequenceDiagram
    participant D as Device or operator
    participant A as FastAPI web process
    participant P as PostgreSQL
    participant R as Redis and ARQ
    participant W as ARQ worker
    participant X as External provider

    D->>A: Trigger or lifecycle action
    A->>R: Reserve idempotency or rate-limit state
    A->>P: Commit alarm change, lifecycle event, and outbox row
    A->>R: Enqueue ordered outbox event
    R->>W: Deliver ARQ job
    W->>P: Load current alarm and event state
    W->>X: Send notification or callback
    W->>P: Record delivery outcome
    W->>R: Schedule escalation when required
    W->>P: Scan unpublished events during recovery
    W->>R: Re-enqueue recoverable event
```

When Escalane creates or changes an alarm, it writes the lifecycle event and
outbox row in the same PostgreSQL transaction. It publishes each alarm's events
in order. Once ARQ accepts an event, Escalane marks the corresponding outbox row
as published. If enqueueing fails, the row stays in PostgreSQL for the worker to
recover at startup or during its once-per-minute recovery job.

The worker reloads the stored state before it acts. Initial notifications and
escalations may be retried, which makes calls to providers at least once rather
than exactly once. Stable job identities, current-state checks, retry
classification, and delivery records reduce duplicate effects, but a provider
can still receive the same request more than once.

## State ownership

| Store | What it stores | What happens during recovery |
|---|---|---|
| PostgreSQL | Master data, escalation policy and targets, alarms, notes, lifecycle events, administrative audit, notification audit, and ordered outbox | This is the durable source of truth. Back up and restore it consistently. |
| Redis | ARQ jobs, delayed work, browser sessions, CSRF/session data, trigger reservations, idempotency state, and rate limits | This data coordinates current work. A restart invalidates sessions and requires a tested queue recovery procedure. |
| Process memory | Simulation notification records and mock ticket counter | This data disappears with its process and is not shared between API and worker processes. |

If Redis restarts or ARQ retries a job, PostgreSQL remains the source of truth.
Simulation records exist only for the demonstration flow and cannot establish
that a provider received a delivery. Because the worker records simulated
deliveries in its own memory, `/v1/simulation/notifications` and the console's
simulation page show them only when the API and worker run in the same
process. With the separate API and worker containers of the reference Compose
deployment, the list stays empty.

## HTTP and trust boundaries

| Endpoint | Access control |
|---|---|
| `/healthz`, `/readyz` | Public liveness and dependency/schema readiness |
| `/v1/yealink/alarm` | Per-device token, Redis-backed limit, and source allowlist outside simulation |
| `/a/{ack_token}` | Bearer capability; responses are marked no-store |
| `/v1/alarms`, `/v1/admin`, `/metrics`, `/healthz/details` | Static `X-Admin-Key` comparison |
| `/admin/*` | Admin-key login exchanged for a one-hour Redis session; mutating forms require CSRF |
| `/v1/simulation/*` | Admin key and simulation mode; routes return 404 when simulation is disabled |

Escalane trusts forwarded client and scheme headers only when the immediate peer
matches a configured proxy. It validates non-loopback `BASE_URL` values and
enabled provider URLs against their HTTPS and host-allowlist rules. Browser
assets remain on the same origin, and the application does not configure CORS.

## Configuration and deployment

Runtime settings live in `src/escalane/config/settings.py`. Process environment
variables take precedence over the optional `.env` file at the repository root,
and `.env.example` documents the available settings. On startup, Escalane
validates production credentials, source allowlists, whether simulation is
restricted to loopback, provider endpoints, and webhook controls.

The Dockerfile installs both the package and its migrations into one non-root
image. `deploy/docker-compose.yml` runs that image in the migration, API, and
worker roles alongside PostgreSQL and Redis. The API and worker wait for a
successful migration. This Compose setup is a reference deployment and does
not provide TLS termination, external monitoring, scheduled backups, or managed
secrets.

CI builds a wheel to verify package integrity, while the tested container image
is the supported release artifact. The `pages/` source builds separately into
the ignored `build/pages/` directory and may be published to GitHub Pages. The
static site never connects to a running Escalane service.

## Extending the application

- Put a new query or state change in the feature that owns the data (`alarms/`,
  `configuration/`, or `notifications/`). Feature functions raise
  `config.errors` exceptions or their own exception types, never HTTP
  exceptions.
- Add HTTP routes under `web/routes/` and register them explicitly in
  `ALL_ROUTERS`. Registration order matters where a literal path shares a
  prefix with a parameterized one. Shared console rendering and session helpers live in `web/console.py`.
- Put new provider transports behind the narrow protocols in `providers/`, and
  leave notification policy in `notifications/`.
- Register new worker behavior as ARQ functions without changing existing
  payload formats or job IDs.
- Represent each schema change with a new Alembic revision, update
  `EXPECTED_ALEMBIC_HEAD` in `operations/readiness.py`, and apply the revision
  before starting API or worker code that depends on it.
- Preserve existing HTTP routes, worker payloads, database schema, provider
  behavior, packaged templates and assets, and operator workflows unless a
  change explicitly updates them.

The [Setup](SETUP.md), [Operations](OPERATIONS.md),
[Integrations](INTEGRATIONS.md), and [Frontend](FRONTEND.md) guides cover the
corresponding developer and operator tasks.
