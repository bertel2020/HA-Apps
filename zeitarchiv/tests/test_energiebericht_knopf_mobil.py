"""Der Bericht-Knopf wird auf dem Telefon nicht angeboten.

Der Energiebericht ist eine eigenständige, druckoptimierte Seite ohne mobiles
Layout — sein Kennzahlen-Raster steht auf `repeat(3, 1fr)`, und `1fr` kann
nicht unter den Inhalt schrumpfen. Gemessen laufen bei 375px 299px aus dem
Fenster, bei 640px noch 14px; ab 674px passt es. Ein Knopf, der auf dem Telefon
in eine Seite führt, die dort nicht funktioniert, ist die schlechtere Antwort
als kein Knopf.
"""

from __future__ import annotations

from _paths import APP


def test_the_report_button_is_hidden_below_the_width_the_report_needs() -> None:
    css = (APP / "static/css/pages/energiedashboard.css").read_text(encoding="utf-8")
    mobil = css[css.index("  @media (max-width:700px){"):]
    assert ".edash-report-btn{display:none;}" in mobil[: mobil.index("}\n  }") + 5]


def test_the_report_itself_still_has_no_mobile_layout() -> None:
    """Sichert die Begründung ab: fällt die Ausnahme oben irgendwann weg, weil
    der Bericht ein mobiles Layout bekommt, schlägt dieser Test zuerst fehl und
    erinnert daran, den Knopf wieder freizugeben."""
    css = (APP / "static/css/pages/energiedashboard_report.css").read_text(encoding="utf-8")
    medien = [z.strip() for z in css.splitlines() if z.strip().startswith("@media")]
    assert medien == ["@media print{"], medien
    assert "grid-template-columns:repeat(3,1fr)" in css.replace(" ", "")
