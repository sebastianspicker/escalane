# Alarm Broker Architecture

## Scope and intent

The system is intentionally designed as a hardened PoC:
- stable core flow,
- clear auditability,
- secure defaults,
- additive, backward-compatible API evolution.

It is not a complete production platform yet.

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
