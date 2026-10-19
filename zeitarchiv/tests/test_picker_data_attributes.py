"""Veränderliche Texte gehören in Auswahllisten nicht in den onclick-Handler.

Einheiten, CSV-Spaltenköpfe, Entitätsnamen und Markierungstexte stammen aus
Daten. Als JavaScript-Stringliteral im onclick dekodiert der Browser &#39;
vor dem Ausführen wieder zu ' — ein Apostroph legte den Filter lahm, ein
präparierter Wert konnte Skript ausführen. Sie stehen deshalb in
data-value/data-label, wo Jinjas HTML-Maskierung ausreicht.
"""

from __future__ import annotations

import html
import re

from jinja2 import Environment, FileSystemLoader, select_autoescape

from _paths import TEMPLATES, template_text


HOSTILE = "x');alert(1);//\"<b>"

# Datei → Picker-Präfixe, deren Zeilen veränderliche Texte tragen.
DATA_PICKERS = {
    "entities_list.html": ("unit-filter",),
    "_csv_import_section.html": ("ts-col", "value-col"),
    "_housekeeping_activity_form.html": (
        "activity-entity", "activity-action", "activity-status", "activity-days",
    ),
    "_rows_filter_menu.html": ("rows-filter",),
}


def test_data_pickers_do_not_interpolate_text_into_onclick() -> None:
    for name, prefixes in DATA_PICKERS.items():
        source = template_text(name)
        for prefix in prefixes:
            handlers = re.findall(
                rf"onclick=\"selectDDOption\('{prefix}',([^\"]*)\"", source
            )
            assert handlers, (name, prefix)
            assert any("this.dataset.label" in args for args in handlers), (name, prefix)
            for args in handlers:
                # Nur die Schleifenposition (eine Zahl) bzw. ein fester
                # Literalwert darf noch direkt eingesetzt werden.
                assert "{{" not in args.replace("{{ loop.index0 }}", ""), (name, prefix, args)


def test_hostile_activity_label_stays_inside_the_data_attribute() -> None:
    environment = Environment(
        loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape(["html"])
    )
    environment.globals["_"] = lambda text, **values: text
    options = [("", "Alle"), ("sensor.a", HOSTILE)]
    rendered = environment.get_template("_housekeeping_activity_form.html").render(
        activity_entity_options=options,
        activity_action_options=options,
        activity_status_options=options,
        activity_days_options=options,
        activity_entity_filter="",
        activity_action_filter="",
        activity_status_filter="",
        activity_days_filter="",
        activity_rows=[],
    )
    assert "alert(1)" not in "".join(re.findall(r'onclick="([^"]*)"', rendered))
    labels = re.findall(r'data-label="([^"]*)"', rendered)
    assert HOSTILE in [html.unescape(label) for label in labels]
