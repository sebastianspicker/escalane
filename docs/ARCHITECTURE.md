# Architecture

Escalane is a FastAPI service with server-rendered operator pages, a
PostgreSQL data store, Redis-backed queues and sessions, and an ARQ worker.

## Runtime components

| Component | Responsibility |
|---|---|
| FastAPI API | Trigger intake, acknowledgement, admin API, operator pages, health, and metrics |
| PostgreSQL | Alarm state, configuration, audit data, and event outbox |
| Redis | ARQ jobs, browser sessions, idempotency, and rate limits |
| ARQ worker | Notification delivery, escalation scheduling, callbacks, and outbox recovery |
| Jinja assets | Server-rendered HTML, CSS, JavaScript, and SVG |
| Alembic | Ordered database schema migrations |

`deploy/docker-compose.yml` runs these as PostgreSQL, Redis, migration, API,
and worker services. Migration, API, and worker use one image reference.

## Trigger and delivery flow

1. `GET /v1/yealink/alarm` validates the source address, query parameter, and
   device token.
2. `TriggerService.process_trigger` loads the device context and creates or
   reuses the alarm transactionally.
3. The transaction records an ordered event in `alarm_event_outbox`.
4. The publisher enqueues `process_alarm_event` in Redis.
5. The worker sends initial notifications, schedules escalation steps, and
   records bounded delivery results.
6. Later state changes can enqueue follow-up delivery and a signed webhook
   callback.

The outbox publishes only the earliest pending event for each alarm. Recovery
runs once per minute to retry unpublished rows. External delivery is at least
once, so provider-side idempotency remains important.

## Acknowledgement flow

Each alarm has a unique acknowledgement token. `GET /a/{ack_token}` renders the
responder page. `POST /a/{ack_token}` validates the form and performs the
lifecycle transition. Possession of the token authorizes this route, so the
URL is a bearer capability and must not be logged or shared beyond the
responder.

Operator API and browser actions use separate admin-key or session
authorization paths.

## Alarm lifecycle

| State | Meaning |
|---|---|
| `triggered` | Active and not yet acknowledged |
| `acknowledged` | A responder or operator accepted the alarm |
| `resolved` | The incident was completed |
| `cancelled` | The alarm was cancelled |

Lifecycle changes use compare-and-set service methods and write audit events.
Deletion is a separate operator action and is not another lifecycle state.

## HTTP surfaces

Public functional routes cover liveness, readiness, device trigger, responder
acknowledgement, and operator sign-in. Packaged assets are served at
`/admin/assets/*`. Admin JSON routes use `X-Admin-Key`. Authenticated browser
routes use a Redis-backed session and CSRF tokens.

`ENABLE_API_DOCS=true` enables `/docs`, `/redoc`, and `/openapi.json`.

Router assembly is defined in
`services/escalane/escalane/api/routes/__init__.py`. Application construction,
middleware, assets, and lifespan resources are owned by
`services/escalane/escalane/api/main.py`.

## Data model

The models in `services/escalane/escalane/db/models.py` cover:

- sites, rooms, people, and devices
- escalation policies, steps, and targets
- alarms, notes, and notification audits
- ordered alarm event outbox rows

Schema history is authoritative in `services/escalane/alembic/versions/`.
`/readyz` verifies that the database is at Alembic revision `0007`.

## Package structure

```text
services/escalane/escalane/
├── api/             FastAPI app, routes, templates, and browser assets
├── connectors/      Zammad, SendXMS, Signal, mock, and connector interfaces
├── core/            Shared URL, network, and security helpers
├── db/              Engine, sessions, and SQLAlchemy models
├── services/        Lifecycle, seed, delivery, trigger, and outbox logic
├── worker/          ARQ tasks and worker settings
├── seed.py          Seed placeholder expansion
└── settings.py      Environment configuration and validation
```

The API layer handles transport concerns. Service modules own application
transactions and lifecycle rules. Connector modules translate delivery
requests. Worker modules own asynchronous orchestration and retry behavior.

## Failure behavior

- Empty admin credentials fail closed.
- Invalid runtime configuration prevents startup.
- `/healthz` reports process liveness only.
- `/readyz` returns HTTP 503 when PostgreSQL, Redis, or schema revision is not
  ready.
- Provider failures are recorded with bounded diagnostic categories rather
  than raw exception data.
- Pending outbox rows retain publish errors for later recovery.

## Observability

The API emits structured request logs with request IDs and masks
capability-bearing routes. Admin-protected `/metrics` reports application
counters. `/healthz/details` exposes dependency status to admin clients.
Database queries above `SLOW_QUERY_LOG_MS` produce warning logs.

See [OPERATIONS.md](OPERATIONS.md) for operator procedures and
[SECURITY.md](../SECURITY.md) for trust boundaries.
