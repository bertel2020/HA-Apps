"""Automatisches Aktualisieren der Einzelseiten (Tabelle, später Chart und Entität): Einstellung ``page_refresh``.

„on“ (Standard) lädt die Werte einer geöffneten Seite jede Minute nach, „off“ setzt ``data-page-refresh="off"``
an ``<html>`` (base.html); das Skript der Seite liest dieses Attribut. Dashboards laden unabhängig davon nach.
Die Einstellung gilt je Installation.
"""

from __future__ import annotations

from collections.abc import Mapping

from .i18n import N_, tr
from .storage.index import Index

SETTING = "page_refresh"
LABELS = {"on": N_("An"), "off": N_("Aus")}


def current(index: Index) -> str:
    """Gespeicherter Wert, unbekannte Werte gelten als „on“."""
    value = index.get_setting(SETTING, "on")
    return value if value in LABELS else "on"


def settings_context() -> dict:
    return {"page_refresh_options": list(LABELS.items())}


def apply_form(index: Index, form: Mapping) -> None:
    """Speichert das Formularfeld ``page_refresh`` (ValueError bei ungültigem Wert, fehlendes Feld = nichts)."""
    value = form.get(SETTING)
    if value is None:
        return
    if value not in LABELS:
        raise ValueError(tr("Ungültige Einstellung zum automatischen Aktualisieren"))
    index.set_setting(SETTING, str(value))
