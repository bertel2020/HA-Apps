"""Markierungs-Filter als Dropdown, Werte-Tabelle auf dem Telefon, Menü-Klemmung.

Drei Änderungen an derselben mobilen Ansicht, die je einen früheren Zustand
festschreiben, damit er nicht unbemerkt zurückkehrt.
"""

from __future__ import annotations

from jinja2 import Environment, FileSystemLoader

from app.formatting import format_int, format_value

from _paths import APP, TEMPLATES, page_text


def _css(name: str) -> str:
    return (APP / f"static/css/{name}").read_text(encoding="utf-8")


def test_the_marking_filter_is_one_dropdown_and_not_a_row_of_chips() -> None:
    """`.filter-chip` ist app-weit die MEHRFACH-Auswahl (Typ-Filter der
    Startseite, Kennzahlen in der Legende). Der Markierungsfilter ist eine
    Einfachauswahl — als Pillenreihe sah er aus wie das eine und verhielt sich
    wie das andere, brauchte eine eigene Werkzeugzeile und brach auf dem
    Telefon in zwei Reihen um."""
    cleanup = page_text("cleanup.html")
    menu = (TEMPLATES / "_rows_filter_menu.html").read_text(encoding="utf-8")

    assert 'class="filter-chip"' not in cleanup
    assert "toolbar-filters" not in cleanup
    assert 'name="filter"' in cleanup, "Wert lebt weiter in #controls"
    assert 'id="rows-filter-input"' in cleanup, "dd-picker.js erwartet {prefix}-input"
    assert "js/dd-picker.js" in cleanup

    assert 'id="rows-filter-btn"' in menu
    assert 'id="rows-filter-popover"' in menu
    assert "toggleDDPicker('rows-filter')" in menu
    assert "selectDDOption('rows-filter'" in menu
    # Der Filter sitzt außerhalb des hx-targets und muss deshalb bei jedem
    # Zeitraum-/Seitenwechsel per oob nachgezogen werden — sonst blieben die
    # Zahlen auf dem Stand des ersten Aufrufs stehen.
    assert 'hx-swap-oob="true"' in menu
    assert '{% include "_rows_filter_menu.html" %}' in (
        TEMPLATES / "_rows_table.html"
    ).read_text(encoding="utf-8")


def test_every_marking_carries_its_hit_count_and_empty_ones_are_not_selectable() -> None:
    """Die Zahlen stehen ohnehin in derselben Antwort (counts.*, dieselbe
    Quelle wie die Kennzahlen-Tabelle darüber). Ohne sie sahen alle Kategorien
    gleich aus, und die Auswahl einer leeren endete zwangsläufig auf „Keine
    Werte in diesem Zeitraum/Filter“."""
    menu = (TEMPLATES / "_rows_filter_menu.html").read_text(encoding="utf-8")
    for schluessel in ("all", "outliers", "gaps", "duplicates", "repetitions", "counter_decreases"):
        assert f"counts['{schluessel}']" in menu, schluessel
    assert "| format_int" in menu
    # Leere Einträge bekommen kein onclick, sind also nicht nur ausgegraut.
    assert "{% if not leer %}onclick=" in menu

    css = _css("pages/cleanup.css")
    assert ".dd-picker-row-count.is-empty{" in css
    # Treffer rot wie in der Kennzahlen-Tabelle (dort .has-issues) — beide
    # zeigen dieselben Zahlen und sollen dieselbe Aussage machen.
    assert ".dd-picker-row-count.has-issues .n{" in css


def test_the_values_table_stays_a_table_on_phones() -> None:
    """Karten lohnen sich, wo eine breite Liste auf die Namensspalte
    zusammenschrumpft. Hier sind es vier schmale Spalten — als Karten stünde
    jeder Wert einzeln, und das Vergleichen aufeinanderfolgender Zeitstempel
    (worum es beim Bereinigen geht) wäre vorbei."""
    rows = (TEMPLATES / "_rows_table.html").read_text(encoding="utf-8")
    assert 'data-cards="off"' in rows
    assert "rows-values-table" in rows
    # Die Breiten dürfen NICHT inline stehen: unter 640px werden sie auf auto
    # zurückgenommen, und ein Inline-Stil ließe sich nur mit !important
    # überschreiben. Geprüft wird nur DIESE Tabelle — die Kennzahlen-Tabelle
    # weiter oben in derselben Datei trägt ihre Breiten weiterhin inline.
    werte_tabelle = rows[rows.index('class="dt compact rows-values-table"'):]
    werte_tabelle = werte_tabelle[: werte_tabelle.index("</table>")]
    assert "table-layout" not in werte_tabelle
    assert 'style="width:' not in werte_tabelle
    assert "<col><col><col><col>" in werte_tabelle

    css = _css("pages/cleanup.css")
    assert ".rows-values-table{table-layout:fixed;}" in css
    mobil = css[css.index("@media (max-width:640px)"):]
    assert "table-layout:auto" in mobil
    # Zeitstempel und Wert dürfen nicht umbrechen — sonst wird die Tabelle
    # nicht schmaler, sondern jede Zeile doppelt so hoch (der Wert trennt sich
    # dabei von seiner .value-unit).
    assert ".rows-values-table td:nth-child(2),\n    .rows-values-table td:nth-child(3){white-space:nowrap;}" in css
    # Die Markierungsspalte trägt 16-18px Inhalt; mit der Standardpolsterung von
    # 2x14px war sie 48px breit. Der Selektor MUSS table.dt.compact enthalten:
    # die Polsterung kommt aus `table.dt.compact td` (0-2-2) und schlüge eine
    # Regel aus Klasse + :first-child (0-2-1).
    assert "table.dt.compact.rows-values-table td:first-child{padding-left:8px" in css
    # Feste px-Breite, kein kleiner Prozentwert: bei table-layout:fixed skaliert
    # der Browser ALLE Spalten proportional, wenn die Summe nicht aufgeht — 4 %
    # ergaben nachgemessen 45px bei 1.120px Tabellenbreite, aber nur 28px bei
    # 700px. Mit 38px bleibt sie über den ganzen gemessenen Bereich konstant.
    assert ".rows-values-table col:nth-child(1){width:38px;}" in css
    # Die übrigen drei müssen auf 100 % summieren, sonst wächst Spalte 1 wieder
    # mit (das war der Fehler des ersten Anlaufs).
    prozente = [
        int(css.split(f".rows-values-table col:nth-child({n}){{width:")[1].split("%")[0])
        for n in (2, 3, 4)
    ]
    assert sum(prozente) == 100, prozente


def test_the_action_bar_is_the_head_of_the_table() -> None:
    """Gemessen bei 1.280x900: das erste Auswahlkästchen lag bei y=456, der
    „Löschen"-Knopf bei y=1215 — 759px auseinander und damit außerhalb des
    Bildes, während man die oberen Zeilen ankreuzt. Jetzt 95px."""
    rows = (TEMPLATES / "_rows_table.html").read_text(encoding="utf-8")
    leiste = rows.index('<div class="rows-actionbar">')
    tabelle = rows.index('class="dt compact rows-values-table"')
    pager = rows.index('<div class="pager"')
    assert leiste < tabelle < pager, "Leiste über der Tabelle, Pager darunter"

    # Was an der Auswahl hängt, steht links; was für die ganze Tabelle gilt
    # (Rückgängig) oder für den Filter (Sammelaktionen), rechts.
    rechts = rows[rows.index('<span class="rows-actionbar-right">'):]
    rechts = rechts[: rechts.index("</span>")]
    assert "undo-preview-btn" in rechts
    assert "duplicates-preview" in rechts
    assert "repetitions-preview" in rechts
    assert "needs-selection" not in rechts, "Löschen gehört zur Auswahl, also links"

    css = _css("pages/cleanup.css")
    # Optik von .table-resize-actions: getönter Grund, gemeinsamer Rahmen, oben
    # abgerundet — die Leiste soll als Teil der Tabelle lesbar sein.
    assert ".rows-actionbar + .tbl-wrap{border-top-left-radius:0" in css
    assert "border-radius:12px 12px 0 0;" in css
    # Sie verschwindet nicht bei leerer Auswahl, sondern wird blass — sonst
    # rutschte die Tabelle beim ersten Kreuz unter dem Finger weg.
    assert ".rows-actionbar.has-selection strong{" in css
    js = (APP / "static/js/pages/cleanup.js").read_text(encoding="utf-8")
    assert "classList.toggle('has-selection', count > 0)" in js


def test_the_options_menu_is_clamped_into_the_window() -> None:
    """Gemessen bei 375px: das Menü ist fest 290px breit und mit left:0 an
    seinem Knopf verankert (links 257) — es lief bis 547 und ragte 172px aus
    dem Fenster. Da <html> overflow-x:hidden trägt, war das nicht wegscrollbar:
    die Schalter sitzen rechtsbündig und waren unerreichbar."""
    skript = (APP / "static/js/menu-popover-clamp.js").read_text(encoding="utf-8")
    # Gegen documentElement.clientWidth, nicht innerWidth: das ist die Box, an
    # der overflow-x:hidden abschneidet. Geprüft wird der Code ohne Kommentare,
    # denn dort steht innerWidth als Begründung sehr wohl.
    code = "\n".join(z for z in skript.splitlines() if not z.strip().startswith("//"))
    assert "documentElement.clientWidth" in code
    assert "innerWidth" not in code
    # left und nicht transform — transform gehört der Öffnen-Animation.
    assert "pop.style.left" in skript
    assert "style.transform" not in skript

    for seite in ("entity_detail.html", "chart_editor.html"):
        assert "js/menu-popover-clamp.js" in page_text(seite), seite

    # Höhe: 621px Menü auf einem 812px hohen Bildschirm — innen scrollen statt
    # die Seite unter dem eigenen Auslöser wegzuziehen.
    app_css = _css("app.css")
    assert ".menu-popover{max-height:70vh;overflow-y:auto;}" in app_css


def test_rows_filter_menu_compiles() -> None:
    environment = Environment(loader=FileSystemLoader(TEMPLATES))
    environment.filters["format_int"] = format_int
    environment.filters["format_value"] = format_value
    environment.get_template("_rows_filter_menu.html")
    environment.get_template("_rows_table.html")
    environment.get_template("cleanup.html")
