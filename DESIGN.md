# Alarm Broker UI foundation

## Visual language

The interface is a quiet operational surface: neutral system fonts, modest
contrast layers, compact spacing, and colour used as a state signal rather than
as decoration. CSS custom properties follow `prefers-color-scheme`; the markup
stays semantic so that forced-colors mode can replace the palette safely.

## Accessibility contract

- Use landmarks (`header`, `nav`, `main`, `footer`) and one visible `h1`.
- Every input has a visible label; messages use appropriate live regions.
- `:focus-visible` is always high contrast and never removed.
- Motion is disabled under `prefers-reduced-motion: reduce`.
- Native `<dialog>` is enhanced only by same-origin JavaScript. Closing restores
  focus to the opener; without JavaScript, linked details and forms still work.
- The worklist table remains a table. Its wrapper scrolls horizontally at 320 px
  rather than hiding columns or changing row order.

## Template contract

Jinja templates inherit `base.html` and use `macros.html` for repeated status,
language, and action controls. Conventional route context names are:

- `locale`, `locales`, `t`, `page_title`, `current_path`, `message`
- `alarms`, `counts`, `filters`, `alarm`, `events`, `error`
- `login_action`, `worklist_url`, `detail_url`, `ack_action`, `csrf_token`

Routes may supply URLs directly or use their normal URL helper. Templates do
not construct acknowledgement URLs or render acknowledgement tokens on admin
pages.
