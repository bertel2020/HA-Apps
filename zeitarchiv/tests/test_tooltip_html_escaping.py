"""Tooltips der Seiten-Skripte: Namen, Kategorien und Einheiten werden maskiert.

ECharts setzt den Rückgabewert eines Tooltip-Formatters als HTML in den body der Seite ein. Anzeigename und Einheit
kommen von Home Assistant (`friendly_name`, `unit_of_measurement`), der Server prüft dort nur die Länge. Ein Gerät mit
dem Namen `<img src=x onerror=…>` darf beim Überfahren eines Diagramms nie als Markup ankommen.

chart-tile-render.js ist von test_chart_tooltip_escaping.py abgedeckt (die Formatter lassen sich dort in node
ausführen). Die Formatter in diesen drei Skripten hängen an großen Alpine-Komponenten; hier prüft ein Quelltext-Test,
dass keine HTML-Zeile einen solchen Wert roh einsetzt.
"""

from __future__ import annotations

import re

import pytest

from _paths import APP_JS, PAGE_JS

FILES = [APP_JS / "energiedashboard.js", PAGE_JS / "chart_editor.js", PAGE_JS / "entity_detail.js"]

# Werte, die Anzeigename, Kategorie oder Einheit tragen können (oder ein Ergebnis, das sie enthält).
UNSAFE = re.compile(
    r"\$\{(?:unit|UNIT|s\.unit|absUnit)\}"
    r"|\$\{(?:p\.name|p\.seriesName|categories\[[^\]]*\]|labelFor\(|formatPointValue\(|format\()[^}]*\}"
)


def raw_interpolations(source: str) -> list[str]:
    """Zeilen, die HTML zusammensetzen und einen unsicheren Wert unmaskiert einsetzen."""
    found = []
    for number, line in enumerate(source.splitlines(), start=1):
        stripped = line.strip()
        if stripped.startswith("//") or "<" not in line or "${" not in line:
            continue
        if UNSAFE.search(line):
            found.append(f"{number}: {stripped}")
    return found


def test_the_scanner_flags_raw_values() -> None:
    assert raw_interpolations("return `<span>${p.marker}${p.seriesName}</span>`;")
    assert raw_interpolations("return `<strong>${format(value)}</strong>`;")
    assert raw_interpolations("return `${categories[catIndex]}<br>${span}`;")
    assert not raw_interpolations("return `<span>${p.marker}${escapeHtml(p.seriesName)}</span>`;")
    assert not raw_interpolations("return `<strong>${escapeHtml(format(value))}</strong>`;")


@pytest.mark.parametrize("path", FILES, ids=lambda path: path.name)
def test_html_tooltip_lines_escape_names_and_units(path) -> None:
    assert raw_interpolations(path.read_text(encoding="utf-8")) == []
