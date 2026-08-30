# Setup

## Requirements

The supported deployment reference uses Docker with the Compose plugin. Local
development uses Python 3.14.x, GNU Make, PostgreSQL, and Redis.

## Compose deployment

Create the local environment file:

```bash
cp .env.example .env
```

Set a non-default `POSTGRES_PASSWORD`, make it match `DATABASE_URL`, and set a
random `ADMIN_API_KEY`. Configure each enabled provider with its own
credentials. For local simulated delivery, set:

```text
BASE_URL=http://localhost:8080
SIMULATION_ENABLED=true
```

Start the stack and check readiness:

```bash
docker compose -f deploy/docker-compose.yml up -d --build
curl --fail http://127.0.0.1:8080/readyz
```

The Compose deployment starts PostgreSQL, Redis, a one-shot migration service,
the web process, and the worker. `/readyz` is successful only when both
dependencies and the schema are ready.

Load the sample seed with an admin key:

```bash
curl --fail \
  -H "X-Admin-Key: <admin-api-key>" \
  -H "Content-Type: application/yaml" \
  --data-binary @deploy/seed.example.yaml \
  http://127.0.0.1:8080/v1/admin/seed
```

Sample contact values are placeholders. Replace them before testing a live
provider.

## Local development

Run these commands from the repository root:

```bash
make install
alembic upgrade head
uvicorn escalane.web.main:app --reload
```

Start the worker in a second shell:

```bash
arq escalane.worker.settings.WorkerSettings
```

After installation, `make dev` starts the reloadable web process. Use
`make test`, `make lint`, and `make package-check` for the normal local checks.

Direct processes read settings from their environment and an optional root
`.env`. Apply migrations before starting web or worker processes.

## Configuration

Settings are owned by `src/escalane/config/`. The main runtime variables are:

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | PostgreSQL connection string |
| `REDIS_URL` | Redis connection for ARQ and transient state |
| `BASE_URL` | External origin used in acknowledgement links |
| `ADMIN_API_KEY` | Admin API and operator sign-in credential |
| `YELK_IP_ALLOWLIST` | Source CIDRs required outside simulation |
| `YELK_TOKEN_QUERY_PARAM` | Device-trigger query parameter name |
| `RATE_LIMIT_PER_MINUTE` | Per-device trigger limit |
| `TRUSTED_PROXY_CIDRS` | Proxy peers trusted to provide forwarded headers |
| `SIMULATION_ENABLED` | Enables mock delivery and simulation routes |
| `ENABLE_API_DOCS` | Enables OpenAPI and interactive API documentation |

Provider-specific settings and their validation are described in
[Integrations](INTEGRATIONS.md). Do not use placeholder credentials outside a
local simulation.

## Repository paths

| Path | Purpose |
|---|---|
| `src/escalane/` | Application package |
| `tests/` | Application contracts |
| `migrations/` | Alembic environment and revisions |
| `pyproject.toml` | Package, test, lint, and build configuration |
| `alembic.ini` | Root migration configuration |
| `deploy/` | Compose deployment and sample seed |

Continue with [Architecture](ARCHITECTURE.md) and
[Operations](OPERATIONS.md).
