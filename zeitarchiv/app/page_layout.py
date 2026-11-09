"""Experimentelle breite Darstellung (Roadmap 1.40): Einstellung ``page_layout``.

„standard“ lässt den Seitenrahmen bei 1120 px, „wide“ setzt ``data-page-layout="wide"`` an ``<html>``
(base.html); die Regeln dazu hängen im CSS an diesem Attribut. Die Einstellung gilt je Installation.
"""

from __future__ import annotations

from collections.abc import Mapping

from .i18n import N_, tr
from .storage.index import Index

SETTING = "page_layout"
LABELS = {"standard": N_("Aus"), "wide": N_("An")}


def current(index: Index) -> str:
    """Gespeicherter Wert, unbekannte Werte gelten als „standard“."""
    value = index.get_setting(SETTING, "standard")
    return value if value in LABELS else "standard"


def settings_context() -> dict:
    return {"page_layout_options": list(LABELS.items())}


def apply_form(index: Index, form: Mapping) -> None:
    """Speichert das Formularfeld ``page_layout`` (ValueError bei ungültigem Wert, fehlendes Feld = nichts)."""
    value = form.get(SETTING)
    if value is None:
        return
    if value not in LABELS:
        raise ValueError(tr("Ungültige Seitenbreite"))
    index.set_setting(SETTING, str(value))
