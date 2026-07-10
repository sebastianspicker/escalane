from __future__ import annotations

from html import escape
from string import Template

from alarm_broker.api.template_loader import load_template
from alarm_broker.db.models import Alarm, AlarmStatus
from alarm_broker.types import EnrichedAlarmContext

_TEMPLATE: Template = load_template("ack.html")
_STATUS_DESCRIPTIONS = {
    AlarmStatus.TRIGGERED: "Alarm aktiv -- wartet auf Übernahme.",
    AlarmStatus.ACKNOWLEDGED: "Alarm übernommen -- wird bearbeitet.",
    AlarmStatus.RESOLVED: "Alarm abgeschlossen.",
    AlarmStatus.CANCELLED: "Alarm storniert.",
}
_INFO_MESSAGES = {
    AlarmStatus.TRIGGERED: "Bitte quittiere den Alarm, wenn du die Übernahme bestätigst.",
    AlarmStatus.ACKNOWLEDGED: (
        "Dieser Alarm wurde erfolgreich übernommen. Das Einsatzteam wurde benachrichtigt."
    ),
    AlarmStatus.RESOLVED: "Dieser Alarm ist bereits gelöst. Keine weitere Aktion erforderlich.",
    AlarmStatus.CANCELLED: "Dieser Alarm wurde storniert. Keine weitere Aktion erforderlich.",
}
_INFO_CLASSES = {
    AlarmStatus.TRIGGERED: "warning",
    AlarmStatus.ACKNOWLEDGED: "success",
    AlarmStatus.RESOLVED: "success",
    AlarmStatus.CANCELLED: "",
}


def _escaped_label(value: object | None, fallback: str = "-") -> str:
    return escape(str(value or fallback), quote=True)


def _render_ack_form(csrf_token: str) -> str:
    csrf_field = (
        f'<input type="hidden" name="csrf_token" value="{escape(csrf_token, quote=True)}">'
        if csrf_token
        else ""
    )
    return f"""
    <form method="post" onsubmit="return lockSubmit(this)" aria-label="Alarm quittieren">
      {csrf_field}
      <label for="acked_by">Dein Name (optional)
        <input id="acked_by" name="acked_by" autocomplete="name"
               placeholder="z.B. Max Mustermann"
               aria-describedby="name-hint">
      </label>
      <label for="note">Notiz (optional)
        <textarea id="note" name="note" rows="3"
                  placeholder="z.B. Bin vor Ort, Situation unter Kontrolle"
                  aria-describedby="note-hint"></textarea>
      </label>
      <button type="submit" aria-label="Alarm jetzt übernehmen">Alarm übernehmen</button>
      <p class="hint" id="name-hint">Die Seite aktualisiert nach dem Absenden automatisch.</p>
    </form>
"""


def render_ack_page(alarm: Alarm, enriched: EnrichedAlarmContext, *, csrf_token: str = "") -> str:
    is_triggered = alarm.status == AlarmStatus.TRIGGERED
    title = "Alarm übernehmen" if is_triggered else "Alarm"

    return _TEMPLATE.substitute(
        title=title,
        headline=title,
        status_label=escape(alarm.status.value, quote=True),
        status_color="#b45309" if is_triggered else "#047857",
        status_badge_class=escape(alarm.status.value, quote=True),
        status_description=escape(
            _STATUS_DESCRIPTIONS.get(alarm.status, "Alarmstatus"), quote=True
        ),
        person=_escaped_label(enriched.get("person_name") or alarm.person_id),
        room=_escaped_label(enriched.get("room_label") or alarm.room_id),
        created=escape(alarm.created_at.isoformat(), quote=True),
        info_class=escape(_INFO_CLASSES.get(alarm.status, ""), quote=True),
        info_message=escape(
            _INFO_MESSAGES.get(alarm.status, "Dieser Alarm wurde bereits bearbeitet."),
            quote=True,
        ),
        form_block=_render_ack_form(csrf_token) if is_triggered else "",
    )
