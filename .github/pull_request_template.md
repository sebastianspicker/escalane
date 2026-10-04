## What changed and why?

<!-- Describe the problem and how this change addresses it. Link a related issue if there is one. -->

## What should reviewers know?

<!-- Explain any effect on endpoints, workers, database schema, Redis/ARQ jobs, providers, authentication, acknowledgement links, or the operator interface. Include migration and configuration steps. If none of these change, say so. -->

## Type of change

- [ ] Bug fix
- [ ] New feature
- [ ] Refactor or cleanup
- [ ] Documentation
- [ ] Security
- [ ] CI, release, or dependency maintenance
- [ ] Other:

## How did you test it?

<!-- List the commands you ran and their results. Explain any failed or skipped checks. Check a box only when that check passed. -->

- [ ] `make lint`
- [ ] `make type-check`
- [ ] `make hygiene-check`
- [ ] `make audit` for changes to dependencies, authentication, outbound requests, secrets, parsing, or security controls
- [ ] `make package-check` for changes to Python packaging or packaged templates and assets
- [ ] `make release-check RELEASE_TAG=v<version>` for changes to the version, changelog, or release metadata
- [ ] `make container-check` for changes to Docker, migration startup, or readiness checks

## Documentation and release notes

- [ ] Updated `CHANGELOG.md` for changes that affect users, operations, security, or compatibility
- [ ] Updated the docs for changes to endpoints, settings, deployment, or operation
- [ ] No release note is needed because:
