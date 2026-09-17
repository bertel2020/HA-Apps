"""Vergleich (Vormonat/Vorjahresmonat) hat auf der Charts-Seite Vorrang vor
Optionen, mit denen er sich ausschließt (Rohwerte, Zeitstrahl, Gestapelt,
Donut, Auflösung "Voll"/Ausrichtung "Horizontal") — Nutzerwunsch.

Vorher lief das umgekehrt: AKTIVIEREN einer dieser Optionen schaltete einen
bereits aktiven Vergleich stillschweigend ab (z. B. `if (this.raw) this.compare
= false;`). Jetzt sind ihre Bedienelemente disabled, solange Vergleich an ist
— wer sie will, muss Vergleich erst ausschalten, statt dass ein Klick ihn
unbemerkt verwirft.
"""

from _paths import APP, page_text

EDITOR = page_text("chart_editor.html")
CHART_EDITOR_JS = (APP / "static/js/pages/chart_editor.js").read_text(encoding="utf-8")


def test_conflicting_controls_are_disabled_while_compare_is_active() -> None:
    assert ':disabled="compare || ![\'hour\', \'day\', \'week\'].includes(range)"' in EDITOR
    assert ':disabled="compare"' in EDITOR
    assert ":disabled=\"option.value === 'full' && compare\"" in EDITOR


def test_activating_one_of_them_no_longer_silently_turns_compare_off() -> None:
    assert "if (this.raw) this.compare = false;" not in CHART_EDITOR_JS
    assert "if (this.stacked) this.compare = false;" not in CHART_EDITOR_JS
    assert "this.raw = true; this.compare = false;" not in CHART_EDITOR_JS
    assert "if (this.resolutionPreset === 'full') {\n            this.compare = false;" not in CHART_EDITOR_JS


def test_enabling_compare_resets_the_conflicting_options_instead() -> None:
    assert "this.raw = false;\n          this.stacked = false;\n          this.timeline = false;\n          this.donut = false;" in CHART_EDITOR_JS
