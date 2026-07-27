# Browser design contract

Escalane uses server-rendered Jinja templates with packaged CSS, JavaScript,
and SVG assets. There is no frontend build step or client-side application
framework.

## Layout

- Authenticated operator pages use a navigation rail and a data-focused work
  area.
- The worklist uses a semantic table, status totals, filters, and explicit bulk
  actions.
- Alarm detail pages place context and activity beside available actions and
  notes.
- The acknowledgement page is a focused single-column responder flow.
- Configuration pages keep resource navigation visible and use native form
  controls.

## Assets

`services/escalane/escalane/api/assets/ui.css` imports:

| File | Responsibility |
|---|---|
| `tokens.css` | Colour and spacing variables |
| `base.css` | Type, forms, buttons, and notices |
| `shell.css` | Guest and authenticated page shells |
| `worklist.css` | Alarm list, filters, and bulk actions |
| `detail.css` | Alarm detail, timeline, actions, and dialogs |
| `ack.css` | Responder acknowledgement flow |
| `auth.css` | Sign-in page |
| `config.css` | Configuration, system, activity, and simulation pages |
| `a11y.css` | Responsive, reduced-motion, and forced-colour rules |

`ui.js` adds confirmation, busy states, dialog focus restoration, local time
rendering, navigation collapse, and revision polling. The server remains the
authority for permissions, state transitions, validation, and content.

## Interaction requirements

- Use semantic landmarks, one visible `h1`, native buttons, labelled controls,
  tables, description lists, and fieldsets.
- Pair status colour with text.
- Preserve visible `:focus-visible` treatment.
- Return focus to the control that opened a dialog.
- Keep critical responder controls at least 44 CSS pixels high.
- Avoid page-level horizontal overflow at 320 CSS pixels. The alarm table may
  scroll inside its labelled container.
- Respect reduced-motion, forced-colour, light, and dark preferences.
- Do not depend on hover, animation, or JavaScript for a critical action.

See [docs/FRONTEND.md](docs/FRONTEND.md) for routes and validation commands.
