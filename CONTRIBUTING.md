# Contributing

Escalane is a public alpha. Discuss large behavior or interface changes in an
issue before implementation.

Do not put credentials, acknowledgement links, device tokens, alarm data,
personal data, or internal hostnames in issues, fixtures, screenshots, logs, or
commits. Report suspected vulnerabilities through
[SECURITY.md](SECURITY.md).

## Development setup

The service requires Python 3.14.x. CI uses Python 3.14.6.

```bash
make install
```

This creates `.venv` and installs `.[dev]` in editable mode.

## Validation

Run the checks relevant to the change:

```bash
make format-check
make lint
make test
make hygiene-check
```

Additional gates:

| Command | Use |
|---|---|
| `DATABASE_URL=postgresql+asyncpg://… YELK_IP_ALLOWLIST=127.0.0.1/32 make test-postgres-smoke` | Schema, migration, or PostgreSQL behavior |
| `make package-check` | Package metadata, templates, or static assets |
| `make audit` | Dependency or security-sensitive changes |
| `make architecture-check` | Internal import boundaries |
| `make coverage` | Test coverage and configured threshold |
| `make container-check` | Docker, migration, startup, or readiness changes |
| `make release-check RELEASE_TAG=v<version>` | Release metadata |

Run the complete local gate before a broad change:

```bash
make pages-build
make check
```

The active suite is organized by feature and boundary:

```text
tests/
├── alarms/          lifecycle, triggers, and ordered outbox contracts
├── configuration/   seed, policy, and redacted admin-audit contracts
├── contracts/       settings, runtime, persistence, and worker boundaries
├── integration/     HTTP, worker, lifecycle, and operator workflows
├── notifications/   provider delivery and audit behavior
├── security/        ingress and HTTP security regressions
├── support/         test-only helpers
└── conftest.py      shared SQLite application fixtures
```

Keep active test source tracked. Local reports, caches, and temporary databases
belong in ignored paths.

Do not describe a skipped check as passing. Record the exact command, result,
and environment limitation in the pull request.

## Change requirements

- Keep the change focused and preserve unrelated work.
- Add or update tests for behavior changes.
- Add Alembic migrations for schema changes. Do not edit an applied migration.
- Keep route, worker payload, outbox ordering, and package-data contracts
  compatible unless the change intentionally revises them.
- Update operational documentation when users or deployers must act
  differently.
- Do not add production dependencies without maintainer agreement.
- Do not reformat unrelated files.

## Pull requests

Use the pull-request template. Explain the operator or maintainer impact,
configuration changes, migration requirements, compatibility risk, and
verification evidence.

Participation is governed by the
[Code of Conduct](CODE_OF_CONDUCT.md).
