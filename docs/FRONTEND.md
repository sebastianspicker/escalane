# Frontend architecture and release checks

The browser interface is server-rendered FastAPI/Jinja HTML with one packaged stylesheet and one progressive-enhancement script. There is no JavaScript build system or client-side framework.

## Routes

- `/admin/login`: bilingual named operator session login
- `/admin`: filtered, paginated alarm worklist and bulk/export actions
- `/admin/alarms/{alarm_id}`: deep-linkable lifecycle, notes, delivery history, and actions
- `/admin/configuration/{sites|rooms|people|devices|escalation|import}`: versioned configuration workflows
- `/admin/simulation`, `/admin/system`, `/admin/activity`: operational views
- `/a/{ack_token}`: standalone responder acknowledgement
- `/admin/assets/ui.css` and `/admin/assets/ui.js`: same-origin packaged assets

The static admin key is exchanged only at login. Browser mutations use the named Redis session plus a separate CSRF token and POST/Redirect/GET. Existing `/v1/*` routes remain authenticated by `X-Admin-Key` and do not accept the browser session as authority.

## Interface conventions

Templates inherit `base.html`; translation keys live in `api/i18n.py` and must have exact German/English parity. Locale precedence is explicit `lang`, locale cookie, `Accept-Language`, then English. System fonts and system light/dark preference are used. JavaScript may manage native dialogs, busy labels, local time display, and revision polling, but forms remain the baseline.

Polling runs every 15 seconds, does not extend the session, compares only an opaque revision, and never navigates automatically. It pauses while the page is hidden, a form is dirty, or a dialog is open.

## Release checks

Run Ruff format/check, strict mypy, the non-E2E suite with project coverage threshold, served HTTP E2E, Alembic/PostgreSQL smoke, security/package audits, wheel smoke, and the Docker build. Browser release checks cover Chromium, Firefox, and WebKit at 320, 390, 768, 1280, and 1440 px, keyboard/focus restoration, reduced motion, forced colours, CSP violations, external requests, and console errors.

Install the declared development dependencies and browser engines with `python -m playwright install chromium firefox webkit`, then run `make browser-e2e`. CI installs all three engines before the complete E2E job.

Playwright coverage and manual VoiceOver/Safari and NVDA/Firefox verification are still required before calling the RC browser UI fully release-verified.

## Current local verification snapshot

On 2026-07-11, direct headless Chrome verification covered 390×844 and 1280×800 rendering, login, worklist, alarm detail, native dialog opening and focus restoration, and the German responder acknowledgement flow. The run found no page-level horizontal overflow, CSP violations, external requests, or browser console errors; the dense worklist table scrolled within its labelled container and the responder action measured 44.78 px high. Chromium/Firefox/WebKit Playwright and manual screen-reader checks remain separate release gates.

The same local hardening pass completed Ruff, strict mypy, Bandit, dependency
audit, served HTTP E2E, wheel packaging, and the non-E2E suite at 93.86%
coverage. The final cleanup removed the obsolete `string.Template`
compatibility path and verifies the actual packaged Jinja templates and assets.
Docker, PostgreSQL migration smoke, the complete Playwright matrix, and manual
screen-reader checks remain separate release evidence.
