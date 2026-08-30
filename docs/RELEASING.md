# Release process

Tagged releases publish a container image and GitHub prerelease. The wheel is
an installation and package-integrity artifact, not the documented deployment
artifact.

## Prepare

1. Choose a strict SemVer prerelease, such as `0.4.0-alpha.1`.
2. Set `escalane.__version__` to that value.
3. Move the matching user-visible change notes out of `[Unreleased]` in
   `CHANGELOG.md`.
4. Run:

   ```bash
   make release-check RELEASE_TAG=v0.4.0-alpha.1
   make check
   make container-check
   ```

The release check validates tag syntax, package version, and changelog
agreement. It does not establish deployment or provider readiness.

## Freeze and publish

Use an immutable candidate commit on `main`. Do not tag a dirty checkout or a
candidate with an unresolved required gate. Create an annotated or signed
`v<version>` tag. Do not move or overwrite a published tag.

Publish and verify a digest, not a mutable image tag:

```bash
export ESCALANE_IMAGE='ghcr.io/sebastianspicker/escalane@sha256:<digest>'
docker compose -f deploy/docker-compose.yml pull migration api worker
docker compose -f deploy/docker-compose.yml up -d --wait postgres redis
docker compose -f deploy/docker-compose.yml run --rm --no-deps migration
docker compose -f deploy/docker-compose.yml up -d --no-deps --force-recreate api worker
curl --fail http://127.0.0.1:8080/readyz
```

Before release, record the exact candidate commit and complete
target-environment checks for provider behaviour and idempotency, TLS and proxy
configuration, secret storage, backup and restore, rollback, retention, alert
routing, and manual accessibility review.
