"""Regressionstests gegen seitwärts überlaufende Seiten auf dem Telefon.

Diese Tests messen kein echtes Layout — das geht nur im Browser, und dort ist
der Befund erhoben worden (390px Viewport, `documentElement.scrollWidth` gegen
`clientWidth`, 17 Seiten). Was hier festgehalten wird, sind die CSS-Invarianten,
aus denen der Überlauf jeweils entstanden ist: eine randlos gezogene Kopfzeile,
die nicht mehr zum Body-Padding passt, Flex-Leisten ohne Umbruch und
Tooltip-Boxen, die versteckt trotzdem Fläche belegen.
"""

from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
APP_CSS = APP / "static" / "css" / "app.css"
TEMPLATES = APP / "templates"


def _media_block(css: str, query: str) -> str:
    """Alles, was bei dieser Bedingung gilt. `app.css` hat pro Bedingung mehrere
    Blöcke (jeweils bei dem Bauteil, um das es geht) — die werden hier
    zusammengezogen. @media-Blöcke sind in dieser Datei nicht verschachtelt,
    deshalb endet einer bei der ersten Zeile, die nur `}` enthält."""
    marker = f"@media ({query}){{"
    blocks, start = [], css.find(marker)
    while start != -1:
        blocks.append(css[start:css.index("\n}", start)])
        start = css.find(marker, start + 1)
    assert blocks, f"kein @media ({query})-Block gefunden"
    return "\n".join(blocks)


def _shorthand(block: str, selector: str, prop: str) -> list[str]:
    rule = re.search(re.escape(selector) + r"\{([^}]*)\}", block)
    assert rule, f"{selector} fehlt in diesem Block"
    value = re.search(rf"(?:^|;)\s*{prop}:([^;]+)", rule.group(1))
    assert value, f"{prop} fehlt in {selector}"
    return value.group(1).split()


def test_topnav_negative_margin_matches_the_body_padding_on_phones() -> None:
    """Die Kopfzeile zieht sich per negativem Rand über das Body-Padding hinaus,
    damit sie randlos sitzt. Passen die beiden Werte nicht zusammen, ragt sie um
    die Differenz heraus — und weil `.topnav` auf jeder Seite steht, scrollt
    dann die GANZE App seitwärts (vorher: 24-16 = 8px auf 15 von 17 Seiten)."""
    css = APP_CSS.read_text(encoding="utf-8")
    for query in ("max-width:480px",):
        block = _media_block(css, query)
        top, side, _bottom = _shorthand(block, "body", "padding")
        margin = _shorthand(block, ".topnav", "margin")
        assert margin[0] == f"-{top}", f"{query}: topnav-Rand oben {margin[0]} gegen Body-Padding {top}"
        assert margin[1] == f"-{side}", f"{query}: topnav-Rand seitlich {margin[1]} gegen Body-Padding {side}"
        assert _shorthand(block, ".topnav", "padding")[1] == side


def test_pager_wraps_instead_of_pushing_the_page_sideways() -> None:
    """Der Pager trägt zwei Gruppen (Blättern, Zeilen/Seite), die zusammen 402px
    brauchen — auf dem Telefon stehen 358px zur Verfügung."""
    css = APP_CSS.read_text(encoding="utf-8")
    rule = re.search(r"(?:^|\n)\.pager\{([^}]*)\}", css)
    assert rule and "flex-wrap:wrap" in rule.group(1)


def test_the_table_editor_action_bar_wraps() -> None:
    """Fünf Buttons brauchen 650px Inhaltsbreite in einem 358px-Container."""
    source = (TEMPLATES / "table_editor.html").read_text(encoding="utf-8")
    rule = re.search(r"\.tbl-add-row-bar\{([^}]*)\}", source)
    assert rule and "flex-wrap:wrap" in rule.group(1)


def test_tooltips_leave_the_layout_on_narrow_viewports() -> None:
    """visibility:hidden versteckt die Sprechblase, nimmt sie aber nicht aus dem
    Layout — eine bis zu 342px breite Box an einem weit rechts stehenden Host
    schob das Dokument über den Rand (gemessen: 666px bei 390px Viewport).
    Hover und Fokus holen sie zurück, damit ein schmales Desktop-Fenster die
    Erklärungen behält; auf Touch gibt es beides nicht."""
    block = _media_block(APP_CSS.read_text(encoding="utf-8"), "max-width:640px")
    hidden = re.search(r"\[data-tooltip\]::after,\.entity-tooltip\{([^}]*)\}", block)
    assert hidden and "display:none" in hidden.group(1)
    assert "[data-tooltip]:hover::after" in block
    assert ".entity-tooltip-host:focus-within>.entity-tooltip" in block
    assert re.search(r"\.entity-tooltip-host:focus-within>\.entity-tooltip\{[^}]*display:block", block)
