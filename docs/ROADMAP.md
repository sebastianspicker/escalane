# Roadmap

## Guiding Principles

- No breaking changes to existing public endpoints.
- Stability and API consistency take precedence over feature breadth.
- UI improvements are additive and compatible with existing paths.

## Completed History

Completed phase history is archived in
[docs/archive/roadmap-history.md](archive/roadmap-history.md). This file tracks
the active backlog and definition of done for the public release surface.

## Backlog

1. ~~Extended search/filter options for admin operations.~~ ✅ Already implemented (person_id, room_id, device_id, created_after, created_before, severity filters on `GET /v1/alarms`).
2. ~~Distributed tracing (propagate request_id to worker tasks).~~ ✅ Phase 22.
3. ~~Query performance logging.~~ ✅ Phase 22.
4. ~~Connection pool tuning for production deployments.~~ ✅ Phase 22.
5. Streaming export — `GET /v1/alarms/export` currently buffers up to 2 000 rows in memory before writing the response. Implement true server-side streaming to handle larger exports without memory pressure.
6. ~~IP allowlist hardening — `YELK_IP_ALLOWLIST` defaults to blank (all IPs accepted). Add a startup `UserWarning` in `Settings` when the allowlist is empty and `simulation_enabled` is `False`.~~ ✅ Implemented (`warn_empty_ip_allowlist` model validator in `settings.py`).
7. ~~Trigger response normalisation — unknown-token (404) vs. incomplete-mapping (409) leaks token validity to callers. Normalise both to a single 404 to prevent token-probing.~~ ✅ Implemented (always returns 404 in `trigger_service.py`).

## Definition of Done

- Consolidated documentation in `docs/` with `docs/README.md` as index.
- Unified Notes route and stable simulation endpoints with tests.
- UI flows without known runtime errors in core paths.
- Full green quality gates (lint + tests + mypy + coverage ≥ 93%).
- Clean layer boundaries (zero backwards imports).
- Zero deprecation warnings in test output.
