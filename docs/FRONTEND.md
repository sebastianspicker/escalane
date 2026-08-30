# Frontend

Escalane serves Jinja templates and packaged static assets from the web
adapter. There is no SPA, package-manager command, or frontend bundle.

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

Authenticated form posts require CSRF protection. JavaScript enhances
confirmation, busy states, dialogs, local times, navigation, and revision
polling; it does not authorize actions or decide lifecycle transitions.

## Source layout

- `src/escalane/web/templates/` contains Jinja templates.
- `src/escalane/web/assets/` contains CSS, JavaScript, and SVG.
- `src/escalane/web/assets/ui.css` imports the CSS modules.
- `src/escalane/web/assets/ui.js` contains browser enhancement.
- `src/escalane/web/i18n.py` contains English and German strings.

## Interaction contract

- Use semantic landmarks, one visible `h1`, native buttons, labelled controls,
  tables, description lists, and fieldsets.
- Pair status colour with text and preserve visible `:focus-visible` treatment.
- Return focus to the control that opened a dialog.
- Keep responder controls at least 44 CSS pixels high.
- Avoid page-level horizontal overflow at 320 CSS pixels. Wide tables may
  scroll only inside a labelled container.
- Do not require hover, animation, or JavaScript for a critical action.

## Validation

For user-facing changes, check keyboard access and visible focus; sign-in,
worklist, detail, configuration, and acknowledgement flows; loading, empty,
error, stale-session, and conflict states; 320 CSS pixel reflow; reduced
motion and forced colours; and English and German string parity.

The direct suite does not replace manual screen-reader and target-browser
review.
