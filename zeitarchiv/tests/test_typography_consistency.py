"""Regressionstests für die gemeinsame Typografie der Weboberfläche."""

from __future__ import annotations

import re
from pathlib import Path

from _paths import TEMPLATES, APP_CSS, APP_JS




def _full_page_templates() -> list[Path]:
    """Seit ZG-04 Schritt 1 hat nur noch `base.html` einen eigenen `<head>`.
    „Vollseite" heißt deshalb nicht mehr „enthält <!doctype>", sondern
    „erbt von base.html"."""
    return [
        path
        for path in TEMPLATES.glob("*.html")
        if path.read_text(encoding="utf-8").startswith('{% extends "base.html" %}')
    ]


def test_full_pages_load_all_used_ibm_plex_mono_weights() -> None:
    """Die Schriftschnitte kommen aus einer einzigen Zeile in base.html —
    und keine Seite bringt daneben noch einen eigenen Fonts-Link mit.

    Die zweite Hälfte ist der eigentliche Wert: vorher stand derselbe Link
    23-mal da und musste 23-mal stimmen. Jetzt wäre eine 24. Kopie eine
    Regression, keine Pflicht."""
    base = (TEMPLATES / "base.html").read_text(encoding="utf-8")
    assert "IBM+Plex+Mono:wght@400;500;600;700" in base
    assert "IBM+Plex+Sans:wght@400;500;600;700" in base

    full_pages = _full_page_templates()
    assert len(full_pages) >= 20, f"nur {len(full_pages)} Templates erben von base.html"
    for path in full_pages:
        source = path.read_text(encoding="utf-8")
        assert "fonts.googleapis.com" not in source, f"{path.name} lädt Schriften selbst"


def test_code_and_icon_buttons_inherit_the_shared_fonts() -> None:
    css = APP_CSS.read_text(encoding="utf-8")
    assert "button{font-family:inherit;}" in css
    assert "code,.mono{font-family:var(--font-mono);}" in css

    sources = [css]
    sources.extend(path.read_text(encoding="utf-8") for path in TEMPLATES.glob("*.html"))
    combined = "\n".join(sources)
    assert "font-family:Arial" not in combined


def test_template_font_sizes_use_the_global_scale() -> None:
    sources = [APP_CSS.read_text(encoding="utf-8")]
    sources.extend(path.read_text(encoding="utf-8") for path in TEMPLATES.glob("*.html"))
    combined = "\n".join(sources)
    assert re.search(r"font-size\s*:\s*\d+(?:\.\d+)?px", combined) is None


def test_both_statistic_charts_scale_their_canvas_typography() -> None:
    source = (TEMPLATES / "statistik.html").read_text(encoding="utf-8")
    scaled_text_style = "fontSize: Math.round(12 * uiFontScale * 10) / 10"
    assert source.count(scaled_text_style) == 2


def _chart_sources() -> dict[str, str]:
    """Alle Stellen, an denen ECharts konfiguriert wird — eigene JS-Module und
    die Skriptblöcke der Templates. app.css ist bewusst NICHT dabei: dort
    dürfen die Schriftfamilien als Literal stehen, das ist ihre Definition."""
    sources = {path.name: path.read_text(encoding="utf-8") for path in APP_JS.glob("*.js")}
    sources.update(
        {path.name: path.read_text(encoding="utf-8") for path in TEMPLATES.glob("*.html")}
    )
    return sources


def test_chart_typography_never_hardcodes_a_pixel_size() -> None:
    """ECharts rendert Beschriftungen ins Canvas, wo --font-scale nicht greift —
    jede Größe muss deshalb im Skript durch die Skalierung laufen. Eine nackte
    Zahl bedeutet: diese Beschriftung ignoriert die Schriftgrößen-Einstellung."""
    for name, source in _chart_sources().items():
        assert re.search(r"fontSize\s*:\s*\d", source) is None, name


def test_chart_typography_takes_font_families_from_the_shared_tokens() -> None:
    """Ein wiederholter Font-Stack im Skript zieht bei einem Wechsel der
    Schriftart nicht mit — die Familien kommen aus --font-mono/--font-display."""
    for name, source in _chart_sources().items():
        assert "'IBM Plex" not in source, name
        assert '"IBM Plex' not in source, name


def test_energiedashboard_reads_the_font_scale_at_call_time() -> None:
    """Die Auswahl in Einstellungen → Darstellung setzt --font-scale sofort am
    Wurzelelement. Ein beim Skriptstart gecachter Faktor (wie in
    dashboard-tiles.js, das keine Live-Umschaltung kennt) würde die Änderung
    hier erst beim nächsten Seitenaufruf übernehmen."""
    source = (APP_JS / "energiedashboard.js").read_text(encoding="utf-8")
    assert "function scaledFont(size)" in source
    assert "parseFloat(cssVar('--font-scale'))" in source
    assert "function fontMono()" in source and "cssVar('--font-mono')" in source
    assert "function fontDisplay()" in source and "cssVar('--font-display')" in source


def test_only_loaded_font_weights_are_used() -> None:
    """Ein Gewicht, das nicht geladen ist, verschwindet nicht — es fällt nach den
    CSS-Matching-Regeln still auf einen geladenen Schnitt zurück. Genau daran ist
    die „fett" markierte Trennzeile gescheitert: 650 und 800 landeten beide auf
    700 und waren dadurch nicht zu unterscheiden. Der Fehler ist im Browser
    unsichtbar, deshalb muss ihn der Test sehen.

    Quelle der geladenen Schnitte ist bewusst die <link>-Zeile selbst und keine
    Liste im Test — wird sie geändert (etwa beim Umstieg auf selbst gehostete
    Schriften, ZG-14), zieht die Prüfung automatisch mit."""
    loaded: set[int] = set()
    for path in TEMPLATES.glob("*.html"):
        for spec in re.findall(r"IBM\+Plex\+\w+:wght@([\d;]+)", path.read_text(encoding="utf-8")):
            loaded.update(int(weight) for weight in spec.split(";"))
    assert loaded, "keine Google-Fonts-Einbindung gefunden — Quelle der Schnitte prüfen"

    sources = {path.name: path.read_text(encoding="utf-8") for path in TEMPLATES.glob("*.html")}
    sources[APP_CSS.name] = APP_CSS.read_text(encoding="utf-8")
    sources.update({path.name: path.read_text(encoding="utf-8") for path in APP_JS.glob("*.js")})
    for name, source in sources.items():
        used = {
            int(weight)
            for weight in re.findall(r"font-weight:\s*(\d+)|fontWeight:\s*(\d+)", source)
            for weight in weight
            if weight
        }
        assert used <= loaded, f"{name}: {sorted(used - loaded)} nicht geladen (geladen: {sorted(loaded)})"


def _regel(css: str, selektor: str) -> str:
    """Der Rumpf einer CSS-Regel, ohne verschachtelte Blöcke."""
    start = css.index(selektor + "{") + len(selektor) + 1
    return css[start : css.index("}", start)]


def test_choice_buttons_use_the_display_font_not_mono() -> None:
    """docs/frontend.md: --font-mono steht in dieser App für maschinenlesbar
    (Entity-IDs, Zeitstempel, Rohwerte). Beschriftungen bekommen --font-display.

    Die Knopfreihen der Kachel-Einstellungen zeigen Wörter — "Woche",
    "Rollierend", "Aktuell", "Roh", "1 Std". Sie standen zunächst in Mono, weil
    die Klasse von der Nachkommastellen-Reihe abgeschrieben war. Mono auf
    Wörtern entwertet die Unterscheidung, die sie sonst trägt: "liegt Mono auch
    auf Fließtext, sagt sie nichts mehr aus".
    """
    css = APP_CSS.read_text(encoding="utf-8")
    for selektor in (".dtile-choice-cell", ".dtile-sparkline-resolution-cell"):
        assert "var(--font-display)" in _regel(css, selektor), selektor
        assert "var(--font-mono)" not in _regel(css, selektor), selektor


def test_real_values_keep_the_mono_font() -> None:
    """Gegenprobe: die Umstellung darf nicht durchschlagen, wo der Text
    tatsächlich der Wert ist — "1×1" bei der Kachelgröße und die Ziffern
    0/1/2/3 bei den Nachkommastellen. Sonst wäre die Unterscheidung nicht
    geschärft, sondern abgeschafft."""
    for name in ("dashboard_detail.html", "entities.html"):
        quelle = (TEMPLATES / name).read_text(encoding="utf-8")
        assert "var(--font-mono)" in _regel(quelle, ".dtile-size-picker-head strong"), name
        assert "var(--font-mono)" in _regel(quelle, ".dtile-decimals-cell"), name
