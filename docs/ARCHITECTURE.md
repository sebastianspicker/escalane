# Alarm Broker Architecture

## Scope and intent

The system is a release-candidate reference implementation with:
- stable core flow,
- clear auditability,
- secure defaults,
- additive, backward-compatible API evolution.

It is not validated for safety-critical, security-critical, or compliance-critical production use.

## Runtime components

- FastAPI API service (`alarm_broker.api`)
- arq worker (`alarm_broker.worker`)
- PostgreSQL (state + audit)
- Redis (idempotency, rate limiting, jobs)
- Connector clients:
  - Zammad
  - SMS provider (generic HTTP)
  - Signal endpoint (signal-cli-rest-api compatible)

## End-to-end flow

1. Trigger source (Yealink webhook) calls:
- `GET /v1/yealink/alarm?token=<device_token>`

2. API validates:
- source IP allowlist,
- idempotency bucket,
- per-token rate limits,
- device mapping consistency.

3. API persists `alarms` row with `status=triggered`, `ack_token`, metadata, then enqueues `alarm_created`.

4. Worker enriches alarm context (person/room/site), sends stage 0 notifications, and schedules escalation jobs.

5. ACK flow:
- `GET /a/{ack_token}` renders responder page,
- `POST /a/{ack_token}` acknowledges alarm and enqueues `alarm_acked`.

6. Admin lifecycle/API:
- `POST /v1/alarms/{alarm_id}/ack`
- `POST /v1/alarms/{alarm_id}/resolve`
- `POST /v1/alarms/{alarm_id}/cancel`

## Alarm lifecycle

Allowed transitions:
- `triggered -> acknowledged`
- `triggered -> resolved`
- `triggered -> cancelled`
- `acknowledged -> resolved`
- `acknowledged -> cancelled`

Invalid transitions are rejected with `409`.
Repeated transition to same target state is idempotent (`204`).

## Operational endpoints

- `GET /healthz`: basic liveness
- `GET /readyz`: DB + Redis readiness

`/readyz` returns `503` if one dependency is unavailable.

## Data model (PostgreSQL)

Core tables:
- **Master data**: `sites`, `rooms` (FK sites), `persons`, `devices` (token mapping)
- **Escalation config**: `escalation_targets`, `escalation_policy`, `escalation_steps` (composite PK: policy_id, step_no, target_id)
- **Alarms**: `alarms` (UUID PK, status lifecycle, context, integration fields, JSON meta)
- **Audit**: `alarm_notifications` (channel, target_id, payload, result, error), `alarm_notes`

`devices.device_token` is the inbound trigger anchor. `alarms.ack_token` provides the capability URL.

## Module organization

The API routes are split into focused modules:
- `api/routes/alarms.py` — list, export, stats (read-only queries)
- `api/routes/alarm_operations.py` — CRUD, state transitions, bulk operations
- `api/routes/alarm_notes.py` — notes timeline

Typed internal data uses `types.py` (`EnrichedAlarmContext`, `NotificationPayload`).

## Primary code paths

The main vertical slice is:

1. `api/main.py` creates the FastAPI app, installs security/observability middleware, opens DB/Redis resources, and registers routes.
2. `api/routes/yealink.py` handles the external trigger request and delegates all business logic to `TriggerService`.
3. `services/trigger_service.py` validates the trigger, reserves a Redis idempotency key, persists the alarm row, and records event-delivery state in `alarm.meta`.
4. `services/event_publisher.py` converts service events into ARQ jobs for the worker. Its `JOB_NAME` and payload fields are a wire contract with `worker/tasks.py`.
5. `worker/tasks.py` dispatches ARQ events to notification fan-out, delayed escalations, ACK follow-up notes, state-change webhooks, and event-delivery recovery.
6. `services/notification_service.py` builds user-facing messages and writes one `alarm_notifications` audit row per channel attempt.

The ACK flow starts in `api/routes/ack.py`, but uses the same state-transition helpers in `services/alarm_service.py` and the same event publisher wrappers in `services/event_service.py`.

## Internal invariants

- Device tokens are secrets. Logs use short SHA-256 token hashes, and trigger errors avoid distinguishing unknown tokens from incomplete mappings.
- The Redis idempotency key is scoped to a 10-second bucket. It stores the pre-reserved alarm UUID so rapid duplicate trigger requests can return the same alarm once persistence catches up.
- `alarm.meta.event_delivery` is recovery state, not business history. It records whether the trigger-side `alarm.created` and initial `alarm.state_changed` jobs were queued; `recover_incomplete_alarm_events` uses it to retry after transient Redis/worker failures.
- Resolve and cancel are alarm states, not separate worker event types. Downstream webhooks receive them through the generic `alarm.state_changed` event with `new_state`.
- Notification delivery is best effort per channel. Failures are logged in `alarm_notifications` and metrics, but one failing connector should not block other channels.
- `ack_token` is a capability URL. It is never logged as a raw path segment and the ACK page is sent with `Cache-Control: no-store`.

## Error handling

Services use domain exceptions (`ConflictError`, `NotFoundError`, `ValidationError` from `core/errors.py`). Centralized exception handlers in `api/main.py` map them to HTTP status codes. Routes never raise `HTTPException` for business logic errors.

## Layer boundaries

- `api/routes/` → `services/` → `db/`, `connectors/`
- `core/` is cross-cutting (errors, idempotency, rate limiting, metrics rendering) with zero DB imports
- `worker/` → `services/` → `db/`, `connectors/`
- Message formatting lives in `services/message_formatter.py`

## API notes

- Existing endpoints remain backward-compatible.
- `GET /v1/alarms` supports additive pagination via optional `cursor` + `limit`.
- If another page exists, header `X-Next-Cursor` is returned.
- CSV export sanitizes values to prevent formula injection.

## Observability baseline

A request middleware adds:
- request ID (`X-Request-ID` response header),
- structured request logs with route, status, latency, and optional alarm ID.

Prometheus metrics at `/metrics` expose HTTP request counts, alarm status gauges, and notification attempt counters. The endpoint is protected by the admin API key rather than being publicly scrapeable by default.

## Security baseline

- Fail-closed admin auth when `ADMIN_API_KEY` is unset.
- Token and IP validation on inbound trigger routes.
- Escaped ACK HTML rendering.
- Input validation on admin seed and escalation policy operations (Pydantic + ORM only).
- HMAC-SHA256 signed webhook payloads.
- CSV export formula injection protection.
- Strict mypy type checking enforced in CI.
