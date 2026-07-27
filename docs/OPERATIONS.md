# Operations

The checked-in Compose stack is suitable for local evaluation and as a
deployment reference. It binds the API to `127.0.0.1:8080` and does not include
TLS termination, external monitoring, or scheduled backups.

## Service lifecycle

Build and start:

```bash
docker compose -f deploy/docker-compose.yml up -d --build
```

Inspect status and logs:

```bash
docker compose -f deploy/docker-compose.yml ps
docker compose -f deploy/docker-compose.yml logs api worker migration
```

Stop the stack without deleting volumes:

```bash
docker compose -f deploy/docker-compose.yml down
```

Do not add `--volumes` unless the PostgreSQL and Redis data is intentionally
disposable.

## Health and readiness

`/healthz` is a liveness check. It confirms that the API process can answer:

```bash
curl --fail http://127.0.0.1:8080/healthz
```

`/readyz` checks PostgreSQL, Redis, and the current Alembic revision. It returns
HTTP 503 when a dependency or schema check fails:

```bash
curl --fail http://127.0.0.1:8080/readyz
```

Detailed health and metrics require the admin key:

```bash
curl --fail \
  -H "X-Admin-Key: <admin-api-key>" \
  http://127.0.0.1:8080/healthz/details

curl --fail \
  -H "X-Admin-Key: <admin-api-key>" \
  http://127.0.0.1:8080/metrics
```

Treat both responses as sensitive operational data. Do not expose them through
an unauthenticated proxy rule.

## Logs

`LOG_LEVEL` accepts `DEBUG`, `INFO`, `WARNING`, `ERROR`, or `CRITICAL`. The
default is `INFO`.

API request logs include a request ID. Capability-bearing acknowledgement
paths are masked. Provider failures use bounded categories to reduce accidental
request-data exposure. Review log forwarding and retention before deployment.

Set `SLOW_QUERY_LOG_MS` to control the database warning threshold. A value of
`0` logs every query and can create substantial output.

## Database backup and restore

Create a PostgreSQL custom-format dump:

```bash
docker compose -f deploy/docker-compose.yml exec -T postgres \
  pg_dump -U alarm -d alarm -Fc > escalane.dump
```

Restore into an empty target database after stopping application writes:

```bash
docker compose -f deploy/docker-compose.yml exec -T postgres \
  pg_restore -U alarm -d alarm --clean --if-exists < escalane.dump
```

Test restore procedures on a separate environment. `pg_restore --clean`
replaces objects in the selected database and is destructive.

The Redis service uses append-only persistence with `appendfsync everysec`.
Its volume contains queued jobs, sessions, idempotency keys, and rate-limit
state. PostgreSQL remains the authority for alarm and audit data. Preserve and
restore Redis only as part of a tested, consistent recovery procedure.

## Upgrade

Select the new immutable image:

```bash
export ESCALANE_IMAGE='ghcr.io/sebastianspicker/escalane@sha256:<digest>'
docker compose -f deploy/docker-compose.yml pull migration api worker
```

Start dependencies, run the migration from the exact image, then replace API
and worker:

```bash
docker compose -f deploy/docker-compose.yml up -d --wait postgres redis
docker compose -f deploy/docker-compose.yml run --rm --no-deps migration
docker compose -f deploy/docker-compose.yml up -d --no-deps --force-recreate api worker
curl --fail http://127.0.0.1:8080/readyz
```

Do not start the new API or worker if migration fails. Before an application
rollback, review whether the new schema remains compatible with the previous
image. Alembic downgrade support must be assessed migration by migration.

## Reverse proxy

Terminate TLS at a separately managed reverse proxy. Keep the Compose loopback
binding and proxy to `127.0.0.1:8080`.

Set `BASE_URL` to the externally visible HTTPS origin. Set
`TRUSTED_PROXY_CIDRS` to the exact address or narrow CIDR of the immediate
proxy peer. Escalane ignores forwarded client and scheme headers from other
sources.

The reverse proxy should preserve the request path and query, pass a request
ID when available, apply appropriate body and timeout limits, and avoid logging
acknowledgement URLs or trigger tokens.

## Worker and Redis

The worker entry point is:

```text
arq escalane.worker.settings.WorkerSettings
```

The worker processes alarm events, delivery attempts, delayed escalation, and
outbox recovery. Redis must permit `EVAL`; the trigger service uses it for an
atomic compare-and-delete operation. The checked-in Redis configuration uses
`noeviction`, so a full Redis instance rejects writes rather than silently
discarding queue or session keys.

Monitor:

- Redis memory and rejected writes
- queued and failed ARQ jobs
- unpublished outbox rows
- provider delivery failures
- database connection saturation
- readiness failures and migration drift

The default database pool and worker settings are starting values, not measured
capacity guidance. Test changes under representative load.

## Simulation

Simulation mode is for local evaluation:

```text
SIMULATION_ENABLED=true
BASE_URL=http://localhost:8080
```

Prepare its fixtures and inspect mock delivery:

```bash
make demo-prepare
```

The simulation API and `/admin/simulation` require admin authentication.
Simulation does not contact configured providers.

## Troubleshooting

### Readiness returns 503

Inspect dependency and migration logs:

```bash
docker compose -f deploy/docker-compose.yml ps
docker compose -f deploy/docker-compose.yml logs postgres redis migration api
```

Confirm that `DATABASE_URL` and `REDIS_URL` use Compose service names when the
application runs inside Compose. Apply migrations with:

```bash
docker compose -f deploy/docker-compose.yml run --rm migration
```

### Admin sign-in fails

An empty `ADMIN_API_KEY` fails closed. Confirm that the API received the
intended configuration and restart it after changing `.env`. Do not print the
key in logs or command output.

### Trigger is rejected

Check:

- the query key matches `YELK_TOKEN_QUERY_PARAM`
- the device token exists and is active
- the observed source matches `YELK_IP_ALLOWLIST`
- forwarded addresses are trusted only from `TRUSTED_PROXY_CIDRS`
- the per-device Redis rate limit has not been exceeded

### Connector does not send

Check the worker logs and alarm delivery audit. Confirm that the connector is
enabled, its required credential is set, its endpoint passes URL validation,
and any generic webhook host is listed in `WEBHOOK_ALLOWED_HOSTS`.

### Browser session expires

Operator sessions are stored in Redis. Redis restart or data loss invalidates
them. Sign in again. Confirm Redis health if sessions expire unexpectedly.

### Browser assets return 404

Confirm that the installed wheel or container includes
`escalane/api/templates` and `escalane/api/assets`. Run:

```bash
make package-check
```

## Operational boundary

Before live use, verify connector delivery, provider idempotency, TLS and proxy
behavior, network policy, secret storage, alert routing, retention, backup and
restore, rollback, accessibility, and target-browser behavior. Repository CI
does not establish those environment-specific properties.
