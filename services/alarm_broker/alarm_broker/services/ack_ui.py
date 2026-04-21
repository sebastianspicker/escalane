from __future__ import annotations

from html import escape
from string import Template

from alarm_broker.api.template_loader import load_template
from alarm_broker.db.models import Alarm, AlarmStatus
from alarm_broker.types import EnrichedAlarmContext

_TEMPLATE: Template = load_template("ack.html")


def render_ack_page(alarm: Alarm, enriched: EnrichedAlarmContext, *, csrf_token: str = "") -> str:
    person = escape(str(enriched.get("person_name") or (alarm.person_id or "-")), quote=True)
    room = escape(str(enriched.get("room_label") or (alarm.room_id or "-")), quote=True)
    created = escape(alarm.created_at.isoformat(), quote=True)
    status_label = escape(alarm.status.value, quote=True)

    is_triggered = alarm.status == AlarmStatus.TRIGGERED
    status_descriptions = {
        AlarmStatus.TRIGGERED: "Alarm aktiv -- wartet auf Übernahme.",
        AlarmStatus.ACKNOWLEDGED: "Alarm übernommen -- wird bearbeitet.",
        AlarmStatus.RESOLVED: "Alarm abgeschlossen.",
        AlarmStatus.CANCELLED: "Alarm storniert.",
    }
    info_messages = {
        AlarmStatus.TRIGGERED: "Bitte quittiere den Alarm, wenn du die Übernahme bestätigst.",
        AlarmStatus.ACKNOWLEDGED: (
            "Dieser Alarm wurde erfolgreich übernommen. Das Einsatzteam wurde benachrichtigt."
        ),
        AlarmStatus.RESOLVED: "Dieser Alarm ist bereits gelöst. Keine weitere Aktion erforderlich.",
        AlarmStatus.CANCELLED: "Dieser Alarm wurde storniert. Keine weitere Aktion erforderlich.",
    }
    info_classes = {
        AlarmStatus.TRIGGERED: "warning",
        AlarmStatus.ACKNOWLEDGED: "success",
        AlarmStatus.RESOLVED: "success",
        AlarmStatus.CANCELLED: "",
    }

    csrf_field = (
        f'<input type="hidden" name="csrf_token" value="{escape(csrf_token, quote=True)}">'
        if csrf_token
        else ""
    )
    form_block = (
        f"""
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
        if is_triggered
        else ""
    )

    return _TEMPLATE.substitute(
        title="Alarm übernehmen" if is_triggered else "Alarm",
        headline="Alarm übernehmen" if is_triggered else "Alarm",
        status_label=status_label,
        status_color="#b45309" if is_triggered else "#047857",
        status_badge_class=escape(alarm.status.value, quote=True),
        status_description=escape(status_descriptions.get(alarm.status, "Alarmstatus"), quote=True),
        person=person,
        room=room,
        created=created,
        info_class=escape(info_classes.get(alarm.status, ""), quote=True),
        info_message=escape(
            info_messages.get(alarm.status, "Dieser Alarm wurde bereits bearbeitet."),
            quote=True,
        ),
        form_block=form_block,
    )
