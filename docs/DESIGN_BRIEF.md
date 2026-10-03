# Design brief: Escalane interface

This brief records the reasoning behind the operator console, responder page
and static demo design. It covers the product, audience, constraints and
chosen direction. Implementation rules live in [FRONTEND.md](FRONTEND.md).

## 1. Product summary

Escalane is a self-hosted alarm intake and escalation engine. A device (today
a Yealink-compatible handset with an emergency key) sends an HTTP trigger. The
service records an alarm in PostgreSQL, notifies Zammad, SMS (SendXMS), Signal
or a webhook, and escalates through ordered steps until someone acknowledges
it. Operators work the alarm in a server-rendered console. Responders receive a
capability link (`/a/{token}`) and confirm receipt on their phone.

The product has three surfaces:

| Surface | Route | Device | Situation |
|---|---|---|---|
| Operator worklist, detail, drawer | `/admin`, `/admin/alarms/{id}` | Desk monitor, often all shift | Watching, triaging, closing |
| Responder acknowledgement | `/a/{token}` | Phone, from an SMS or Signal link | Interrupted, moving, possibly stressed |
| Configuration, audit, system, simulation | `/admin/configuration/*`, `/admin/activity`, `/admin/system`, `/admin/simulation` | Desk | Occasional, deliberate maintenance |

A static GitHub Pages demo (`pages/`) reuses the production CSS and JS and is
the public face of the project.

**Moment of value.** For the operator, value arrives when the worklist answers
"is anyone still waiting for help, and for how long?" at a glance. For the
responder, it arrives when a single tap confirms "I have this" and the page
says so without doubt.

## 2. Audience

**Primary: the desk operator.** This is a security desk, gatehouse or
facilities control room operator at a campus-type organisation (university,
care campus, public building). They are not developers. They watch the
worklist for hours, often next to CCTV, telephony and a ticketing tool
(Zammad). They speak German or English. They need to:

- see immediately whether an alarm is unacknowledged, and how old it is;
- know who and where (person, room) without decoding IDs;
- act (acknowledge, resolve, cancel with reason, add a note) with no
  ambiguity, and be sure the action was recorded.

They worry about missing an alarm, acting on the wrong row, and being blamed
later, which is why every action is audited. They distrust anything that looks
like marketing, decoration that competes with alarms, and interfaces that are
"always red". Quality, to them, means calm, legible, predictable and fast.

**Secondary: the responder.** This could be a caretaker, first-aider, duty
manager or nurse on call. They open an SMS or Signal link on a phone, possibly
outdoors, in a stairwell or wearing gloves. They need one large, unmistakable
action and confirmation that it worked. They must not be asked to sign in.

**Tertiary: the evaluator.** This is a developer or IT lead reading GitHub and
the Pages demo, deciding whether to deploy. They judge quality by how
deliberate and honest the product looks.

## 3. Key journeys

1. **Triage (primary).** Worklist → spot an open alarm → open the drawer →
   acknowledge → see the confirmation → later resolve with a note.
2. **Respond.** SMS or Signal link → responder page → (optional name and note)
   → Acknowledge → confirmation.
3. **Investigate.** Worklist filter or search → full alarm detail → activity
   timeline → older activity.
4. **Bulk close.** Select rows → acknowledge, resolve or cancel with a
   reason.
5. **Maintain.** Configuration resources, escalation policy JSON, YAML import
   preview and apply, audit log, system readiness, simulation feed.

## 4. Brand traits

| Trait | Not |
|---|---|
| **Calm.** Quiet by default, so that what is lit means something | Not sleepy or low-contrast |
| **Unambiguous.** One meaning per colour, one primary action per screen | Not shouty or alarmist |
| **Accountable.** Every time, actor and ID is visible and exact | Not bureaucratic or form-heavy |
| **Instrument-grade.** Built like equipment people depend on | Not cold, militaristic or "hacker" |
| **Honest.** Says what it is: a public alpha, fictional demo data | Not apologetic or cute |

## 5. Market observations

Category reference points, reasoned from knowledge of the products rather
than a fresh crawl: PagerDuty, Opsgenie, incident.io, Grafana OnCall and
Rootly (developer on-call); nurse-call and alarm servers from Ascom, Hillrom
and similar vendors; and building-management and fire-panel software.

Category conventions:

- A dark brand-coloured sidebar, a light content area, and tinted status pills
  in red, amber and green. Colour is used for branding, navigation, statuses
  and success messages at once.
- Dashboards full of KPI cards and charts, with "green is good" everywhere.
- Inter or a system UI font, rounded cards and soft shadows.
- Vendor alarm software looks dated and dense, and is often red-saturated.

**Honour:** red means an active, unacknowledged problem; amber means
caution; tabular, monospaced times and IDs; dense tables for desk work; one
big button on mobile.

**Break:** colour as decoration. In the category, the brand colour and green
"OK" states compete with alarms. Control-room practice says otherwise: ISA-101
high-performance HMI guidance and the aviation "dark cockpit" principle keep
normal states grey, so colour only appears for abnormal states that need a
human.

## 6. Current state

- **Stack:** Jinja templates, modular plain CSS (`tokens.css`, `base.css`,
  `shell.css`, page modules), one vanilla `ui.js`, no build step, CSP
  `default-src 'self'`. The Pages demo copies the same assets.
- **Tokens:** a light and dark colour set exists, but with duplicated aliases
  (`--danger`/`--triggered`, `--muted`/`--ink-muted`, `--acked`). There are no
  type, space or motion scales; sizes are ad hoc (`0.72rem`, `0.7rem`,
  `0.6875rem` all coexist).
- **Brand assets:** the logo (`escalane-mark.svg`) shows three stepped lanes
  in teal, each ending in an amber dot, which is a literal escalation ladder.
  The name "Escalane" is escalate + lane. **Keep and evolve:** the mark is
  distinctive and meaningful.
- **Fonts:** system stack ("Segoe UI", "Helvetica Neue"), so the product
  looks different on every OS and has no typographic identity.

**Weaknesses:**

1. The navy-teal rail is the loudest element on a quiet screen, and it is the
   generic SaaS look.
2. Colour is spent everywhere: a teal brand, green "resolved", P2 in teal, a
   pink row wash for P0, and severity chips that look like status. A resolved
   alarm is as colourful as a triggered one, so the eye can't triage.
3. Severity and status compete. Both use red and amber fills.
4. The status counts are four equal boxes; "3 triggered" has no more weight
   than "1 cancelled".
5. On mobile, the worklist is a horizontally scrolling table (person and room
   are cut off), counts stack into a tall list, and the export buttons and
   language form crowd the header.
6. The responder page has a weak headline ("Acknowledge alarm" is the
   button's label, not a situation), a teal button on a red band, a full-width
   form card, and a desktop footer.
7. Unstyled states: `notice-warning` (the delivery-pending flash) has no
   style. The detail page never shows flash confirmations, and the flash keys
   `alarm_acknowledged*`, `note_added`, `alarm_deleted` and `bulk_*` are
   missing from the catalogue (pre-existing bug noted in the handoff).
8. Inconsistent sizes, label styles and radii across modules.

## 7. Constraints (load-bearing)

- Routes, form fields, CSRF hidden inputs, `data-*` hooks used by `ui.js`
  and `demo.js`, and the drawer/dialog/history fragment structure stay intact.
- Tests pin markup: `class="severity-filter"`, `aria-current="page">P0</a>`,
  `data-theme-toggle`, `>Owner<`, `<h1>Simulation</h1>`, `Acknowledge alarm`
  on the responder page, `notice notice-success` / `notice notice-warning`,
  `class="resource-record"`, `<a class="button" href=…>Next page <span`, and
  `data-drawer-fragment`.
- CSP `default-src 'self'`: fonts must be self-hosted. They are packaged via
  `pyproject.toml` package data and copied to Pages by `build_pages.py`.
- English and German string parity in `i18n.py`.
- FRONTEND.md requirements: one `h1`, no colour-only status, visible focus,
  44px responder controls, no page scroll at 320px, works without JS,
  forced colours, reduced motion.
- The Pages validator requires every demo button to carry
  `data-simulated-action` and a visible "Simulated" label, plus a static-demo
  disclosure on every page.

## 8. Assumptions log

| # | Assumption | Evidence | Confidence |
|---|---|---|---|
| A1 | Primary operators are security, facilities or gatehouse desk staff at campus-type organisations, not software on-call engineers | `deploy/simulation_seed.yaml` ("Security Operations Center", "Security Desk Operator", "Campus Ops"); Zammad group default `Notfallstelle`; SendXMS sender `Notfall`; Yealink handsets; rooms and people as master data | Medium–high |
| A2 | Responders open the ack page on a phone from SMS or Signal | SendXMS and Signal providers; capability-token links; FRONTEND.md "44px responder controls" | High |
| A3 | German-speaking deployments are first-class | Full `de` catalogue, German provider defaults | High |
| A4 | Operators use the console for long stretches on a desktop monitor, sometimes in dim rooms | Live polling, revision refresh, theme toggle and dark mode already exist | Medium |
| A5 | Status (open vs closed) matters more than severity for triage; severity refines order | Worklist counts and nav badge are by status; sort defaults to age; bulk actions are status transitions | Medium |
| A6 | Operators value a quiet screen over brand colour | Control-room HMI practice (ISA-101, EEMUA 191), alarm-fatigue literature; no brand-colour usage in docs | Medium |
| A7 | Self-hosting two variable WOFF2 files (~60–90 KB total) is acceptable | No CDN allowed by CSP; ships in the wheel; cached `immutable` by StaticFiles ETag | High |
| A8 | Evaluators judge the project via the Pages demo and README screenshots | README "Screenshot tour", Pages workflow | High |
| A9 | Operators may run the console on Windows (where Segoe UI rendered) as well as macOS/Linux | Previous font stack led with Segoe UI | Low–medium |

---

## Design direction

### Direction A: "Alarmbuch" (the duty logbook)

- **Concept.** The control-room logbook: every alarm is a ruled ledger entry
  with time, place, measure and signature. It suits the *accountable* trait
  and the audit log.
- **Type.** A text serif (Source Serif 4) for entries, with IBM Plex Mono for
  times. Scale 1.2.
- **Colour.** Warm paper, blue-black ink, a red rubric for open alarms, pencil
  grey for closed ones.
- **Layout.** A single column of ruled rows with a strong left time column.
  Wide margins and low density.
- **Motion.** None beyond focus.
- **Signature.** Hand-ruled hairlines and a "countersigned" stamp for
  acknowledgement.
- **Versus the category.** Nobody in alerting looks like paper.
- **Refuses.** Cards, pills and icons.
- **Risk.** Nostalgic and slower to scan. Serif numerals at small sizes hurt
  triage, and dark mode makes no sense for paper. It optimises for the
  auditor, not the person watching for the next alarm.

### Direction B: "Stellwerk" (signal-box track diagram)

- **Concept.** Escalation as railway lanes. Each alarm is a train moving along
  its lane through escalation steps, and status reads as signal aspects. It
  makes the name and logo literal.
- **Type.** D-DIN Condensed for labels and Overpass Mono for numbers.
- **Colour.** Track grey, with signal red, yellow and green on near-black.
- **Layout.** Horizontal lanes per alarm with step markers. A wide desktop
  canvas.
- **Motion.** Trains advance on escalation.
- **Signature.** A lane diagram per alarm.
- **Versus the category.** Highly distinctive.
- **Refuses.** Tables.
- **Risk.** The worklist view model has no per-step escalation data, so this
  needs backend changes or invented data. It is decorative relative to the
  task, mobile is very hard, and it is dark-only. Too much concept, not
  enough instrument.

### Direction C: "Dark cockpit" (chosen)

- **Concept.** This comes from the aviation "dark cockpit" and ISA-101
  high-performance HMI philosophy: when everything is normal, nothing is lit.
  The console is a quiet, neutral instrument, and colour is a resource spent
  only on alarms that still need a human:
  - **Red (warning):** triggered, nobody has it yet.
  - **Amber (caution):** acknowledged, someone has it, still open.
  - **No colour:** resolved or cancelled. It is over.

  On a quiet day the whole screen is grey. When an alarm arrives, it is the
  only red thing on the screen. This is the *calm* and *unambiguous* traits
  made into a rule, and it answers weakness 2 directly.
- **Typography.** **Atkinson Hyperlegible Next** (Braille Institute, SIL OFL)
  for all text, plus **Atkinson Hyperlegible Mono** for times, ages, IDs and
  counts. The family was designed for character differentiation (0/O, 1/l/I,
  rn/m) under low vision, which is exactly the job when reading room numbers
  and alarm IDs under stress. Its slightly unusual letterforms give the
  product an identity without decoration. One superfamily keeps it coherent.
  The scale is a 1.2 minor-third on a 15px desk base (12 / 13 / 15 / 18 / 22 /
  27 / 32), with large mono numerals for counts and ages. Panel labels
  (`th`, `dt`, section labels) are set in small uppercase mono with
  tracking, like engraved labels on an annunciator panel.
- **Colour.** Neutral graphite greys with a hint of cool green (instrument
  paint, not navy):

  | Role | Light | Dark |
  |---|---|---|
  | Canvas | `#e9ebe8` | `#141615` |
  | Surface | `#f7f8f6` | `#1c1f1d` |
  | Ink | `#161917` | `#e7e9e6` |

  - **Ink** is the interactive colour: solid primary buttons, links and focus
    rings. There is no brand hue in the chrome.
  - **Warning red** (`#c4210f` / `#ff7a66`) is reserved for triggered alarms,
    errors and destructive confirmation.
  - **Caution amber** (`#a65f00` fill text; `#f2b33d` lamp) is reserved for
    acknowledged alarms and warnings.
  - The logo keeps its teal and amber, as the only "brand" colour, and appears
    small.
  - Resolved is not green. Green means nothing here, because "normal" is
    unlit.
- **Layout.**
  - A light rail, the same material as the canvas and separated by a single
    rule, replaces the navy slab.
  - The worklist opens with an annunciator strip: one wide "waiting" window
    (the count of triggered alarms, a large mono numeral) that lights red when
    above 0 and goes dark at 0. Acknowledged lights amber, and resolved and
    cancelled stay dark.
  - Below the strip is a dense table on a 4px base grid. At phone width,
    rows become two-line entries (lamp + age + who/where + meta), not a
    side-scrolling table.
  - The detail page uses a fixed action column. The responder page is a
    single column, state-first.
- **Motion.** Almost none:
  - The unacknowledged lamp has a slow 2s "breathing" pulse on a 6px dot,
    the ISA convention that unacknowledged alarms flash. Under the WCAG 2.3.1
    threshold, it is the only perpetual motion in the product, and it stops
    under `prefers-reduced-motion`.
  - The drawer slides in on `transform` (180ms, decelerate).
  - Hover states change background only, in 120ms.
- **Signature details.**
  1. **Annunciator lamps.** Status is a rectangular lamp window: filled red
     with white text when triggered, an amber outline when acknowledged, and
     plain grey text when closed. The status word is always present, so colour
     never carries meaning alone.
  2. **The waiting count.** The worklist's first and largest element is
     "3 waiting", the number of alarms nobody has yet. At 0 it reads "None
     waiting" in grey: the dark cockpit.
  3. **Lanes.** The logo's stepped lanes reappear as the escalation timeline
     rule and on the sign-in page, drawn in ink hairlines.
- **Versus the category.** No brand colour in chrome, no green, no dark
  sidebar, no KPI cards and no rounded-pill soup. Radii are 3–4px, like
  equipment, not apps. Severity becomes typographic (P0 solid ink, P1 ink
  outline, P2 plain), so it no longer competes with status hue.
- **Refuses.** Gradients, shadows beyond one functional overlay shadow,
  icons in tinted circles, illustration, emoji glyphs as icons (the demo's
  ⌁ ◫ ◌ glyphs are replaced), and success-green.

### Choice and trade-offs

**Direction C.** It is the only direction that improves the operator's core
task (seeing what is still open in three seconds) rather than decorating it.
It needs no new data, works on mobile and in both themes, and degrades
gracefully in forced-colours mode because meaning is carried by words and
shape.

**What it trades away:**

- the warmth and narrative of A, and the literal brand storytelling of B;
- the familiar "green = done" reassurance (mitigated by explicit "Resolved"
  text and a check glyph in the timeline);
- the teal brand presence in the UI.

If A6 (operators want a quiet screen) is wrong, the system still works: a
single token (`--accent`) could reintroduce a brand hue to the rail without
touching alarm semantics.
