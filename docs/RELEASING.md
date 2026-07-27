# Release process

Tagged releases publish a GHCR container image and a GitHub prerelease. CI
builds and inspects a wheel, but the workflow does not publish it to a package
index or attach it to the release.

## Prepare

1. Choose a strict SemVer prerelease such as `0.4.0-alpha.1`.
2. Set `escalane.__version__` to that value.
3. Move user-visible entries from `[Unreleased]` to the matching heading in
   `CHANGELOG.md`.
4. Update `RELEASE_STATUS.md` with evidence from the candidate commit.
5. Capture and review the four images described in
   [assets/screenshots/README.md](assets/screenshots/README.md).
6. Run:

   ```bash
   make release-check RELEASE_TAG=v0.4.0-alpha.1
   ```

`scripts/validate_release.py` checks strict tag syntax, package version, and a
matching changelog heading. It does not prove that CI, deployment, or
connector checks passed.

## Freeze and verify

Merge the candidate to `main` and use that immutable commit for release
evidence. Required automated gates are defined in `.github/workflows/ci.yml`:

- public-file hygiene
- Ruff formatting and lint
- mypy
- non-E2E tests and the 93 percent coverage threshold
- HTTP and browser E2E with Chromium, Firefox, and WebKit
- PostgreSQL and Alembic smoke
- Bandit and `pip-audit`
- wheel build and resource smoke
- container migration and readiness smoke

Complete target-environment checks that CI cannot establish: live connectors,
provider idempotency, TLS and proxy behavior, secrets and retention, backup and
restore, rollback, alert routing, and manual accessibility review.

Do not tag a dirty checkout, a branch-only commit, or a candidate with an
unresolved required gate.

## Publish

Create an annotated or signed `v<version>` tag on a commit reachable from
`origin/main`. The release workflow rejects lightweight tags and mismatched
metadata.

The workflow reruns reusable CI, builds the release image, runs migration and
readiness smoke against that exact image, pushes SemVer-derived tags to GHCR,
resolves the immutable digest, and creates a GitHub prerelease.

Do not move or overwrite a published tag.

## Verify the published image

Deploy the digest, not a mutable tag:

```bash
export ESCALANE_IMAGE='ghcr.io/sebastianspicker/escalane@sha256:<digest>'
docker compose -f deploy/docker-compose.yml pull migration api worker
docker compose -f deploy/docker-compose.yml up -d --wait postgres redis
docker compose -f deploy/docker-compose.yml run --rm --no-deps migration
docker compose -f deploy/docker-compose.yml up -d --no-deps --force-recreate api worker
curl --fail http://127.0.0.1:8080/readyz
```

Exercise trigger, operator, responder, worker, connector, and rollback paths in
the target environment. Confirm that the GitHub release is marked as a
prerelease and records the image digest and known limitations.

If verification fails, stop the rollout and publish a new prerelease after the
fix passes the complete process.
