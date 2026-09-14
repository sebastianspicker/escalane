# Operate Escalane

The included Compose stack is a starting point for deployment. It exposes the
API only on `127.0.0.1:8080`. You must provide TLS termination, external
monitoring, and scheduled backups for the environment where you run it.

## Start, inspect, and stop the stack

Build and start all services:

```bash
docker compose -f deploy/docker-compose.yml up -d --build
```

Check their status and review logs from the migration, API, and worker:

```bash
docker compose -f deploy/docker-compose.yml ps
docker compose -f deploy/docker-compose.yml logs migration api worker
```

Stop the stack while keeping its persistent volumes:

```bash
docker compose -f deploy/docker-compose.yml down
```

Adding `--volumes` deletes the PostgreSQL and Redis volumes. Use it only when
that data is intentionally disposable.

## Check health and readiness

`/healthz` shows whether the API process is alive. `/readyz` also checks
PostgreSQL, Redis, and the expected migration revision:

```bash
curl --fail http://127.0.0.1:8080/healthz
curl --fail http://127.0.0.1:8080/readyz
```

The detailed health endpoint and metrics require `X-Admin-Key`. Their output
contains operational details, so give access only to trusted systems and
operators.

## Understand stored state and delivery

PostgreSQL is the durable record for alarms, configuration, audit entries, and
the ordered outbox. Redis stores ARQ jobs, browser sessions, idempotency state,
and rate limits. Back up PostgreSQL as the recovery record. Restore Redis only
with a procedure you have tested for a consistent deployment state.

Escalane commits an alarm event and its outbox row before it submits the ARQ
job. The worker then calls the provider and records the delivery result. Watch
for pending outbox rows, failed jobs, provider failures, Redis memory pressure
and rejected writes, exhausted database connections, and readiness failures.
Providers can receive the same delivery more than once because delivery is at
least once.

## Back up and restore PostgreSQL

Create a custom-format backup:

```bash
docker compose -f deploy/docker-compose.yml exec -T postgres \
  pg_dump -U alarm -d alarm -Fc > escalane.dump
```

Before restoring, stop application writes and verify the target database. Then
run:

```bash
docker compose -f deploy/docker-compose.yml exec -T postgres \
  pg_restore -U alarm -d alarm --clean --if-exists < escalane.dump
```

`pg_restore --clean` removes existing database objects before recreating them.
Test the entire backup and restore procedure in a separate environment before
depending on it for recovery.

## Upgrade an installation

Use the same immutable image digest for the migration, API, and worker:

```bash
export ESCALANE_IMAGE='ghcr.io/sebastianspicker/escalane@sha256:<digest>'
docker compose -f deploy/docker-compose.yml pull migration api worker
docker compose -f deploy/docker-compose.yml up -d --wait postgres redis
docker compose -f deploy/docker-compose.yml run --rm --no-deps migration
docker compose -f deploy/docker-compose.yml up -d --no-deps --force-recreate api worker
curl --fail http://127.0.0.1:8080/readyz
```

If the migration fails, leave the existing API and worker in place. Before
rolling back an application image, confirm that the older image can use the
current database schema.

## Put Escalane behind a reverse proxy

Keep the Compose port bound to loopback and terminate TLS at a separately
managed reverse proxy. Set `BASE_URL` to the HTTPS origin that users can reach.
Set `TRUSTED_PROXY_CIDRS` only to the immediate proxy peers whose forwarded
headers Escalane should trust. Keep acknowledgement URLs and trigger tokens out
of logs.

See [Security](../SECURITY.md) for the rest of the deployment controls.

## Recover queued work and retry delivery

A recovery run handles no more than `RECOVERY_EVENT_LIMIT` events. The default
is 5,000, and the accepted range is 1 to 100,000. Within that run, Escalane
locks and commits at most `OUTBOX_BATCH_SIZE` rows per transaction. The default
is 25, and the accepted range is 1 to 500.

`RECOVERY_BUDGET_SECONDS` limits how long recovery should spend between
batches. Its default is 10 seconds and its maximum is 300 seconds. This is a
soft budget rather than a provider timeout: a slow Redis request may keep a
batch running beyond it. When one alarm stream fails, recovery blocks that
stream for the rest of the run and continues with other streams. Stable ARQ job
identities prevent a successful enqueue from producing a second job when the
database commit must be retried.

Notification delivery is serial and at least once. A permanent provider
rejection is recorded as `permanent_error`, which prevents a retry for another
target from repeating the rejected delivery. This behavior covers initial
tickets, acknowledgement notes, state webhooks, and target notifications.

Delivery audit lookups use a logical key scoped to the alarm, channel, and
target. A successful Zammad ticket creation remains identifiable even if the
provider returns no ticket ID. During acknowledgement recovery, Escalane first
removes completed and permanently rejected deliveries for the alarm's current
ticket, then applies the candidate limit.

Webhook connections are cached by their original scheme, host, and port, up to
64 origins. Escalane still validates DNS and pins the resolved address for each
delivery. It may evict idle clients, and it uses a temporary client when all
cached clients are busy. Different origins do not share a client just because
they resolve to the same IP address. Worker shutdown closes the pool.

## Interpret the dashboard and history limits

The dashboard polls every 15 seconds and uses a revision stored in PostgreSQL.
Database triggers update that revision in the same transaction whenever
visible alarm data or displayed person and room names change. This includes
writes from imports and application services. A rolled-back transaction does
not change the committed revision.

Status counts are cached in Redis for up to 60 seconds under the database
revision. A new revision immediately changes the cache lookup. Before Escalane
stores a count, it checks the revision again. If Redis is unavailable, it reads
the counts from PostgreSQL. The dashboard worklist and its cache both omit
soft-deleted alarms.

Configuration lists show 50 records by default and allow up to 100 per page,
ordered by resource ID. Alarm details show the newest 50 timeline entries in
chronological order. Select **Older activity** to move through earlier entries;
the creation event is on the earliest page. The links work without JavaScript.
In the drawer, older entries load incrementally and a failed request offers a
retry.

## Interpret metrics

HTTP metrics use the matched route template as the route label. Static assets
use `/admin/assets/{path}`, misses use `unmatched`, and unsupported HTTP methods
use `OTHER`. Alarm IDs, acknowledgement capabilities, and arbitrary request
paths do not appear in metric labels.

These latency histograms report seconds in fixed buckets and do not include
target labels:

- `escalane_http_duration_seconds`
- `escalane_provider_delivery_duration_seconds`
- `escalane_redis_enqueue_duration_seconds`
- `escalane_connection_acquisition_duration_seconds`

Connection acquisition measures pool wait plus connection setup. Provider and
queue timing includes failed attempts. API counters belong to the individual
API process that exposes them.

Every 15 seconds, the most recently reporting worker stores a bounded snapshot
in Redis with a 45-second expiry. Its histogram names start with `worker_`.
These values describe one worker, not the combined total for a worker fleet.
Escalane omits an expired or unavailable worker snapshot. A missing heartbeat
does not mean the backlog is zero or the worker is healthy.

`escalane_outbox_pending` and `escalane_outbox_oldest_age_seconds` report the
current durable backlog. `escalane_worker_overdue_queue_age_seconds` ignores
ARQ jobs scheduled for the future.
`escalane_worker_heartbeat_timestamp_seconds` records when the worker was
observed. Pool gauges report the pool size, checked-out connections, configured
capacity, and utilization, where utilization is checked-out connections
divided by capacity. Injected engines that do not expose capacity metadata omit
capacity and utilization.

Historical alarm and notification totals use a ten-second Redis cache and fall
back to PostgreSQL when Redis fails. Historical alarm totals include
soft-deleted alarms, unlike the dashboard counts. Historical totals and worker
snapshots may also come from different observation times.

## Upgrade or roll back migration 0008

Migration `0008` must finish before the new API or worker starts. It adds the
nullable notification logical key, backfills it in batches of 1,000 records,
adds an index, and creates the dashboard revision and its triggers. The batches
bound statement size and application memory, but PostgreSQL holds the
migration's DDL and row locks until the transaction commits. Test the migration
against a representative restored database and schedule a maintenance window
for the real upgrade.

Older application versions can insert null logical keys after migration
`0008`. New readers retain SQL payload matching for those records. This
compatibility does not prove that an old image is safe to deploy, because
readiness checks require a specific Alembic head.

Do not downgrade the schema while new processes are running. A downgrade
removes the logical keys and dashboard revision state, preserves audit
payloads, and requires the older application image. A later upgrade creates a
new revision epoch. Keep a consistent backup and test the chosen image and
schema together before allowing writes again.

## Troubleshoot common failures

If `/readyz` fails, inspect the PostgreSQL, Redis, migration, API, and worker
logs. Then check that `DATABASE_URL` and `REDIS_URL` point to endpoints the
containers can reach.

For a delivery failure, review the worker log and delivery audit. Confirm that
the provider is enabled, its credentials are available, and the destination
passes validation. Restarting Redis invalidates browser sessions, so operators
will need to sign in again.
