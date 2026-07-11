"""Small, dependency-free translation catalogue for the server-rendered UI."""

from __future__ import annotations

from collections.abc import Mapping

DEFAULT_LOCALE = "en"
SUPPORTED_LOCALES = ("en", "de")

CATALOGUE: dict[str, dict[str, str]] = {
    "en": {
        "product_name": "Alarm Broker",
        "admin": "Administration",
        "sign_in": "Sign in",
        "sign_out": "Sign out",
        "admin_key": "Admin key",
        "language": "Language",
        "worklist": "Alarm worklist",
        "alarm": "Alarm",
        "status": "Status",
        "created": "Created",
        "person": "Person",
        "room": "Room",
        "source": "Source",
        "severity": "Severity",
        "acknowledged_by": "Acknowledged by",
        "actions": "Actions",
        "details": "Details",
        "acknowledge": "Acknowledge",
        "resolve": "Resolve",
        "cancel": "Cancel",
        "save": "Save",
        "filter": "Filter",
        "all_statuses": "All statuses",
        "no_alarms": "No alarms match this view.",
        "alarm_details": "Alarm details",
        "activity": "Activity",
        "note": "Note",
        "optional": "optional",
        "responder_name": "Your name",
        "acknowledge_alarm": "Acknowledge alarm",
        "acknowledge_help": "Confirm that you are taking ownership of this alarm.",
        "back_to_worklist": "Back to worklist",
        "try_again": "Try again",
        "error": "Something went wrong",
        "error_help": "The requested action could not be completed.",
        "close": "Close",
        "refresh_available": "New alarm information is available.",
        "refresh": "Refresh",
        "triggered": "Triggered",
        "acknowledged": "Acknowledged",
        "resolved": "Resolved",
        "cancelled": "Cancelled",
        "required": "required",
        "skip_to_content": "Skip to content",
        "operator_name": "Operator name",
        "search": "Search",
        "export": "Export",
        "selected_action": "Action for selected alarms",
        "apply": "Apply",
        "reason": "Reason",
        "add_note": "Add note",
        "delete": "Delete",
        "cancel_alarm": "Cancel alarm",
        "resolve_alarm": "Resolve alarm",
        "next_page": "Next page",
        "menu": "Menu",
    },
    "de": {
        "product_name": "Alarm Broker",
        "admin": "Verwaltung",
        "sign_in": "Anmelden",
        "sign_out": "Abmelden",
        "admin_key": "Admin-Schlüssel",
        "language": "Sprache",
        "worklist": "Alarmübersicht",
        "alarm": "Alarm",
        "status": "Status",
        "created": "Erstellt",
        "person": "Person",
        "room": "Raum",
        "source": "Quelle",
        "severity": "Dringlichkeit",
        "acknowledged_by": "Übernommen von",
        "actions": "Aktionen",
        "details": "Details",
        "acknowledge": "Übernehmen",
        "resolve": "Abschließen",
        "cancel": "Abbrechen",
        "save": "Speichern",
        "filter": "Filtern",
        "all_statuses": "Alle Status",
        "no_alarms": "Keine Alarme in dieser Ansicht.",
        "alarm_details": "Alarmdetails",
        "activity": "Verlauf",
        "note": "Notiz",
        "optional": "optional",
        "responder_name": "Ihr Name",
        "acknowledge_alarm": "Alarm übernehmen",
        "acknowledge_help": "Bestätigen Sie, dass Sie diesen Alarm übernehmen.",
        "back_to_worklist": "Zurück zur Übersicht",
        "try_again": "Erneut versuchen",
        "error": "Etwas ist schiefgelaufen",
        "error_help": "Die gewünschte Aktion konnte nicht abgeschlossen werden.",
        "close": "Schließen",
        "refresh_available": "Neue Alarminformationen sind verfügbar.",
        "refresh": "Aktualisieren",
        "triggered": "Ausgelöst",
        "acknowledged": "Übernommen",
        "resolved": "Abgeschlossen",
        "cancelled": "Storniert",
        "required": "erforderlich",
        "skip_to_content": "Zum Inhalt springen",
        "operator_name": "Name der Bedienperson",
        "search": "Suchen",
        "export": "Exportieren",
        "selected_action": "Aktion für ausgewählte Alarme",
        "apply": "Ausführen",
        "reason": "Begründung",
        "add_note": "Notiz hinzufügen",
        "delete": "Löschen",
        "cancel_alarm": "Alarm stornieren",
        "resolve_alarm": "Alarm abschließen",
        "next_page": "Nächste Seite",
        "menu": "Menü",
    },
}


def normalise_locale(value: str | None) -> str:
    """Return a supported locale, accepting a standard Accept-Language prefix."""
    candidate = (value or DEFAULT_LOCALE).replace("_", "-").split("-", 1)[0].lower()
    return candidate if candidate in SUPPORTED_LOCALES else DEFAULT_LOCALE


def translate(key: str, locale: str | None = None, **values: object) -> str:
    """Look up *key*, falling back to English and then to the key itself."""
    selected = normalise_locale(locale)
    text = CATALOGUE[selected].get(key, CATALOGUE[DEFAULT_LOCALE].get(key, key))
    return text.format_map(_MissingValues(values))


class _MissingValues(dict[str, object]):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def translation_context(locale: str | None = None) -> Mapping[str, object]:
    """Provide a compact context suitable for Jinja template globals."""
    selected = normalise_locale(locale)
    return {
        "locale": selected,
        "locales": SUPPORTED_LOCALES,
        "t": lambda key, **values: translate(key, selected, **values),
    }
