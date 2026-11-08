"""Kleine Bedienungsfehler im Wetterdashboard: Monatsvergleich nach Ladefehler, Namen der Dialoge."""

import re

from _paths import APP, template_text

WETTER_JS = (APP / "static/js/wetter.js").read_text(encoding="utf-8")


def test_monthly_comparison_stays_visible_after_a_load_error() -> None:
    """Vorher setzte ein Ladefehler `monthly = null` und blendete die ganze Karte samt Kennzahl-Auswahl aus."""
    view = template_text("_wetter_view.html")
    assert 'x-show="monthly || monthlyError"' in view
    assert '@click="loadMonthly()"' in view
    assert "this.monthlyError = this.monthlyLoaded;" in WETTER_JS


def test_every_wetter_dialog_has_an_accessible_name() -> None:
    for name in ("_wetter_view.html", "_wetter_setup.html"):
        text = template_text(name)
        for tag in re.findall(r"<dialog [^>]*>", text):
            match = re.search(r'aria-labelledby="([^"]+)"', tag)
            assert match, f"{name}: Dialog ohne aria-labelledby: {tag[:80]}"
            assert f'id="{match.group(1)}"' in text, f"{name}: {match.group(1)} fehlt"
