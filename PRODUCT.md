# Product

## Register

product

## Users

Alarm operators work frequently at a campus, security, or operations desk under time pressure. They need dense scanning, keyboard-safe actions, explicit freshness, and an auditable alarm timeline. Responders use an occasional capability link on a phone and need a short, plain-language acknowledgement flow. System administrators and maintainers use configuration, simulation, health, and activity views less frequently and need exact state without exposed secrets.

## Product Purpose

Alarm Broker is an open-source release-candidate reference implementation for alarm intake, persistence, notification fan-out, escalation, acknowledgement, and lifecycle management. Its browser UI provides a calm bilingual operator work surface and a focused mobile responder acknowledgement flow. It is not validated for safety-critical or compliance-critical deployment.

## Brand Personality

Calm, precise, dependable. The interface should feel like a trustworthy operational tool: direct language, stable structure, and state before decoration.

## Anti-references

Do not imitate theatrical command centres, consumer dashboards, or generic AI-generated SaaS surfaces. Avoid “Mission Control,” “Alarm Intelligence,” “Engage,” glow, glass panels, gradients, ornamental pulses, hero metrics, tiny tracked uppercase scaffolding, decorative charts, and custom controls where native HTML is clearer.

## Design Principles

- State before decoration: show status, time, person, location, freshness, and the next safe action first.
- Core actions survive without JavaScript; enhancement must preserve operator context and focus.
- Interrupt in proportion to risk: acknowledge is immediate, while cancellation and deletion require a reason and confirmation.
- Never expose capability tokens, static admin credentials, target addresses, or device tokens in operator pages or audit data.
- German and English convey equivalent meaning without changing authority or domain data.

## Accessibility & Inclusion

WCAG 2.2 AA is the release target. Primary journeys must be keyboard-operable with visible focus, semantic tables and dialogs, text-plus-colour state, persistent actionable errors, reduced-motion and forced-colours support, 320 px reflow, and at least 44 px critical responder controls. Manual VoiceOver/Safari and NVDA/Firefox verification remains a pre-release responsibility.
