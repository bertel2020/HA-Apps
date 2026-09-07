"""Regressionstests für die globalen Farbschemata."""

from __future__ import annotations


from jinja2 import Environment, FileSystemLoader, select_autoescape

from _paths import APP_CSS, APP_JS, TEMPLATES, page_text



CSS = APP_CSS


def test_display_settings_offer_both_color_schemes() -> None:
    environment = Environment(
        loader=FileSystemLoader(TEMPLATES),
        autoescape=select_autoescape(["html"]),
    )
    html = environment.get_template("_settings_darstellung_form.html").render(
        color_scheme="home_assistant",
        color_scheme_options=[("zeitarchiv", "Zeitarchiv"), ("home_assistant", "Home Assistant")],
        color_mode="dark",
        color_mode_options=[("auto", "Automatisch"), ("light", "Hell"), ("dark", "Dunkel")],
        font_scale="2",
        font_scale_options=[
            ("1", "Klein"),
            ("2", "Normal"),
            ("3", "Groß"),
        ],
        font_scale_values={"1": "1", "2": "1.125", "3": "1.25"},
        saved=False,
    )
    assert 'type="hidden" name="color_scheme"' in html
    assert 'id="color-scheme-input" value="home_assistant"' in html
    assert "selectDDOption('color-scheme', 'zeitarchiv', 'Zeitarchiv')" in html
    assert "selectDDOption('color-scheme', 'home_assistant', 'Home Assistant')" in html
    assert 'id="color-mode-input" value="dark"' in html
    assert "selectDDOption('color-mode', 'dark', 'Dunkel')" in html
    assert 'id="font-scale-input" value="2"' in html
    for key, scale, label in (
        ("1", "1", "Klein"),
        ("2", "1.125", "Normal"),
        ("3", "1.25", "Groß"),
    ):
        assert f"selectDDOption('font-scale', '{key}', '{label}')" in html
        assert f"setProperty('--font-scale', '{scale}')" in html
    for label in ("Klein", "Normal", "Groß"):
        assert f">{label}</div>" in html
    # Die entfallenen Randstufen dürfen nicht wieder auftauchen.
    for label in ("Kleiner", "Größer"):
        assert f">{label}</div>" not in html
    assert "document.documentElement.dataset.colorScheme = 'home_assistant'" in html
    assert "document.documentElement.dataset.colorMode = 'dark'" in html


def test_all_full_pages_receive_the_persisted_color_scheme() -> None:
    """Farbschema und Hell/Dunkel hängen am <html>-Element — und das gibt es
    seit ZG-04 Schritt 1 nur noch einmal, in base.html.

    Vorher trug jede der 23 Seiten die beiden Attribute selbst; eine neue
    Seite, die sie vergisst, startete im Standardschema. Der Test prüft
    deshalb jetzt beides: dass base.html sie trägt, und dass jede Vollseite
    von base.html erbt statt sich ein eigenes <html> zu bauen."""
    base = (TEMPLATES / "base.html").read_text(encoding="utf-8")
    assert 'data-color-scheme="{{ color_scheme' in base
    assert 'data-color-mode="{{ color_mode' in base

    full_pages = [
        path
        for path in TEMPLATES.glob("*.html")
        if path.read_text(encoding="utf-8").startswith('{% extends "base.html" %}')
    ]
    assert len(full_pages) >= 20, f"nur {len(full_pages)} Templates erben von base.html"
    for path in full_pages:
        assert '<html lang="de"' not in path.read_text(encoding="utf-8"), (
            f"{path.name} baut sich ein eigenes <html> statt base.html zu erben"
        )


def test_home_assistant_scheme_has_light_dark_and_chart_tokens() -> None:
    css = CSS.read_text(encoding="utf-8")
    dashboard_script = (APP_JS / "dashboard-tiles.js").read_text(encoding="utf-8")
    chart_template = page_text("chart_editor.html")
    assert ':root[data-color-scheme="home_assistant"]' in css
    assert "--accent-line:#006787" in css
    assert "--chart-line:#009AC7" in css
    assert "--accent-line:#37C8FD" in css
    assert "--chart-1:#37C8FD" in css
    assert "--accent-contrast:#141414" in css
    assert ':root[data-color-mode="dark"]' in css
    assert ':root[data-color-mode="light"]{color-scheme:light;}' in css
    assert 'input[type="date"],input[type="time"]{color-scheme:inherit;}' in css
    assert "getPropertyValue(`--chart-${i + 1}`)" in dashboard_script
    assert "getPropertyValue(`--chart-${i + 1}`)" in chart_template


def test_modern_scheme_uses_cool_slate_cobalt_and_balanced_chart_tokens() -> None:
    css = CSS.read_text(encoding="utf-8")
    settings = (TEMPLATES / "_settings_darstellung_form.html").read_text(
        encoding="utf-8"
    )
    assert ':root[data-color-scheme="modern"]' in css
    assert "--bg:#F6F7FB" in css
    assert "--accent-line:#3157C8" in css
    assert "--accent-bar:#0E7C86" in css
    assert "--warning:#A96700" in css
    assert "--bg:#0F1218" in css
    assert "--accent-line:#7EA1FF" in css
    assert "--accent-bar:#4FB7B7" in css
    assert "--warning:#E6A15A" in css
    assert "--chart-8:#9AA8BC" in css
    assert "Cobalt/Teal" in settings
