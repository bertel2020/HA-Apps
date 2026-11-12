"""Wertekachel: bei niedriger Kachel darf die Zahl nicht aus ihrer Box ragen (Forum 2026-11-12).

Der Präzise Modus macht eine einzeilige Kachel nur halb so hoch. Die Wert-Box hatte min-height:0 und schrumpfte unter
die Zeilenhöhe der Zahl; der Strich der Kennzahlen-Zeile lief dann durch den Hauptwert. Jetzt behält die Wert-Box
ihre Höhe, die Sparkline gibt nach."""

import re

from _paths import APP

SEITEN_CSS = ["entities.css", "dashboard_detail.css"]  # beide tragen eine eigene Kopie der Kachel-Regeln


def _regel(css: str, selektor: str) -> str:
    treffer = re.search(re.escape(selektor) + r"\s*\{([^}]*)\}", css)
    assert treffer, selektor
    return treffer.group(1)


def test_the_value_box_does_not_shrink_below_its_content() -> None:
    for name in SEITEN_CSS:
        css = (APP / "static/css/pages" / name).read_text(encoding="utf-8")
        assert "min-height" not in _regel(css, ".dtile-entity-value"), name


def test_the_sparkline_gives_way_first() -> None:
    for name in SEITEN_CSS:
        css = (APP / "static/css/pages" / name).read_text(encoding="utf-8")
        regel = _regel(css, ".dtile-entity-sparkline")
        assert "flex:0 1 auto" in regel and "min-height:0" in regel and "overflow:hidden" in regel, name
