# Operations

The checked-in Compose stack is a deployment reference. It binds the API to
`127.0.0.1:8080` and does not provide TLS termination, external monitoring, or
scheduled backups.

## Lifecycle

Build and start:

```bash
docker compose -f deploy/docker-compose.yml up -d --build
```

Inspect status and logs:

```bash
docker compose -f deploy/docker-compose.yml ps
docker compose -f deploy/docker-compose.yml logs migration api worker
```

Stop without deleting persistent volumes:

```bash
docker compose -f deploy/docker-compose.yml down
```

Do not add `--volumes` unless PostgreSQL and Redis data is intentionally
disposable.

## Health

`/healthz` reports process liveness. `/readyz` verifies PostgreSQL, Redis, and
the expected migration revision:

```bash
curl --fail http://127.0.0.1:8080/healthz
curl --fail http://127.0.0.1:8080/readyz
```

Detailed health and metrics require `X-Admin-Key`; treat their output as
sensitive operational data.

## Durable state and delivery

PostgreSQL is the source of record for alarms, configuration, audit, and the
ordered outbox. Redis holds ARQ jobs, sessions, idempotency state, and rate
limits. Back up PostgreSQL as the durable recovery record and recover Redis
only through a tested, consistent procedure.

An alarm event is committed with its outbox row before ARQ receives it. The
worker delivers provider requests and writes delivery audit. Monitor pending
outbox rows, failed jobs, provider failures, Redis memory and rejected writes,
database connection saturation, and readiness failures. Provider delivery is
at least once.

## Backup and restore

Create a PostgreSQL custom-format backup:

```bash
docker compose -f deploy/docker-compose.yml exec -T postgres \
  pg_dump -U alarm -d alarm -Fc > escalane.dump
```

Restore only into the intended target after stopping application writes:

```bash
docker compose -f deploy/docker-compose.yml exec -T postgres \
  pg_restore -U alarm -d alarm --clean --if-exists < escalane.dump
```

`pg_restore --clean` is destructive. Test backup and restore procedures in a
separate environment.

## Upgrade

Use one immutable image digest for migration, API, and worker:

```bash
export ESCALANE_IMAGE='ghcr.io/sebastianspicker/escalane@sha256:<digest>'
docker compose -f deploy/docker-compose.yml pull migration api worker
docker compose -f deploy/docker-compose.yml up -d --wait postgres redis
docker compose -f deploy/docker-compose.yml run --rm --no-deps migration
docker compose -f deploy/docker-compose.yml up -d --no-deps --force-recreate api worker
curl --fail http://127.0.0.1:8080/readyz
```

Do not start a new API or worker when migration fails. Assess schema
compatibility before rolling an application image back.

## Network boundary

Terminate TLS at a separately managed reverse proxy and keep the Compose
loopback binding. Set `BASE_URL` to the externally visible HTTPS origin and
restrict `TRUSTED_PROXY_CIDRS` to the immediate proxy peers. Do not log
acknowledgement URLs or trigger tokens.

## Troubleshooting

For readiness failures, inspect PostgreSQL, Redis, migration, API, and worker
logs, then confirm `DATABASE_URL` and `REDIS_URL` use reachable endpoints.

For delivery failures, inspect the worker log and delivery audit, then confirm
the provider is enabled, credentials are present, and destination validation
passes. Redis restart invalidates browser sessions; users must sign in again.

See [Security](../SECURITY.md) for deployment controls.
