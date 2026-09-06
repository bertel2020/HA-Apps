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

from _paths import TEMPLATES, APP_CSS, APP_JS


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


def test_card_mode_neutralises_the_inline_table_min_width() -> None:
    """Listentabellen tragen eine Mindestbreite als Inline-Style, damit ihre
    Spalten am Schreibtisch nicht zusammenfallen (`_entities_table.html` rechnet
    sich 1.028px aus). In der Kartenform gibt es keine Spalten mehr — bleibt die
    Mindestbreite stehen, schiebt sie die ganze Seite seitwärts. Gemessen waren
    das 654px Überlauf bei 390px Viewport. Inline-Style schlägt jede Regel ohne
    !important, deshalb steht es hier."""
    block = _media_block(APP_CSS.read_text(encoding="utf-8"), "max-width:640px")
    rule = re.search(r"table\.dt\.dt-cards\{([^}]*)\}", block)
    assert rule, "Basisregel für den Kartenmodus fehlt"
    assert "min-width:0!important" in rule.group(1).replace(" ", "")


def test_table_cards_skips_tables_whose_grid_carries_the_meaning() -> None:
    """Nicht jede Tabelle darf zu Karten werden. Vergleichstabellen leben von
    ihrem Raster (Trenn- und Summenzeilen, erkennbar an colspan), mehrstufige
    Köpfe gäben je Wert zwei Etiketten, und eine Chart-Legende ist keine Liste,
    sondern Beschriftung neben dem Diagramm."""
    source = (APP_JS / "table-cards.js").read_text(encoding="utf-8")
    assert "[colspan],[rowspan]" in source
    assert "chart-legend-table" in source
    assert "head.rows.length === 1" in source
    assert "MIN_COLUMNS" in source


def test_every_page_with_cards_uses_its_own_url_prefix() -> None:
    """Die App schreibt denselben Präfix auf drei Arten (ZG-03): relativ,
    `{{ base }}` und `{{ app_root }}`. Wer beim Einfügen eines Skripts die
    falsche Variante erwischt, merkt es nur auf der Seite, deren Pfadtiefe
    abweicht — dort fiele der Kartenmodus still aus, ohne Fehlermeldung."""
    pattern = re.compile(r'<script src="([^"]*?)static/js/([^"]+)\.js\?v=\{\{ js_v \}\}"></script>')
    pages = 0
    for path in TEMPLATES.glob("*.html"):
        found = pattern.findall(path.read_text(encoding="utf-8"))
        if "table-cards" not in {name for _, name in found}:
            continue
        pages += 1
        prefixes = {prefix for prefix, _ in found}
        assert len(prefixes) == 1, f"{path.name}: uneinheitliche Präfixe {sorted(prefixes)}"
    assert pages >= 9, f"nur {pages} Seiten laden table-cards.js"


def test_sorting_moves_out_of_the_table_into_a_menu() -> None:
    """Sortieren ist auf dem Telefon eine Einstellung der Liste, keine
    Tabellenkopfzeile. Als Chip-Leiste standen dort vier bis sieben
    Bedienelemente über der ersten Karte; als Menü ist es ein Knopf in der
    Optik der Filter-Dropdowns darüber.

    Der Test hält vor allem fest, dass die Kopfzeile in der Kartenform
    ausnahmslos verschwindet — solange sie als Leiste sichtbar blieb, brauchte
    ihr Auf-/Zuklapper ein zusätzliches <th>, das außerhalb der Kartenform an
    zwei Stellen versteckt werden musste."""
    cards = (APP_JS / "table-cards.js").read_text(encoding="utf-8")
    assert "head.classList.add('dt-cards-head-hidden');" in cards
    assert "dt-cards-sortbar" not in cards, "die Chip-Leiste ist ersetzt, nicht ergänzt"
    assert "dt-cards-sort-toggle" not in cards

    # Das zusätzliche <th> ist weg, also darf auch seine Sonderbehandlung weg
    # sein — sonst bliebe eine Regel stehen, die auf nichts mehr zeigt.
    resizable = (APP_JS / "resizable-tables.js").read_text(encoding="utf-8")
    assert "dt-cards-sort" not in resizable
    css = APP_CSS.read_text(encoding="utf-8")
    assert "dt-cards-sort-toggle" not in css


def test_the_sort_menu_reuses_the_existing_dropdown_look() -> None:
    """Ein neues Element soll aussehen, als hätte es schon immer dazugehört:
    das Menü trägt die Klassen der Filter-Dropdowns aus der Werkzeugleiste
    darüber. Sein Auf und Zu steuert es trotzdem selbst, weil dd-picker.js
    nicht auf allen Seiten mit Listentabellen geladen ist."""
    cards = (APP_JS / "table-cards.js").read_text(encoding="utf-8")
    assert "'dd-picker-wrap dt-cards-sort'" in cards
    assert "'dd-picker-popover dt-cards-sort-popover'" in cards
    assert "'dd-picker-row'" in cards

    css = APP_CSS.read_text(encoding="utf-8")
    # Außerhalb der Kartenform sortiert man über die sichtbaren Spaltenköpfe.
    assert re.search(r"(?m)^\.dt-cards-sort\{[^}]*display:none", css)


def test_the_sort_menu_uses_the_page_own_sorting_mechanism() -> None:
    """Zwei Sortiermechanismen leben nebeneinander: serverseitige Links, die
    per htmx nachladen, und clientseitige Spaltenköpfe (sortable-table.js). Das
    Menü ruft beide über ihren eigenen Weg auf, statt einen dritten zu bauen —
    ein synthetisches link.click() erreicht htmx nicht, htmx.trigger() schon."""
    cards = (APP_JS / "table-cards.js").read_text(encoding="utf-8")
    assert "window.htmx.trigger(link, 'click')" in cards
    assert "cell.click();" in cards
    # Clientseitig lädt nichts nach, also zieht die Beschriftung von Hand nach.
    assert "setTimeout(mark, 0);" in cards
