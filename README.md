![Escalane route mark](src/escalane/web/assets/escalane-mark.svg)

# Escalane

Escalane accepts device alarm triggers, keeps alarm and audit state in
PostgreSQL, and coordinates notification and escalation delivery through
Redis-backed ARQ workers. It provides responder acknowledgement links and a
server-rendered operator console.

Escalane is a public alpha for technical evaluation. It is not validated for
safety-critical, emergency-response, or compliance-critical use.

## Runtime model

- PostgreSQL is the durable source for configuration, alarms, lifecycle events,
  notification audit, and the ordered outbox.
- Redis carries ARQ jobs and transient browser-session, idempotency, and
  rate-limit state.
- The web process receives HTTP requests and renders browser pages.
- The worker consumes jobs, delivers provider requests, schedules escalation,
  and records delivery results.

An alarm transition writes its durable event and outbox row in PostgreSQL. The
publisher sends that row to ARQ. The worker performs delivery and stores the
delivery audit. Delivery is at least once, so providers must tolerate a retry.

## Quick start

Create a local configuration file and set the required credentials:

```bash
cp .env.example .env
```

For a local simulated workflow, set `SIMULATION_ENABLED=true` and a loopback
`BASE_URL`. Start the Compose stack and wait for readiness:

```bash
docker compose -f deploy/docker-compose.yml up -d --build
curl --fail http://127.0.0.1:8080/readyz
```

Load the sample configuration, then trigger its device:

```bash
curl --fail \
  -H "X-Admin-Key: <admin-api-key>" \
  -H "Content-Type: application/yaml" \
  --data-binary @deploy/seed.example.yaml \
  http://127.0.0.1:8080/v1/admin/seed

curl --get --fail \
  --data-urlencode "token=<device-token>" \
  http://127.0.0.1:8080/v1/yealink/alarm
```

Open `http://127.0.0.1:8080/admin/login` and authenticate with
`ADMIN_API_KEY`. See [Setup](docs/SETUP.md) for configuration and local
process commands.

## HTTP surfaces

| Surface | Path | Access |
|---|---|---|
| Liveness and readiness | `/healthz`, `/readyz` | Public |
| Device trigger | `/v1/yealink/alarm` | Device token and source allowlist |
| Responder acknowledgement | `/a/{ack_token}` | Capability token |
| Operator console | `/admin` | Session and CSRF protection |
| Alarm and administration API | `/v1/alarms`, `/v1/admin` | `X-Admin-Key` |
| Metrics and detailed health | `/metrics`, `/healthz/details` | `X-Admin-Key` |

Set `ENABLE_API_DOCS=true` to expose `/docs`, `/redoc`, and `/openapi.json`.

## Repository layout

```text
.
├── src/escalane/       Application package
├── tests/              Application contracts
├── migrations/         Alembic environment and revisions
├── deploy/             Compose deployment and sample seed
├── docs/               Operating and architecture guides
├── pyproject.toml      Package and tool configuration
└── alembic.ini         Migration configuration
```

The authoritative source map and dependency direction are in
[Architecture](docs/ARCHITECTURE.md).

## Development

From the repository root:

```bash
make install
make lint
make test
make package-check
```

After `make install`, `make dev` starts the reloadable web process. Use
[Contributing](CONTRIBUTING.md) for the complete workflow and
[Frontend](docs/FRONTEND.md) for browser checks.

## Operations and security

The checked-in Compose deployment starts PostgreSQL, Redis, migration, API,
and worker services. It binds the API to loopback and does not provide TLS,
backups, external monitoring, or managed secret storage.

Use [Operations](docs/OPERATIONS.md) for deployment procedures and
[Security](SECURITY.md) before exposing a service. Integration contracts are
in [Integrations](docs/INTEGRATIONS.md).

## Support

See [SUPPORT.md](SUPPORT.md) for public issue guidance and
[CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) for participation rules. The project
is licensed under the [MIT License](LICENSE).
