# Architecture

Escalane is one modular monolith. The web process handles HTTP and browser
rendering; the ARQ worker handles asynchronous delivery and scheduling. Both
use the same feature modules and PostgreSQL persistence boundary.

## Runtime boundaries

| Boundary | Responsibility |
|---|---|
| PostgreSQL | Durable configuration, alarm lifecycle, audit data, and ordered outbox |
| Redis and ARQ | Job transport plus transient sessions, idempotency, and rate limits |
| Web adapter | FastAPI routes, request authentication, templates, assets, and HTTP responses |
| Worker adapter | ARQ jobs, delayed escalation, outbox publication and recovery |
| Providers | Translation of notification requests to external systems |

## Dependency direction

```text
config / contracts / persistence / security / runtime / providers
                         ↓
alarms / configuration / notifications / operations
                         ↓
web                         worker
```

Foundations must not depend on features or adapters. Feature modules may use
foundations and explicit feature contracts, but never import `web` or `worker`.
The adapters translate inbound transport concerns into feature calls and do not
own alarm or delivery policy.

## Source map

| Path | Responsibility |
|---|---|
| `src/escalane/config/` | Environment settings and configuration validation |
| `src/escalane/contracts/` | Stable shared types and boundary contracts |
| `src/escalane/persistence/` | Models, engine, sessions, and database access |
| `src/escalane/security/` | Source-IP and outbound-URL trust-boundary controls |
| `src/escalane/runtime/` | Redis rate-limit keys and atomic coordination helpers |
| `src/escalane/providers/` | External-provider interfaces and implementations |
| `src/escalane/alarms/` | Trigger, acknowledgement, lifecycle, and alarm events |
| `src/escalane/configuration/` | Seed, policy, master-data, and redacted admin-audit rules |
| `src/escalane/notifications/` | Notification policy, payloads, delivery, and escalation |
| `src/escalane/operations/` | Metrics and operational queries |
| `src/escalane/web/` | FastAPI application, routes, templates, and browser assets |
| `src/escalane/worker/` | ARQ task registration, scheduling, and recovery |
| `migrations/` | Alembic environment and schema revisions |

## Alarm and delivery flow

1. The web adapter validates a Yealink-compatible request, responder action,
   or operator action and calls the relevant feature.
2. The feature updates durable state and inserts the ordered outbox row in the
   same PostgreSQL transaction.
3. The outbox publisher submits the event to ARQ through Redis.
4. The worker invokes notification and provider logic, schedules follow-up
   escalation where needed, and writes a delivery audit record.
5. Recovery retries unpublished outbox rows. Provider delivery is at least
   once, so external idempotency remains necessary.

## External contracts

The public surfaces are `/healthz`, `/readyz`,
`/v1/yealink/alarm`, `/a/{ack_token}`, `/admin`, `/v1/alarms`, and
`/v1/admin`. HTTP authentication and response formatting belong in `web`.
Provider protocols belong in `providers`; provider-specific responses must not
leak into HTTP contracts.

Schema history is in `migrations/`. Apply it before starting web or worker
processes. PostgreSQL remains authoritative if Redis is restarted or a job is
retried.
