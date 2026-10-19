"""Knopftexte der Kopfzeilen- und Markierungs-Auswahl folgen der Sprache.

"Ja"/"Nein" beim CSV-Import und "Markierung:" im Markierungsfilter standen
fest auf Deutsch im Template bzw. im onclick und blieben in der englischen
Oberfläche deutsch.
"""

from __future__ import annotations

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.formatting import format_int
from app.i18n import translate_html

from _paths import TEMPLATES


def _environment(lang: str) -> Environment:
    environment = Environment(
        loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape(["html"])
    )
    environment.globals["_"] = lambda text, **values: translate_html(text, lang, **values)
    environment.filters["format_int"] = format_int
    return environment


def test_marking_filter_is_translated() -> None:
    counts = {key: 1 for key in (
        "all", "outliers", "gaps", "duplicates", "repetitions", "counter_decreases",
    )}
    html = _environment("en").get_template("_rows_filter_menu.html").render(
        counts=counts, filter="all", counter_decrease_enabled=True,
    )
    assert "Markierung" not in html
    assert "Mark: All" in html
    assert 'data-label="Mark: ' in html


def test_header_picker_has_no_fixed_german_labels() -> None:
    # Rohtext statt template_text(): das packt _("…") für Tests aus und
    # ließe ein übersetztes "Ja" wie ein festes aussehen.
    source = (TEMPLATES / "_csv_import_section.html").read_text(encoding="utf-8")
    assert "'Ja'" not in source and "'Nein'" not in source
    assert '{{ "Ja"' not in source
    assert "selectDDOption('header', 'on', this.dataset.label)" in source
