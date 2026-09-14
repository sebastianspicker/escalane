# Release Escalane

A tagged release publishes a container image and creates a GitHub prerelease.
The wheel verifies installation and package contents in CI, but it is not the
documented deployment artifact and is not published.

## Prepare the version

1. Choose a strict SemVer prerelease, such as `0.4.0-alpha.1`.
2. Set `escalane.__version__` to that value.
3. Move the matching user-visible notes out of `[Unreleased]` in
   `CHANGELOG.md`.
4. Run the release checks:

   ```bash
   make release-check RELEASE_TAG=v0.4.0-alpha.1
   make check
   make container-check
   ```

`make release-check` confirms that the tag, package version, and changelog
agree. It does not test a real deployment or live providers.

## Check the candidate environment

Record the exact candidate commit before tagging it. Complete the checks that
depend on the target environment:

- provider behavior and idempotency
- TLS and reverse proxy configuration
- secret storage
- backup and restore
- rollback
- data retention
- alert routing
- manual accessibility review

The repository cannot supply evidence for these checks on its own. Keep the
results with the release record so an operator can tell which environment was
tested.

## Tag and publish

Use an immutable candidate commit on `main`. Resolve every required check before
tagging, and make sure the checkout is clean. Create an annotated or signed
`v<version>` tag. Once a tag is published, never move or overwrite it.

The tag starts the release workflow. That workflow verifies that the tagged
commit belongs to `main`, validates the release metadata, runs CI, builds and
tests the container, pushes it to GHCR, and creates the GitHub prerelease.

Deploy and verify the published image by digest:

```bash
export ESCALANE_IMAGE='ghcr.io/sebastianspicker/escalane@sha256:<digest>'
docker compose -f deploy/docker-compose.yml pull migration api worker
docker compose -f deploy/docker-compose.yml up -d --wait postgres redis
docker compose -f deploy/docker-compose.yml run --rm --no-deps migration
docker compose -f deploy/docker-compose.yml up -d --no-deps --force-recreate api worker
curl --fail http://127.0.0.1:8080/readyz
```

The digest identifies the exact image. Mutable version tags are convenient for
discovery, but they are not a reliable deployment identity.

## Account for the alpha limits

The repository does not define a production capacity limit, recovery
objectives, or live provider behavior for your environment. Python dependencies
are pinned in the checked-in constraint files, but every refresh still needs
review and release validation. The release workflow does not create a Software
Bill of Materials. The Compose reference also uses version tags for PostgreSQL
and Redis rather than reviewed digests. Decide and test these details for your
deployment before calling a candidate production-ready.

Alarm export returns at most 2,000 records. It is not intended as a bulk archive
or large-scale export interface.

Before version 1.0, changes may affect HTTP routes, worker payloads, the database
schema, provider contracts, or deployment requirements. Cover those changes
with tests, add migration or operator instructions when needed, and record the
user-visible impact in `CHANGELOG.md`.
