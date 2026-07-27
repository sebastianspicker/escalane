# Setup

## Requirements

The documented runtime path uses Docker and the Compose plugin. Local
development requires Python 3.14.x; CI uses Python 3.14.6.

Optional checks also require:

- PostgreSQL and Redis for the runtime and integration checks
- Playwright browser binaries for browser E2E tests
- `curl` for HTTP examples
- GNU Make for repository command targets

## Install with Docker Compose

Create the local environment file:

```bash
cp .env.example .env
```

Set `POSTGRES_PASSWORD` and make the password in `DATABASE_URL` match it. Set a
random `ADMIN_API_KEY`. For the sample seed, also set
`YEALINK_DEVICE_TOKEN`, `SIGNAL_TARGET_GROUP_ID`, `ESCALATE_T1`,
`ESCALATE_T2`, and `ESCALATE_T3`.

For local evaluation without live connectors, set:

```text
BASE_URL=http://localhost:8080
SIMULATION_ENABLED=true
```

Simulation mode requires a loopback `BASE_URL`. Outside simulation mode,
`YELK_IP_ALLOWLIST` is required and the default database password is rejected.

Build the shared application image, apply migrations, and start the API and
worker:

```bash
docker compose -f deploy/docker-compose.yml up -d --build
curl --fail http://127.0.0.1:8080/readyz
```

The API and worker wait for PostgreSQL, Redis, and the one-shot migration
service. `/readyz` returns HTTP 503 until those dependencies and Alembic
revision `0007` are ready.

Load the sample seed:

```bash
curl --fail \
  -H "X-Admin-Key: <admin-api-key>" \
  -H "Content-Type: application/yaml" \
  --data-binary @deploy/seed.example.yaml \
  http://127.0.0.1:8080/v1/admin/seed
```

The sample contact values are placeholders. Replace them before testing a live
connector.

## Local development

From the repository root:

```bash
make install
```

This creates `.venv`, upgrades `pip`, and installs
`services/escalane[dev]` in editable mode.

Compose reads the root `.env` through `deploy/docker-compose.yml`. Direct
Python processes started from `services/escalane` look for `.env` in that
directory. To reuse the ignored root file, create an ignored service-local
symlink:

```bash
ln -s ../../.env services/escalane/.env
```

Alternatively, export every required variable in the shell that starts
Uvicorn, ARQ, or Alembic. A root `.env` file alone is not loaded by those
commands.

To run the API against configured PostgreSQL and Redis services:

```bash
cd services/escalane
../../.venv/bin/uvicorn escalane.api.main:app --reload
```

Run the worker in a second shell:

```bash
cd services/escalane
../../.venv/bin/arq escalane.worker.settings.WorkerSettings
```

Apply migrations before starting either process:

```bash
cd services/escalane
../../.venv/bin/alembic upgrade head
```

`make dev` is a test target. It installs development dependencies and runs the
verbose test suite.

## Configuration

Application settings are loaded from process environment variables and an
optional `.env` in the process working directory. Variable names are
case-insensitive in the settings loader, but the repository uses uppercase
names.

### Core and ingress

| Variable | Default | Requirement or effect |
|---|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://alarm:change-me@localhost:5432/alarm` | PostgreSQL SQLAlchemy URL. The default password is rejected outside simulation. |
| `REDIS_URL` | `redis://localhost:6379/0` | Redis URL for queues, sessions, idempotency, and rate limits. |
| `BASE_URL` | `http://localhost:8080` | Origin for acknowledgement links. It cannot contain credentials, path, query, or fragment. Non-loopback origins require HTTPS. |
| `LOG_LEVEL` | `INFO` | One of `DEBUG`, `INFO`, `WARNING`, `ERROR`, or `CRITICAL`. |
| `YELK_TOKEN_QUERY_PARAM` | `token` | Query key used by the Yealink trigger route. |
| `YELK_IP_ALLOWLIST` | Empty | Comma-separated IP addresses or CIDRs. Required outside simulation. |
| `YEALINK_DEVICE_TOKEN` | Empty | Placeholder consumed by `deploy/seed.example.yaml`; not read by the trigger route directly. |
| `RATE_LIMIT_PER_MINUTE` | `10` | Per-device trigger limit. Range: 1 to 1000. |
| `ADMIN_API_KEY` | Empty | Admin API and browser sign-in credential. Empty configuration fails closed. |
| `ENABLE_API_DOCS` | `false` | Enables `/docs`, `/redoc`, and `/openapi.json`. |
| `TRUSTED_PROXY_CIDRS` | Empty | Immediate proxy peers allowed to supply forwarded client IP and HTTPS headers. |
| `SIMULATION_ENABLED` | `false` | Enables mock connector and simulation routes. Requires a loopback `BASE_URL`. |

### Zammad

| Variable | Default | Requirement or effect |
|---|---|---|
| `ZAMMAD_BASE_URL` | `https://zammad.example.org` | HTTPS origin without URL credentials. The reserved default is rejected when enabled. |
| `ZAMMAD_API_TOKEN` | Empty | A non-empty value enables Zammad delivery. |
| `ZAMMAD_GROUP` | `Notfallstelle` | Ticket group. |
| `ZAMMAD_PRIORITY_ID_P0` | `3` | Priority ID for the initial alarm ticket. |
| `ZAMMAD_STATE_ID_NEW` | `1` | State ID for a new ticket. |
| `ZAMMAD_CUSTOMER` | `guess:alarm-system@example.org` | Zammad customer expression. |

### SendXMS

| Variable | Default | Requirement or effect |
|---|---|---|
| `SENDXMS_ENABLED` | `false` | Enables the SMS connector. |
| `SENDXMS_BASE_URL` | `https://api.sendxms.tld` | HTTPS origin without URL credentials. The reserved default is rejected when enabled. |
| `SENDXMS_API_KEY` | Empty | Required when enabled. |
| `SENDXMS_FROM` | `Notfall` | Sender label. |
| `SENDXMS_SEND_PATH` | `/send` | Relative send endpoint. |

### Signal

| Variable | Default | Requirement or effect |
|---|---|---|
| `SIGNAL_ENABLED` | `false` | Enables the Signal REST bridge connector. |
| `SIGNAL_CLI_ENDPOINT` | `http://signal-cli:8080` | HTTP or HTTPS bridge origin. |
| `SIGNAL_TARGET_GROUP_ID` | Empty | Required when enabled and consumed by the sample seed. |
| `SIGNAL_SEND_PATH` | `/v2/send` | Relative bridge endpoint. |

### Generic webhook

| Variable | Default | Requirement or effect |
|---|---|---|
| `WEBHOOK_ENABLED` | `false` | Enables signed alarm state callbacks. |
| `WEBHOOK_URL` | Empty | HTTPS callback URL. Required when enabled. |
| `WEBHOOK_SECRET` | Empty | HMAC secret of at least 32 characters. Required when enabled. |
| `WEBHOOK_TIMEOUT_SECONDS` | `5` | Request timeout. Range: 1 to 60 seconds. |
| `WEBHOOK_ALLOWED_HOSTS` | Empty | Comma-separated exact host names allowed for callback and generic webhook delivery. Wildcards are rejected. |

`ALLOW_HTTP_WEBHOOKS=true` is a process-environment compatibility switch used
only by generic escalation-target webhooks. It is read directly from
`os.environ`, not from the typed settings object. Compose `env_file` exports it
to the process; direct local commands must export it in the shell. Exact host
allowlisting and public-address checks still apply. It does not permit HTTP for
`WEBHOOK_URL`.

### Timing and database

| Variable | Default | Requirement or effect |
|---|---|---|
| `ESCALATE_T1` | `60` | First delayed seed step in seconds. Must be non-negative. |
| `ESCALATE_T2` | `180` | Second delayed seed step in seconds. Must be non-negative. |
| `ESCALATE_T3` | `300` | Third delayed seed step in seconds. Must be non-negative. |
| `DB_POOL_SIZE` | `5` | SQLAlchemy pool size. Range: 1 to 100. |
| `DB_MAX_OVERFLOW` | `10` | Extra pool connections. Range: 0 to 200. |
| `DB_POOL_TIMEOUT` | `30` | Pool checkout timeout in seconds. Range: 1 to 300. |
| `DB_POOL_RECYCLE` | `1800` | Connection recycle interval in seconds. Range: 60 to 86400. |
| `SLOW_QUERY_LOG_MS` | `200` | Query duration threshold for warning logs. Set to `0` to log every query. |

## Sample workflow

After loading `deploy/seed.example.yaml`, trigger its device:

```bash
curl --get --fail \
  --data-urlencode "token=<device-token>" \
  http://127.0.0.1:8080/v1/yealink/alarm
```

Sign in at `http://127.0.0.1:8080/admin/login`. In simulation mode, inspect
mock deliveries at `/admin/simulation`.

The seed API accepts JSON and YAML. Use `application/yaml` or
`application/x-yaml` for YAML input.

## Database migrations

Create a revision in the writable local checkout:

```bash
cd services/escalane
../../.venv/bin/alembic revision --autogenerate -m "describe change"
```

Apply the checked-in migrations from the Compose image:

```bash
docker compose -f deploy/docker-compose.yml run --rm migration
```

The runtime container is read-only and cannot retain a newly created migration
file.

## Deploy a published image

Use an immutable image digest for migration, API, and worker:

```bash
export ESCALANE_IMAGE='ghcr.io/sebastianspicker/escalane@sha256:<digest>'
docker compose -f deploy/docker-compose.yml pull migration api worker
docker compose -f deploy/docker-compose.yml up -d --wait postgres redis
docker compose -f deploy/docker-compose.yml run --rm --no-deps migration
docker compose -f deploy/docker-compose.yml up -d --no-deps --force-recreate api worker
curl --fail http://127.0.0.1:8080/readyz
```

The checked-in Compose file binds the API to `127.0.0.1:8080`. Put a
separately managed TLS reverse proxy in front of it for remote access and set
`TRUSTED_PROXY_CIDRS` to the narrow address or CIDR of the immediate proxy.

See [OPERATIONS.md](OPERATIONS.md) for upgrades, backups, and troubleshooting.

## Repository paths

| Path | Purpose |
|---|---|
| `services/escalane/escalane/` | Application package |
| `services/escalane/alembic/` | Schema migrations |
| `services/escalane/tests/` | Unit, integration, security, repository-contract, and E2E tests |
| `deploy/docker-compose.yml` | Local and digest-based Compose deployment |
| `deploy/seed.example.yaml` | Sample entities and escalation policy |
| `scripts/` | Release, hygiene, smoke, and screenshot tools |
| `.github/workflows/` | CI, release, and screenshot-review workflows |

## Next steps

- [Architecture](ARCHITECTURE.md)
- [Operations](OPERATIONS.md)
- [Integrations](INTEGRATIONS.md)
- [Security](../SECURITY.md)
- [Contributing](../CONTRIBUTING.md)
