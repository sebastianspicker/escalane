"""Small, dependency-free translation catalogue for the server-rendered UI."""

from __future__ import annotations

from collections.abc import Mapping

DEFAULT_LOCALE = "en"
SUPPORTED_LOCALES = ("en", "de")

CATALOGUE: dict[str, dict[str, str]] = {
    "en": {
        "product_name": "Escalane",
        "brand_tagline": "Alarm intake, acknowledgement, and escalation.",
        "admin": "Administration",
        "sign_in": "Sign in",
        "operator_console": "Operator console",
        "secure_access": "Secure operator access",
        "sign_in_help": (
            "Use the assigned admin key. Your operator name is recorded with each action."
        ),
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
        "owner": "Owner",
        "unassigned": "Unassigned",
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
        "acknowledge_help": (
            "Acknowledge to tell the desk you have this alarm. "
            "Escalation to the next contact stops."
        ),
        "back_to_worklist": "Back to worklist",
        "try_again": "Try again",
        "error": "Something went wrong",
        "error_help": "The requested action could not be completed.",
        "close": "Close",
        "refresh_available": "New alarm activity. Refresh to see it.",
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
        "first_page": "First page",
        "configuration_pages": "Configuration pages",
        "older_activity": "Older activity",
        "latest_activity": "Latest activity",
        "loading_activity": "Loading older activity…",
        "activity_load_error": "Activity could not be loaded. Try Older activity again.",
        "no_older_activity": "No older activity.",
        "menu": "Menu",
        "system": "System",
        "simulation": "Simulation",
        "time": "Time",
        "channel": "Channel",
        "result": "Result",
        "clear_notifications": "Clear notifications",
        "no_records": "No records to display.",
        "alarms": "Alarms",
        "configuration": "Configuration",
        "audit_log": "Audit log",
        "primary_navigation": "Primary navigation",
        "configuration_sections": "Configuration sections",
        "signed_in_as": "Signed in as",
        "change_language": "Change",
        "sites": "Sites",
        "rooms": "Rooms",
        "people": "People",
        "devices": "Devices",
        "escalation": "Escalation",
        "import": "Import",
        "operations": "Operations",
        "manage": "Manage",
        "worklist_help": "Who raised each alarm, where, how long ago, and who has it.",
        "alarm_counts": "Alarm counts by status",
        "search_alarms": "Search alarms",
        "search_hint": "ID, person, room, source, or event",
        "sort_by": "Sort by",
        "order": "Order",
        "ascending": "Ascending",
        "descending": "Descending",
        "newest_first": "Newest first",
        "oldest_first": "Oldest first",
        "updated": "Updated",
        "live": "Live",
        "all": "All",
        "severity_filter": "Filter by severity",
        "clear_selection": "Clear selection",
        "theme": "Theme",
        "switch_to_dark_theme": "Switch to dark theme",
        "switch_to_light_theme": "Switch to light theme",
        "just_now": "just now",
        "seconds_ago": "{seconds} seconds ago",
        "apply_filters": "Apply filters",
        "export_csv": "Export CSV",
        "export_json": "Export JSON",
        "select": "Select",
        "select_alarm": "Select alarm {id}",
        "age": "Age",
        "open": "Open",
        "open_alarm": "Open alarm {id}",
        "no_alarms_help": "Change the search or status filter to see other alarms.",
        "selected_alarms": "selected",
        "action": "Action",
        "required_for_cancel": "required for cancellation",
        "reason_required_for_cancel": "Reason required for cancellation",
        "apply_to_selected": "Apply to selected",
        "pagination": "Worklist pages",
        "back": "Back",
        "alarm_context": "Alarm context",
        "available_actions": "Available actions",
        "resolve_help": "Close this alarm as resolved. A note can record the outcome.",
        "cancel_help": (
            "Cancel only when the alarm should no longer be handled. A reason is required."
        ),
        "keep_alarm_open": "Keep alarm open",
        "responder_action": "Responder action",
        "acknowledgement_complete": (
            "Someone already has this alarm, or it has been closed. "
            "There is nothing more to do here."
        ),
        "sign_in_context": (
            "The operator console for alarm intake, acknowledgement, and escalation. "
            "Every action is recorded with your name."
        ),
        "configuration_help": (
            "Maintain versioned operational master data. Changes are recorded in the audit log."
        ),
        "existing_resources": "Existing {resource}",
        "active": "Active",
        "inactive": "Inactive",
        "save_changes": "Save changes",
        "deactivate": "Deactivate",
        "confirm_deactivate": (
            "Deactivate this record? It will no longer be available for new alarms."
        ),
        "confirm_delete": "Delete this inactive record permanently? This action cannot be undone.",
        "add_first_resource": "Use the form to add the first record.",
        "name": "Name",
        "site_id": "Site ID",
        "label": "Label",
        "floor": "Floor",
        "notes": "Notes",
        "display_name": "Display name",
        "role": "Role",
        "phone_mobile": "Mobile phone",
        "phone_ext": "Extension",
        "vendor": "Vendor",
        "model_family": "Model family",
        "mac": "MAC address",
        "account_ext": "Account extension",
        "device_token": "Device token",
        "person_id": "Person ID",
        "room_id": "Room ID",
        "escalation_policy": "Escalation policy",
        "escalation_help": "Review and update the ordered notification targets and delays.",
        "write_only_targets": (
            "Target addresses are write-only. Leave an existing address blank to retain it."
        ),
        "policy_json": "Policy JSON",
        "save_policy": "Save escalation policy",
        "configuration_import": "Configuration import",
        "import_help": "Preview the YAML content and verify its hash before applying it.",
        "preview_ready": "Preview ready",
        "validated_sections": "Validated sections",
        "content_hash": "Content hash",
        "preview_import": "Preview import",
        "apply_import": "Apply reviewed import",
        "simulation_help": (
            "Inspect connector results generated by the isolated simulation environment."
        ),
        "simulation_empty_help": "Trigger a simulated alarm to create connector activity.",
        "confirm_clear_notifications": "Clear all simulated notification records?",
        "system_help": "Current readiness and dependency state reported by the service.",
        "activity_help": "Recorded operator changes to alarms and configuration.",
        "activity_empty_help": "Recorded operator changes will appear here.",
        "action_not_completed": "Action not completed",
        "reference": "Reference",
        "return_to_safe_page": "Return to previous view",
        "saved": "Changes saved.",
        "public_alpha_notice": "Public alpha. Not validated for emergency response.",
        "sign_in_headline": "Every alarm stays open until someone takes it.",
        "summary_triggered": "waiting for someone",
        "summary_acknowledged": "in hand, still open",
        "summary_resolved": "closed with an outcome",
        "summary_cancelled": "withdrawn",
        "acknowledge_operator_help": "Takes ownership and stops further escalation.",
        "alarm_closed_help": "This alarm is closed. You can still add a note.",
        "open_full_detail": "Open full alarm detail",
        "alarm_from": "Alarm from",
        "add_name_or_note": "Add your name or a note",
        "alarm_acknowledged": "Alarm acknowledged. Escalation has stopped.",
        "alarm_acknowledged_delivery_pending": (
            "Alarm acknowledged. Notifications are queued for the worker."
        ),
        "flash_acknowledged": "Alarm acknowledged. Escalation has stopped.",
        "flash_acknowledged_pending": (
            "Alarm acknowledged. Notifications are queued for the worker."
        ),
        "flash_resolved": "Alarm resolved.",
        "flash_resolved_pending": "Alarm resolved. Notifications are queued for the worker.",
        "flash_cancelled": "Alarm cancelled. The reason is recorded in the activity.",
        "flash_cancelled_pending": "Alarm cancelled. Notifications are queued for the worker.",
        "flash_bulk": "{changed} changed · {unchanged} already in that state · {missing} not found",
        "note_added": "Note added to the activity.",
        "alarm_deleted": "Alarm removed from the worklist.",
        "add_sites": "Add site",
        "add_rooms": "Add room",
        "add_people": "Add person",
        "add_devices": "Add device",
    },
    "de": {
        "product_name": "Escalane",
        "brand_tagline": "Alarmannahme, Übernahme und Eskalation.",
        "admin": "Verwaltung",
        "sign_in": "Anmelden",
        "operator_console": "Bedienoberfläche",
        "secure_access": "Geschützter Zugang",
        "sign_in_help": (
            "Verwenden Sie den zugewiesenen Admin-Schlüssel. "
            "Ihr Name wird mit jeder Aktion protokolliert."
        ),
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
        "owner": "Übernahme",
        "unassigned": "Nicht zugewiesen",
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
        "acknowledge_help": (
            "Übernehmen Sie den Alarm, damit die Leitstelle weiß, dass Sie sich kümmern. "
            "Die Eskalation an den nächsten Kontakt endet."
        ),
        "back_to_worklist": "Zurück zur Übersicht",
        "try_again": "Erneut versuchen",
        "error": "Etwas ist schiefgelaufen",
        "error_help": "Die gewünschte Aktion konnte nicht abgeschlossen werden.",
        "close": "Schließen",
        "refresh_available": "Neue Alarmaktivität. Aktualisieren Sie die Ansicht.",
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
        "first_page": "Erste Seite",
        "configuration_pages": "Konfigurationsseiten",
        "older_activity": "Ältere Aktivitäten",
        "latest_activity": "Neueste Aktivitäten",
        "loading_activity": "Ältere Aktivitäten werden geladen…",
        "activity_load_error": ("Laden fehlgeschlagen. Erneut Ältere Aktivitäten wählen."),
        "no_older_activity": "Keine älteren Aktivitäten.",
        "menu": "Menü",
        "system": "System",
        "simulation": "Simulation",
        "time": "Zeit",
        "channel": "Kanal",
        "result": "Ergebnis",
        "clear_notifications": "Benachrichtigungen löschen",
        "no_records": "Keine Einträge vorhanden.",
        "alarms": "Alarme",
        "configuration": "Konfiguration",
        "audit_log": "Änderungsprotokoll",
        "primary_navigation": "Hauptnavigation",
        "configuration_sections": "Konfigurationsbereiche",
        "signed_in_as": "Angemeldet als",
        "change_language": "Ändern",
        "sites": "Standorte",
        "rooms": "Räume",
        "people": "Personen",
        "devices": "Geräte",
        "escalation": "Eskalation",
        "import": "Import",
        "operations": "Betrieb",
        "manage": "Verwalten",
        "worklist_help": (
            "Wer den Alarm ausgelöst hat, wo, vor wie langer Zeit und wer ihn übernommen hat."
        ),
        "alarm_counts": "Alarmanzahl nach Status",
        "search_alarms": "Alarme suchen",
        "search_hint": "ID, Person, Raum, Quelle oder Ereignis",
        "sort_by": "Sortieren nach",
        "order": "Reihenfolge",
        "ascending": "Aufsteigend",
        "descending": "Absteigend",
        "newest_first": "Neueste zuerst",
        "oldest_first": "Älteste zuerst",
        "updated": "Aktualisiert",
        "live": "Live",
        "all": "Alle",
        "severity_filter": "Nach Dringlichkeit filtern",
        "clear_selection": "Auswahl aufheben",
        "theme": "Darstellung",
        "switch_to_dark_theme": "Zum dunklen Design wechseln",
        "switch_to_light_theme": "Zum hellen Design wechseln",
        "just_now": "gerade eben",
        "seconds_ago": "vor {seconds} Sekunden",
        "apply_filters": "Filter anwenden",
        "export_csv": "CSV exportieren",
        "export_json": "JSON exportieren",
        "select": "Auswählen",
        "select_alarm": "Alarm {id} auswählen",
        "age": "Alter",
        "open": "Öffnen",
        "open_alarm": "Alarm {id} öffnen",
        "no_alarms_help": "Ändern Sie Suche oder Statusfilter, um andere Alarme anzuzeigen.",
        "selected_alarms": "ausgewählt",
        "action": "Aktion",
        "required_for_cancel": "bei Stornierung erforderlich",
        "reason_required_for_cancel": "Begründung bei Stornierung erforderlich",
        "apply_to_selected": "Auf Auswahl anwenden",
        "pagination": "Seiten der Alarmübersicht",
        "back": "Zurück",
        "alarm_context": "Alarmkontext",
        "available_actions": "Verfügbare Aktionen",
        "resolve_help": "Schließt den Alarm als erledigt. Eine Notiz kann das Ergebnis festhalten.",
        "cancel_help": (
            "Nur stornieren, wenn der Alarm nicht mehr bearbeitet werden soll. "
            "Eine Begründung ist erforderlich."
        ),
        "keep_alarm_open": "Alarm geöffnet lassen",
        "responder_action": "Aktion für Einsatzkräfte",
        "acknowledgement_complete": (
            "Dieser Alarm ist bereits übernommen oder abgeschlossen. Hier ist nichts mehr zu tun."
        ),
        "sign_in_context": (
            "Die Bedienoberfläche für Alarmannahme, Übernahme und Eskalation. "
            "Jede Aktion wird mit Ihrem Namen protokolliert."
        ),
        "configuration_help": (
            "Versionierte Betriebsdaten verwalten. Änderungen werden protokolliert."
        ),
        "existing_resources": "Vorhandene {resource}",
        "active": "Aktiv",
        "inactive": "Inaktiv",
        "save_changes": "Änderungen speichern",
        "deactivate": "Deaktivieren",
        "confirm_deactivate": (
            "Diesen Eintrag deaktivieren? Er steht dann für neue Alarme nicht mehr zur Verfügung."
        ),
        "confirm_delete": (
            "Diesen inaktiven Eintrag dauerhaft löschen? "
            "Diese Aktion kann nicht rückgängig gemacht werden."
        ),
        "add_first_resource": "Fügen Sie mit dem Formular den ersten Eintrag hinzu.",
        "name": "Name",
        "site_id": "Standort-ID",
        "label": "Bezeichnung",
        "floor": "Etage",
        "notes": "Notizen",
        "display_name": "Anzeigename",
        "role": "Rolle",
        "phone_mobile": "Mobiltelefon",
        "phone_ext": "Durchwahl",
        "vendor": "Hersteller",
        "model_family": "Modellfamilie",
        "mac": "MAC-Adresse",
        "account_ext": "Konto-Durchwahl",
        "device_token": "Geräte-Token",
        "person_id": "Personen-ID",
        "room_id": "Raum-ID",
        "escalation_policy": "Eskalationsrichtlinie",
        "escalation_help": "Geordnete Benachrichtigungsziele und Verzögerungen prüfen und ändern.",
        "write_only_targets": (
            "Zieladressen sind nur schreibbar. Lassen Sie eine vorhandene Adresse leer, "
            "um sie beizubehalten."
        ),
        "policy_json": "Richtlinie als JSON",
        "save_policy": "Eskalationsrichtlinie speichern",
        "configuration_import": "Konfiguration importieren",
        "import_help": "Prüfen Sie den YAML-Inhalt und seinen Hash, bevor Sie ihn anwenden.",
        "preview_ready": "Vorschau bereit",
        "validated_sections": "Geprüfte Bereiche",
        "content_hash": "Inhalts-Hash",
        "preview_import": "Import prüfen",
        "apply_import": "Geprüften Import anwenden",
        "simulation_help": (
            "Ergebnisse der Konnektoren in der isolierten Simulationsumgebung prüfen."
        ),
        "simulation_empty_help": (
            "Lösen Sie einen simulierten Alarm aus, um Konnektoraktivität zu erzeugen."
        ),
        "confirm_clear_notifications": "Alle simulierten Benachrichtigungseinträge löschen?",
        "system_help": "Aktuelle Bereitschaft und Abhängigkeiten, die der Dienst meldet.",
        "activity_help": "Protokollierte Änderungen an Alarmen und Konfiguration.",
        "activity_empty_help": "Protokollierte Änderungen werden hier angezeigt.",
        "action_not_completed": "Aktion nicht abgeschlossen",
        "reference": "Referenz",
        "return_to_safe_page": "Zur vorherigen Ansicht",
        "saved": "Änderungen gespeichert.",
        "public_alpha_notice": "Öffentliche Alpha. Nicht für den Notfalleinsatz validiert.",
        "sign_in_headline": "Jeder Alarm bleibt offen, bis ihn jemand übernimmt.",
        "summary_triggered": "wartet auf Übernahme",
        "summary_acknowledged": "in Bearbeitung, noch offen",
        "summary_resolved": "mit Ergebnis abgeschlossen",
        "summary_cancelled": "zurückgezogen",
        "acknowledge_operator_help": "Übernimmt den Alarm und beendet die weitere Eskalation.",
        "alarm_closed_help": "Dieser Alarm ist abgeschlossen. Notizen sind weiterhin möglich.",
        "open_full_detail": "Vollständige Alarmdetails öffnen",
        "alarm_from": "Alarm aus",
        "add_name_or_note": "Namen oder Notiz hinzufügen",
        "alarm_acknowledged": "Alarm übernommen. Die Eskalation ist beendet.",
        "alarm_acknowledged_delivery_pending": (
            "Alarm übernommen. Benachrichtigungen warten auf den Worker."
        ),
        "flash_acknowledged": "Alarm übernommen. Die Eskalation ist beendet.",
        "flash_acknowledged_pending": "Alarm übernommen. Benachrichtigungen warten auf den Worker.",
        "flash_resolved": "Alarm abgeschlossen.",
        "flash_resolved_pending": "Alarm abgeschlossen. Benachrichtigungen warten auf den Worker.",
        "flash_cancelled": "Alarm storniert. Der Grund steht im Verlauf.",
        "flash_cancelled_pending": "Alarm storniert. Benachrichtigungen warten auf den Worker.",
        "flash_bulk": (
            "{changed} geändert · {unchanged} bereits in diesem Status · {missing} nicht gefunden"
        ),
        "note_added": "Notiz zum Verlauf hinzugefügt.",
        "alarm_deleted": "Alarm aus der Übersicht entfernt.",
        "add_sites": "Standort hinzufügen",
        "add_rooms": "Raum hinzufügen",
        "add_people": "Person hinzufügen",
        "add_devices": "Gerät hinzufügen",
    },
}


def normalise_locale(value: str | None) -> str:
    """Return a supported locale, accepting a standard Accept-Language prefix."""
    candidate = (value or DEFAULT_LOCALE).replace("_", "-").split("-", 1)[0].lower()
    return candidate if candidate in SUPPORTED_LOCALES else DEFAULT_LOCALE


def canonical_locale(value: str | None) -> str:
    """Return the matching supported locale constant, never the caller-supplied string."""
    for locale in SUPPORTED_LOCALES:
        if locale == value:
            return locale
    return DEFAULT_LOCALE


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
