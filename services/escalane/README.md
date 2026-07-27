# Escalane Python service

This package contains the FastAPI application, ARQ worker, connectors,
SQLAlchemy models, Alembic migrations, Jinja templates, and browser assets.

Requires Python 3.14.x.

## Entry points

- ASGI: `escalane.api.main:app`
- ARQ: `escalane.worker.settings.WorkerSettings`
- Alembic: `alembic upgrade head`

## Source map

| Path | Responsibility |
|---|---|
| `escalane/api/` | HTTP routes, dependencies, templates, and assets |
| `escalane/services/` | Trigger, lifecycle, seed, outbox, and delivery logic |
| `escalane/worker/` | Background jobs and recovery |
| `escalane/connectors/` | External delivery clients |
| `escalane/db/` | Engine, sessions, and models |
| `alembic/` | Schema migration environment and revisions |
| `tests/unit/` | Isolated service, connector, settings, and worker tests |
| `tests/integration/` | API, database, Redis, outbox, and PostgreSQL tests |
| `tests/security/` | Security regression tests |
| `tests/repository/` | Script, workflow, package, documentation, and release contracts |
| `tests/e2e/` | Served HTTP and browser tests |

Repository-level installation, configuration, and validation commands are in
[../../docs/SETUP.md](../../docs/SETUP.md) and
[../../CONTRIBUTING.md](../../CONTRIBUTING.md).
