# Roadmap

Escalane remains a public alpha. Priorities are ordered by release risk.

## Release gates

1. Run the full build, lint, test, package, and container checks on an
   immutable candidate commit.
2. Validate Zammad, SendXMS, Signal, and webhook delivery in the target
   environment, including provider-side idempotency.
3. Exercise backup, restore, Redis recovery, schema migration, and rollback.
4. Complete manual accessibility and target-browser review of operator and
   responder flows.
5. Approve deployment-specific secret storage, retention, alerting, recovery
   objectives, and network policy.

## Technical work

- Establish capacity limits through measured load and failure tests.
- Add a reviewed dependency lock or constraints process for Python 3.14.
- Record a Software Bill of Materials for the release image.
- Pin reviewed PostgreSQL and Redis images by digest for release deployment.
- Replace the bounded alarm CSV export before supporting larger exports.

## Compatibility

Pre-1.0 interfaces can change. Changes to HTTP routes, worker payloads,
database schema, provider contracts, or deployment requirements need tests,
migration guidance where applicable, and a `CHANGELOG.md` entry.
