"""Regressionstests für geglättete Linien ohne künstliche Randlücken."""


from _paths import APP, DOCS, page_text


ENTITY = page_text("entity_detail.html")
EDITOR = page_text("chart_editor.html")
DASHBOARD = (APP / "static/js/dashboard-tiles.js").read_text(encoding="utf-8")


def test_all_line_chart_renderers_use_smooth_lines() -> None:
    assert "smooth: this.chartType === 'line'" in ENTITY
    assert "main.smooth = true" in EDITOR
    assert "cmp.smooth = true" in EDITOR
    assert "cfg.smooth = true" in DASHBOARD


def test_all_line_chart_renderers_extend_last_value_to_window_end() -> None:
    assert "mainData.push([this.windowEnd * 1000" in ENTITY
    assert "compareData.push([this.windowEnd * 1000" in ENTITY
    assert "mainData.push([this.windowEnd * 1000" in EDITOR
    assert "compareData.push([this.windowEnd * 1000" in EDITOR
    assert "lineData.push([data.window_end * 1000" in DASHBOARD


def test_line_charts_do_not_mix_smoothing_with_step_mode() -> None:
    assert ".step = 'end'" not in ENTITY
    assert ".step = 'end'" not in EDITOR
    assert ".step = 'end'" not in DASHBOARD


SETTINGS_FORM = (APP / "templates/_settings_darstellung_form.html").read_text(encoding="utf-8")


def test_the_rolling_window_switch_is_not_described_as_a_drawing_option() -> None:
    """Der Hinweis unter dem Schalter lautete "Linie statt Treppenstufen
    zwischen Punkten" und beschrieb damit einen Modus, den es hier gar nicht
    gibt — der Test darüber wacht sogar darüber, dass keiner existiert.

    Tatsächlich setzt der Schalter den ZEITRAUM der Abfrage: er schreibt
    entity_chart_defaults["continuous"], das als continuous-Parameter in
    query_series() → _window() landet. Wer ihn nach dem alten Hinweis umlegte,
    änderte nicht das Aussehen der Linie, sondern welche Daten geholt werden.
    """
    # Auf den SICHTBAREN Hinweis geprüft, nicht auf die Datei: der Kommentar
    # darüber hält die Geschichte fest und nennt den alten Wortlaut dabei
    # zwangsläufig.
    import re

    hinweis = re.search(
        r'<div class="settings-compact-label">Rollierend</div>.*?'
        r'<div class="settings-compact-hint">(.*?)</div>',
        SETTINGS_FORM, re.S,
    )
    assert hinweis, "Zeile „Rollierend“ mit Hinweis nicht gefunden"
    assert "Treppenstufen" not in hinweis.group(1)
    assert "Zeitraum" in hinweis.group(1)
    # Der Feldname bleibt: er ist der dokumentierte API-/Formular-Vertrag,
    # umbenannt wurde nur die Beschriftung.
    assert 'name="entity_continuous"' in SETTINGS_FORM


def test_the_chart_options_menu_uses_the_same_word_as_the_settings_page() -> None:
    """Ein Begriff für eine Sache: Optionen-Menü, Einstellungsseite und
    Kachelmenü heißen alle "Rollierend". Vorher hieß dasselbe an zwei Stellen
    "Kontinuierlich" — das Handbuch behalf sich schon mit dem Doppelnamen
    "Kontinuierlich/Rollierend"."""
    tile_menu = (APP / "templates/_dashboard_tile_menu.html").read_text(encoding="utf-8")
    guide = (DOCS / "user-guide.md").read_text(encoding="utf-8")
    assert ">Rollierend</span>" in ENTITY
    assert "Rollierend" in tile_menu
    assert "Kontinuierlich" not in guide
