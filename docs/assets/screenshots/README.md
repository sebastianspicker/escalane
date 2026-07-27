# Screenshot review

This directory contains four curated Mock University screenshots:

| File | Required size |
|---|---|
| `01-admin-overview.png` | 1440 pixels wide, at least 720 pixels high |
| `04-admin-alarm-detail.png` | 1440 pixels wide, at least 720 pixels high |
| `06-ack-page-triggered-mobile.png` | 390 pixels wide, at least 700 pixels high |
| `09-simulation-feed.png` | 1440 pixels wide, at least 720 pixels high |

Raw local captures belong in `docs/assets/screenshots/generated/`, which is
ignored and removed by `make clean`.

## Local capture

Start the candidate with `SIMULATION_ENABLED=true`, a loopback `BASE_URL`, and
the worker running. Install Chromium, then run:

```bash
./.venv/bin/python -m playwright install chromium
export ADMIN_API_KEY='<admin-api-key>'
make demo-screens
```

`make demo-screens` reads `ADMIN_API_KEY`. Run
`./.venv/bin/python scripts/demo_capture.py --help` for explicit URL, key,
output, timeout, and headed-browser options.

`--mock-screens` creates one-pixel fixtures for script tests. Do not use that
option for documentation.

## GitHub review workflow

The manual Screenshot review workflow starts the simulation stack, captures
the same four slots, checks their dimensions, and uploads
`screenshot-review/` for seven days. It includes `SOURCE_COMMIT.txt` and does
not commit, push, or publish images.

After downloading the artifact, confirm that UI-related sources have not
changed:

```bash
source_commit="$(cat screenshot-review/SOURCE_COMMIT.txt)"
git diff --exit-code "${source_commit}...HEAD" -- \
  deploy/simulation_seed.yaml \
  scripts/demo_capture.py \
  scripts/demo_prepare.py \
  services/escalane/escalane/api/assets \
  services/escalane/escalane/api/templates
```

## Promotion review

Before replacing a curated file, confirm:

- the capture comes from the intended commit
- only Mock University data is visible
- no key, token, capability URL, personal data, or internal hostname is visible
- the page has no loading, error, stale-state, clipping, or overflow defect
- the image matches the current templates and packaged assets
- every Markdown use has accurate alt text
- keyboard, browser E2E, and relevant manual accessibility checks pass

Copy only the four approved filenames into this directory, then run:

```bash
make hygiene-check
```

Screenshots document the browser surface. They do not establish cross-browser
or accessibility conformance.
