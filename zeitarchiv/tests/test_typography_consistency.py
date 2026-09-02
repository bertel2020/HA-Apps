"""Regressionstests für die gemeinsame Typografie der Weboberfläche."""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT  / "app"
TEMPLATES = APP / "templates"
APP_CSS = APP / "static" / "css" / "app.css"


def _full_page_templates() -> list[Path]:
    return [
        path
        for path in TEMPLATES.glob("*.html")
        if "<!doctype html>" in path.read_text(encoding="utf-8")
    ]


def test_full_pages_load_all_used_ibm_plex_mono_weights() -> None:
    full_pages = _full_page_templates()
    assert len(full_pages) >= 15
    for path in full_pages:
        source = path.read_text(encoding="utf-8")
        assert "IBM+Plex+Mono:wght@400;500;600;700" in source, path.name


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
