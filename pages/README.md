# Static demo source

This directory contains a deliberately small, static projection of Escalane's
operator and responder workflow for GitHub Pages. It uses the sanitized Mock
University scenario from `deploy/simulation_seed.yaml`.

The demo is not a separate application. The Pages build copies the production
styles, script, and mark from `src/escalane/web/assets/`; generated
output is not committed. The static HTML covers only the worklist, alarm detail,
responder acknowledgement, and simulation feed. Every state-changing control is
marked as simulated and operates only on in-browser fixture state.

Build the artifact locally with:

```bash
python scripts/build_pages.py
```
