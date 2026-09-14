# Set up Escalane

You can run Escalane with Docker Compose or directly from a local Python
environment. Both options need PostgreSQL and Redis. The standard test suite
uses isolated fixtures, while separate checks cover live PostgreSQL and Redis.

The Compose deployment needs Docker Engine and the Compose plugin. The package
supports Python `>=3.14,<3.15`, while the repository bootstrap uses CPython
3.14.7. Local development also needs GNU Make and pip.

## Run with Docker Compose

Start by copying the example environment file:

```bash
cp .env.example .env
```

Open `.env` and replace `POSTGRES_PASSWORD=CHANGE_ME` with a strong password.
Use the same password in `DATABASE_URL`, then set `ADMIN_API_KEY` and
`YEALINK_DEVICE_TOKEN` to separate random values. Add credentials for every
provider you plan to enable.

For local testing with simulated delivery, use:

```text
BASE_URL=http://localhost:8080
SIMULATION_ENABLED=true
SIGNAL_TARGET_GROUP_ID=demo-group
```

Use `demo-group` only for this simulated setup. The sample seed reads both its
device token and Signal target from these environment settings, even though
provider delivery is simulated.

Build and start the stack, then wait for the readiness check to pass:

```bash
docker compose -f deploy/docker-compose.yml up -d --build
curl --fail http://127.0.0.1:8080/readyz
```

Compose starts PostgreSQL, Redis, a one-time migration service, the API, and the
worker. The `/readyz` endpoint succeeds only after PostgreSQL and Redis are
available and the database has the expected schema revision.

You can load the sample data after the stack is ready:

```bash
curl --fail \
  -H "X-Admin-Key: <admin-api-key>" \
  -H "Content-Type: application/yaml" \
  --data-binary @deploy/seed.example.yaml \
  http://127.0.0.1:8080/v1/admin/seed
```

The sample contacts are placeholders. Replace them before testing a real
provider.

## Run a local development environment

Direct processes need `DATABASE_URL` and `REDIS_URL` values that the host can
reach. The `postgres` and `redis` hostnames in `.env.example` resolve only on the
Compose network, and the reference stack does not publish either service port.
Use separate reachable PostgreSQL and Redis instances, then create the virtual
environment, install the project, apply its migrations, and start the
reloadable API from the repository root:

```bash
make install
.venv/bin/alembic upgrade head
make dev
```

Start the worker in another shell:

```bash
.venv/bin/arq escalane.worker.settings.WorkerSettings
```

`make install` creates `.venv` and installs Escalane with its development
dependencies in editable mode. Both direct processes read settings from the
shell environment and, when present, the root `.env` file. Always apply
migrations before starting the API or worker. Run `make test`, `make lint`,
`make type-check`, and `make package-check` for the main local checks.

## Configure the runtime

Runtime settings are defined in `src/escalane/config/`. These are the main
variables you will usually set:

| Variable | What it controls |
|---|---|
| `DATABASE_URL` | PostgreSQL connection string |
| `REDIS_URL` | Redis connection for ARQ and temporary state |
| `BASE_URL` | Public origin used to build acknowledgement links |
| `ADMIN_API_KEY` | Credential for the admin API and operator sign-in |
| `YELK_IP_ALLOWLIST` | Source CIDRs allowed to trigger devices outside simulation |
| `YELK_TOKEN_QUERY_PARAM` | Query parameter name used for device triggers |
| `RATE_LIMIT_PER_MINUTE` | Per-device trigger limit |
| `TRUSTED_PROXY_CIDRS` | Proxy peers allowed to supply forwarded headers |
| `SIMULATION_ENABLED` | Enables mock delivery and simulation routes |
| `ENABLE_API_DOCS` | Enables OpenAPI and the interactive API documentation |

[Integrations](INTEGRATIONS.md) lists the provider settings and their validation
rules. Placeholder credentials are suitable only for local simulation.

## Work with pinned dependencies

Local setup, CI, and the container image currently use CPython 3.14.7.
Compatibility ranges live in `pyproject.toml`. Exact pins are split by purpose:

| File | Contents |
|---|---|
| `constraints/python314-build.txt` | Build tools |
| `constraints/python314-runtime.txt` | Runtime dependencies |
| `constraints/python314-dev.txt` | Development and test dependencies |

Local installation, wheel validation, and container builds all read these
files. Build isolation also uses `PIP_BUILD_CONSTRAINT`, which keeps setuptools
and wheel resolution consistent.

To refresh the pins, use an installed development environment and run:

```bash
make constraints-refresh
make install
make constraints-check
make check
make container-check
```

Review all three constraint files together. The refresh command resolves the
current compatibility ranges in temporary environments, so handle its output
like any other dependency update. `make constraints-check` compares the pins,
checks shared runtime and development versions, verifies that the expected
workflows consume them, and confirms the Python patch version. On a host with
Docker, `make container-check` also tests Linux installation and runs API and
worker smoke checks.

## Run service-backed and synthetic checks

`make test-postgres-smoke` needs an explicit `TEST_POSTGRES_URL` and
`YELK_IP_ALLOWLIST`. It applies migrations and runs the outbox and dashboard
concurrency checks, so point it only at a disposable test database. Follow
[Operations](OPERATIONS.md) for the full service-backed procedure.

You can run synthetic comparisons without external services:

```bash
.venv/bin/python scripts/benchmark_optimizations.py --output /tmp/escalane-reads.json
.venv/bin/python scripts/benchmark_delivery.py --output /tmp/escalane-fanout.json
```

These commands use temporary SQLite databases and synthetic providers by
default. Their results apply only to that database engine and workload. See
[Performance validation](OPTIMIZATION_VALIDATION.md) for the recorded results
and the PostgreSQL checks that remain necessary.

## Find your way around the repository

| Path | Contents |
|---|---|
| `src/escalane/` | Application package |
| `tests/` | Application contracts and tests |
| `migrations/` | Alembic environment and revisions |
| `pyproject.toml` | Package, test, lint, and build configuration |
| `alembic.ini` | Root migration configuration |
| `deploy/` | Compose deployment and sample seed |

For more context, read [Architecture](ARCHITECTURE.md) and
[Operations](OPERATIONS.md).
