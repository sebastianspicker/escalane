# Frontend

Escalane uses Jinja templates and packaged static assets served by FastAPI.
There is no SPA, package-manager step, or frontend bundle.

## Routes

| Route | Purpose | Access |
|---|---|---|
| `/admin/login` | Operator sign-in | Admin key |
| `/admin` | Alarm worklist | Browser session |
| `/admin/alarms/{alarm_id}` | Alarm context, activity, notes, and actions | Browser session |
| `/admin/configuration/*` | Resource and escalation configuration | Browser session |
| `/admin/activity` | Recent operational activity | Browser session |
| `/admin/system` | Dependency and runtime status | Browser session |
| `/admin/simulation` | Mock delivery feed | Browser session and simulation mode |
| `/a/{ack_token}` | Responder acknowledgement | Capability token |

Authenticated form posts require a CSRF token. JavaScript enhances confirmation,
busy states, dialogs, local timestamps, navigation, and revision polling; it
does not authorize an action.

## Source layout

- `services/escalane/escalane/api/templates/` contains Jinja templates.
- `services/escalane/escalane/api/assets/` contains CSS, JavaScript, and SVG.
- `services/escalane/escalane/api/assets/ui.css` imports the CSS modules.
- `services/escalane/escalane/api/assets/ui.js` contains browser enhancement.
- `services/escalane/escalane/api/i18n.py` contains English and German strings.

These resources are included in the wheel by
`services/escalane/pyproject.toml`.

## Validation

For user-facing changes, also check:

- keyboard access and visible focus
- sign-in, worklist, detail, configuration, and acknowledgement flows
- loading, empty, error, stale-session, and conflict states
- 320 CSS pixel reflow and page-level horizontal overflow
- reduced motion, forced colours, and light and dark schemes
- English and German string parity

The compact direct suite does not automate browser interaction. Manual
screen-reader and target-browser review remains appropriate for UI changes.

See [../DESIGN.md](../DESIGN.md) for the browser interaction contract.
