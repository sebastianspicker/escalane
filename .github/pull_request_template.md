## Summary

<!-- What changed, and why? Link the issue or task when possible. -->

## Risk and runtime impact

<!-- Note affected endpoints, workers, DB schema, Redis/arq jobs, connectors, auth/session behavior, ACK links, or operator-visible states. Write "None" only after checking. -->

## Type of change

- [ ] Bug fix
- [ ] New feature
- [ ] Refactor / cleanup
- [ ] Documentation
- [ ] Security / hardening
- [ ] CI / release / dependency maintenance
- [ ] Other:

## Verification

<!-- List exact commands run and results. If skipped, explain why. -->

- [ ] `make lint`
- [ ] `python -m mypy --config-file services/escalane/pyproject.toml services/escalane/escalane`
- [ ] `make hygiene-check`
- [ ] `make test`
- [ ] `make e2e` when user-facing HTTP/browser flows changed
- [ ] `make test-postgres-smoke` when DB models, migrations, or persistence behavior changed
- [ ] `make audit` when dependencies, auth, network egress, secrets, parsing, or security controls changed
- [ ] `make package-check` when Python packaging or packaged templates/assets changed
- [ ] `make release-check RELEASE_TAG=v<version>` when version, changelog, or release metadata changed
- [ ] `make container-check` when Docker, migration startup, or readiness behavior changed

## Release notes

- [ ] `CHANGELOG.md` updated for user-facing, operational, security, or compatibility changes
- [ ] Docs updated for changed endpoints, env vars, deployment steps, runtime semantics, or operator guidance
- [ ] No release note needed because:
