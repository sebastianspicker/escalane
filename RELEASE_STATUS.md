# Release status

Evidence cutoff: 2026-08-14, local checkout

Verdict: local alpha candidate, not ready to tag

## Candidate

- Version: `0.4.0-alpha.1`
- Proposed tag: `v0.4.0-alpha.1`
- Python distribution: `escalane`
- Container repository: `ghcr.io/sebastianspicker/escalane`
- Current checkout: not designated as an immutable release identity
- Published artifact: none recorded in this checkout

## Implemented scope

The candidate includes HTTP trigger intake, PostgreSQL lifecycle state, Redis
and ARQ background work, connector delivery, delayed escalation,
capability-link acknowledgement, an operator console, configuration import,
simulation, and ordered outbox recovery.

## Verified local evidence

- `make test`: 625 passed, eight skipped, four browser E2E cases deselected,
  and 95.25 percent coverage against the 93 percent threshold.
- `make lint`: Ruff formatting and lint passed for 160 service and script
  files.
- `python -m mypy --config-file services/escalane/pyproject.toml
  services/escalane/escalane`: passed for 74 application files.
- `make package-check`: the wheel built with its license, templates, and
  browser assets.
- `pip-audit` reported no known vulnerability in the installed dependencies;
  it skipped only the unpublished local `escalane` package.
- Production Bandit and the medium-or-higher script audit passed.
- `make hygiene-check` passed for 243 candidate files and
  `make release-check RELEASE_TAG=v0.4.0-alpha.1` passed.
- The Pages artifact built and validated, and its four HTML routes and four
  principal CSS/JavaScript assets returned HTTP 200 from a loopback server.

These results describe the current local checkout. They are not release
evidence and must be replaced by results from the immutable candidate commit.

## Repository gates

- Review and freeze the complete candidate tree.
- Install the pinned Playwright browser binaries and rerun the Chromium,
  Firefox, and WebKit E2E cases. The served-HTTP E2E case passed locally, but
  all three browser launches were blocked by missing executables.
- Run PostgreSQL migration smoke and exact-image container readiness smoke.
- Run the complete GitHub CI workflow on the approved commit.
- Capture and review screenshots from the exact candidate.
- Record the tested GHCR image by immutable digest.

## Deployment-owner evidence

The deployment owner must record the environment and evidence for:

- live Zammad, SendXMS, Signal, and webhook delivery
- provider-side idempotency and residual duplicate handling
- TLS, proxy trust, ingress, and egress policy
- secret storage and rotation
- PostgreSQL backup and restore
- Redis persistence and recovery
- schema and application rollback
- retention and deletion policy
- monitoring and alert routing
- manual accessibility and target-browser review

## Accepted alpha limitations

- External delivery is at least once and can be duplicated around an audit
  persistence failure.
- Alarm CSV export is limited to 2,000 rows per request.
- Live connector behavior depends on deployment-owner validation.
- The wheel is packaging evidence, not the documented release artifact.
- The project is not validated for safety-critical or compliance-critical use.

## Next gate

Complete repository checks, resolve all failures, and run the full CI and
container matrix on an immutable reviewed commit before creating an annotated
tag.
