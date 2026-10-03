# Contributing

Escalane is a public alpha. Bug reports, documentation fixes, and code changes
are welcome. For a large change to behavior or interfaces, open an issue first
so we can agree on the approach.

Do not put credentials, acknowledgement links, device tokens, alarm data,
personal data, or internal hostnames in issues, fixtures, screenshots, logs, or
commits. Follow [SECURITY.md](SECURITY.md) to report a suspected vulnerability privately.

## Development setup

Use Python 3.14.7 for local development, matching CI and the container image.
The package supports Python 3.14.x. From the repository root, run:

```bash
make install
```

This creates `.venv` and installs the package with its development dependencies
in editable mode, so local source changes are available without reinstalling.

## Running checks

Run the checks relevant to the change:

```bash
make format-check
make lint
make type-check
make test
make hygiene-check
```

Use these checks when the corresponding part of the project changes:

| Command | Use |
|---|---|
| `TEST_POSTGRES_URL='postgresql+asyncpg://alarm:password@127.0.0.1:5432/alarm' YELK_IP_ALLOWLIST='127.0.0.1/32' make test-postgres-smoke` | Schema, migration, or PostgreSQL behavior against a disposable database |
| `make package-check` | Package metadata, templates, or static assets |
| `make constraints-check` | Python dependency declarations, exact pins, or toolchain versions |
| `make audit` | Dependency or security-sensitive changes |
| `make architecture-check` | Internal import boundaries |
| `make coverage` | Test coverage and configured threshold |
| `make container-check` | Docker, migration, startup, or readiness changes |
| `make release-check RELEASE_TAG=v<version>` | Release metadata |

Before submitting a change that affects several parts of the project, run
the full set:

```bash
make check
```

`make check` includes the Pages build and validation. Its dependency audit
requires access to the configured advisory service.

Tests are grouped by feature and by the parts of the system they exercise:

```text
tests/
├── alarms/          lifecycle, triggers, and ordered outbox contracts
├── config/          settings validation and domain errors
├── configuration/   seed, policy, and redacted admin-audit contracts
├── notifications/   delivery, dispatch, and audit behavior
├── operations/      readiness, metric queries, and worker snapshots
├── persistence/     engine, pool, and migration behavior
├── postgres/        opt-in live PostgreSQL concurrency checks
├── providers/       Zammad, SendXMS, Signal, and webhook transport
├── repository/      tooling, architecture, documentation, and Pages contracts
├── runtime/         Redis atomics and rate-limit keys
├── security/        ingress, URL validation, and HTTP security regressions
├── support/         test-only fakes, factories, clients, and helpers
├── telemetry/       metric identity and bounds
├── web/             HTTP routes, admin console sessions, and public route table
├── worker/          ARQ tasks, registration, and resource ownership
└── conftest.py      shared SQLite application fixtures
```

Commit test source along with your changes, and keep generated reports, caches,
and temporary databases in ignored paths. In your pull request, list the
commands you ran and their results. If you could not run a check, explain why.

## Making a change

- Keep the change focused on the problem you are solving.
- Add or update tests for behavior changes.
- Add Alembic migrations for schema changes. Do not edit an applied migration.
- Preserve existing routes, worker payloads, outbox ordering, and packaged
  assets unless the change is intended to alter them.
- Update the docs when a change affects setup or day-to-day operation.
- Do not add production dependencies without maintainer agreement.
- Do not reformat unrelated files.

## Pull requests

Use the pull-request template to explain what changes for users and why.
Call out any new configuration, required migration, or compatibility concern,
and include your test results.

Participation is governed by the
[Code of Conduct](CODE_OF_CONDUCT.md).
