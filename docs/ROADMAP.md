# Roadmap

The current development boundary is a public alpha. Priorities are ordered by
release risk rather than feature count.

## Release gates

1. Run the complete CI and container matrix on an immutable candidate commit.
2. Validate live Zammad, SendXMS, Signal, and webhook behavior, including
   provider-side idempotency.
3. Exercise backup, restore, Redis persistence, migration, and rollback in the
   target environment.
4. Complete manual VoiceOver with Safari and NVDA with Firefox review.
5. Capture the curated screenshots from the exact candidate.
6. Approve deployment-specific secret storage, retention, alerting, recovery
   objectives, and network policy.

## Technical work

- Add a reviewed dependency lock or constraints process for Python 3.14.6.
- Record a Software Bill of Materials for the release image.
- Pin reviewed PostgreSQL and Redis images by digest for a release deployment.
- Replace the bounded 2,000-row alarm CSV export with server-side streaming
  before supporting larger exports.
- Establish capacity limits from measured load and failure tests.

## Compatibility

Pre-1.0 interfaces can change. Changes to HTTP routes, worker payloads,
database schema, connector contracts, or deployment requirements must include
tests, migration guidance where applicable, and an entry in `CHANGELOG.md`.

Current readiness evidence belongs in
[RELEASE_STATUS.md](../RELEASE_STATUS.md), not in this roadmap.
