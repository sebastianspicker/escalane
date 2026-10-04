# Frontend

The web process renders Escalane's Jinja templates and serves the packaged
static assets. The interface has no SPA, JavaScript package-manager command, or
separate frontend bundle.

## Routes

| Route | Purpose | Access |
|---|---|---|
| `/admin/login` | Operator sign-in | Admin key |
| `/admin` | Alarm worklist | Browser session |
| `/admin/alarms/{alarm_id}` | Alarm context, activity, notes, and actions | Browser session |
| `/admin/alarms/{alarm_id}/drawer` | Progressive worklist detail fragment and action forms | Browser session |
| `/admin/configuration/*` | Resource and escalation configuration | Browser session |
| `/admin/activity` | Recent operational activity | Browser session |
| `/admin/system` | Dependency and runtime status | Browser session |
| `/admin/simulation` | Mock delivery feed | Browser session and simulation mode |
| `/a/{ack_token}` | Responder acknowledgement | Capability token |

Every authenticated form submission must pass CSRF protection. JavaScript adds
filter controls, the detail drawer, confirmation prompts, busy states, dialogs,
local-time display, theme preferences, navigation, and revision polling. The
server still authorizes each action and decides every lifecycle transition. If
JavaScript is unavailable, the full alarm-detail page remains the destination.

## Source layout

- Add or edit Jinja templates in `src/escalane/web/templates/`.
- Keep CSS, JavaScript, and SVG files in `src/escalane/web/assets/`.
- Add CSS modules through `src/escalane/web/assets/ui.css`.
- Put browser enhancements in `src/escalane/web/assets/ui.js`.
- Keep the English and German strings together in `src/escalane/web/i18n.py`.

## Design system

The interface follows the "dark cockpit" direction used here: chrome stays neutral, and hue is spent only
on alarms that still need a person.

- **Colour roles.** Warning red (`--warning*`) marks triggered alarms, errors
  and destructive confirmation. Caution amber (`--caution*`) marks
  acknowledged, still-open alarms and delivery warnings. Resolved and
  cancelled stay unlit. Ink (`--ink`) is the only interactive colour; don't
  add a brand or success hue to controls.
- **Severity is typographic.** P0 is a solid ink mark, P1 an ink outline, and
  P2 plain text, so severity never competes with status colour.
- **Tokens.** `tokens.css` defines the colour, type, spacing, radius and motion
  scales for both themes. Use the custom properties rather than literal
  values.
- **Type.** Atkinson Hyperlegible Next and Mono (SIL OFL, licence in
  `assets/fonts-OFL.txt`) are self-hosted because the CSP allows only
  same-origin fonts. Times, ages, IDs and counts use the mono face.
- **Breakpoints.** The rail collapses into a top bar with a menu sheet at
  60rem. The worklist table becomes two-line entries at 40rem.
- **Static demo.** A dashed rule marks anything simulated or fictional.

## Interface requirements

- Give each page semantic landmarks and one visible `h1`. Prefer native
  buttons, labelled controls, tables, description lists, and fieldsets.
- Never communicate status through colour alone. Pair it with text and retain a
  visible `:focus-visible` style.
- When a dialog closes, return focus to the control that opened it.
- Make responder controls at least 44 CSS pixels high.
- At 320 CSS pixels, the page itself must not scroll horizontally. A wide table
  may scroll inside a labelled container.
- Keep every critical action usable without hover, animation, or JavaScript.

## Validation

For a user-facing change, test keyboard access and visible focus. Exercise
sign-in, the worklist, the drawer and full-detail views, configuration, and
acknowledgement. Cover loading, empty, error, expired-session, and conflict
states. Also check 320 CSS pixel reflow, reduced motion, forced colours, and
English and German string parity.

Automated checks do not replace a manual screen-reader review or testing in the
browsers supported by the target deployment.

The [static demo source](../pages/README.md) reuses selected production assets,
but it does not connect to the service. Its [screenshot tour](../pages/tour.html)
shows the `worklist.png`, `alarm.png`, `acknowledge.png`, and
`simulation.png` captures from `pages/assets/screenshots/`. These images contain
fictional demo data rather than information from a live deployment. Make
changes in `pages/` and then rebuild `build/pages/`; generated demo output is
never the frontend source.
