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

from _paths import APP_CSS, APP_JS, TEMPLATES, page_text


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
    source = page_text("table_editor.html")
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


def test_every_page_with_cards_loads_the_card_script() -> None:
    """Ursprünglich prüfte dieser Test etwas anderes: Die App schrieb denselben
    Präfix auf drei Arten (ZG-03), und wer beim Einfügen eines Skripts die
    falsche erwischte, merkte es nur auf der Seite mit abweichender Pfadtiefe —
    dort fiel der Kartenmodus still aus, ohne Fehlermeldung.

    Diese Frage ist mit ZG-05 nicht beantwortet, sondern ENTFALLEN: Ein
    Skript-Tag nennt seit asset() nur noch den Pfad, den Präfix liefert die
    Funktion. Uneinheitlich schreiben lässt er sich damit nicht mehr.

    Was bleibt, ist die andere Hälfte und der eigentliche Regressionsschutz:
    dass die Seiten mit Kartenmodus ihr Skript überhaupt laden."""
    pattern = re.compile(r"\{\{ asset\('js/([^']+)\.js'\) \}\}")
    pages = 0
    for path in TEMPLATES.glob("*.html"):
        if "table-cards" in set(pattern.findall(path.read_text(encoding="utf-8"))):
            pages += 1
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
def _inline_media_block(source: str, query: str) -> str:
    """Wie `_media_block`, aber für die `<style>`-Blöcke in den Templates: die
    sind eingerückt, ihr schließendes `}` steht also nicht am Zeilenanfang.
    Deshalb hier über Klammerzählung statt über die Spalte."""
    marker = f"@media ({query}){{"
    start = source.find(marker)
    assert start != -1, f"kein @media ({query})-Block gefunden"
    tiefe, i = 0, start + len(marker) - 1
    while i < len(source):
        if source[i] == "{":
            tiefe += 1
        elif source[i] == "}":
            tiefe -= 1
            if tiefe == 0:
                return source[start:i]
        i += 1
    raise AssertionError(f"@media ({query}) wird nicht geschlossen")


def test_the_background_process_hint_gets_the_full_row_width_on_phones() -> None:
    """Der Hinweis je Wartungsplaner-Aufgabe steckte als verschachteltes <div>
    zusammen mit dem Namen links neben Zeitstempel und Status. Auf 375px blieben
    ihm dadurch 143px — im Browser gemessen 13-19 Zeichen je Zeile, ein Satz von
    60 Zeichen brauchte vier davon. Der Abschnitt "Diagnose" bestand zu 40% aus
    Hinweistext, obwohl die Texte kurz sind.

    Der Hinweis ist jetzt ein eigenes Kind der Zeile (deshalb Grid statt Flex:
    nur Geschwister lassen sich einzeln umbrechen) und zieht sich unter 640px
    über beide Spalten. Gemessen danach: 293px statt 143px, 17 statt 35 Zeilen,
    29% statt 40%. Der Test hängt an beidem — an der Geschwister-Struktur im
    Markup und an der Regel, die sie mobil ausnutzt.
    """
    settings = page_text("settings.html")
    zeile = re.search(r'<div class="bgproc-row">(.*?)<div class="bgproc-hint">', settings, re.S)
    assert zeile, ".bgproc-row mit Hinweis nicht gefunden"
    assert "<div>" not in zeile.group(1), (
        "Name/Status stecken wieder in einem Zwischen-<div> — dann kann der "
        "Hinweis nicht mehr über die volle Zeilenbreite gehen"
    )
    assert re.search(r"\.bgproc-row\{[^}]*display:grid", settings)

    block = _inline_media_block(settings, "max-width:640px")
    assert re.search(r"\.bgproc-hint\{[^}]*grid-column:1 / -1", block)
    assert re.search(r"\.bgproc-right\{[^}]*grid-row:1", block)
def test_row_cards_start_collapsed_and_keep_one_value_visible() -> None:
    """Eine Zeilenkarte trägt so viele Zeilen, wie die Tabelle Spalten hat —
    gemessen 289px in der Entitätenliste und 207px auf der Housekeeping-Seite,
    keine drei je Schirmfüllung. Eingeklappt bleiben Überschrift, Leitwert und
    die Bedienspalten davor (Favoriten-Stern); danach 135px bzw. 107px.

    Der Test hält beide Hälften fest, weil die Wirkung nur aus ihrem
    Zusammenspiel entsteht: JS vergibt die Klassen, CSS blendet aus. Fehlt eine
    der drei :not()-Ausnahmen, verschwindet in der eingeklappten Karte genau
    das, was sie noch zeigen soll.
    """
    js = (APP_JS / "table-cards.js").read_text(encoding="utf-8")
    css = APP_CSS.read_text(encoding="utf-8")

    assert "dt-card-collapsible" in js and "dt-card-lead" in js and "dt-card-pre" in js
    # Summenzeilen tragen keine Details — sie dürfen keinen Aufklapper bekommen.
    assert "TFOOT" in js

    block = _media_block(css, "max-width:640px")
    regel = re.search(
        r"tr\.dt-card-collapsible:not\(\.is-open\)([^{]*)\{([^}]*)\}", block
    )
    assert regel, "Regel für die eingeklappte Karte fehlt"
    for behalten in ("dt-card-title", "dt-card-lead", "dt-card-pre"):
        assert f":not(.{behalten})" in regel.group(1), f"{behalten} würde mit ausgeblendet"
    assert "display:none" in regel.group(2)


def test_the_lead_value_of_a_row_card_can_be_named_by_the_table() -> None:
    """Von selbst ist der Leitwert die erste Spalte nach dem Namen. In der
    Entitätenliste steht dort "Typ" — eine Einordnung, kein Wert; gesucht wird
    auf dem Telefon danach, wann eine Entität zuletzt etwas geliefert hat.
    Deshalb data-card-lead, und deshalb genau dort gesetzt."""
    js = (APP_JS / "table-cards.js").read_text(encoding="utf-8")
    tabelle = (TEMPLATES / "_entities_table.html").read_text(encoding="utf-8")
    assert "table.dataset.cardLead" in js
    # Greift der Name ins Leere, gilt wieder die erste Spalte: ein Tippfehler im
    # Template darf das Einklappen nicht abschalten.
    assert "benannt >= 0 ? benannt :" in js
    assert 'data-card-lead="Letzter Wert"' in tabelle
def test_the_list_toolbar_collapses_into_one_menu_without_a_second_form() -> None:
    """Sechs Bedienelemente über der Entitätenliste, auf 375px vier Reihen und
    161px Höhe. Sie ziehen in ein Menü — aber als DIESELBEN Elemente und
    innerhalb von #controls, sonst wären Feldnamen, hx-include und die
    Auswertung im Server plötzlich doppelt gepflegt. Gemessen danach: eine
    Reihe, 36px, erste Karte bei 315 statt 403px.

    Der Test hält die drei Eigenschaften fest, an denen das hängt: verschoben
    statt nachgebaut, Anker für den Rückweg auf breiten Bildschirmen, und kein
    MutationObserver — mit einem lief das Skript gegen den in table-cards.js,
    und die Seite blieb stehen.
    """
    js = (APP_JS / "list-settings-menu.js").read_text(encoding="utf-8")
    liste = page_text("entities_list.html")

    assert "list-settings-menu.js" in liste
    # Verschoben, nicht nachgebaut: die Elemente wandern in das Popover.
    assert "popover.appendChild(el)" in js
    # Und finden zurück, wenn der Bildschirm wieder breit ist.
    assert "createComment" in js and "insertBefore(el, platz)" in js
    # Auf den Aufruf geprüft, nicht auf das Wort: der Kommentar im Skript hält
    # fest, warum dort keiner steht, und nennt ihn dabei zwangsläufig.
    assert "new MutationObserver" not in js, (
        "Ein Beobachter auf dem Dokument schaukelt sich mit dem in "
        "table-cards.js auf — die Seite blieb dabei stehen."
    )


def test_the_search_field_gives_up_its_minimum_width_next_to_the_menu() -> None:
    """Mit min-width:250px passt der Menü-Knopf auf 343px nicht mehr daneben
    und rutscht in eine zweite Reihe — das Menü hätte dann eine von vier
    Reihen gespart statt drei."""
    block = _media_block(APP_CSS.read_text(encoding="utf-8"), "max-width:640px")
    regel = re.search(
        r'#controls:has\(\.list-settings\)[^{]*input\[type="search"\][^{]*\{([^}]*)\}', block
    )
    assert regel, "Regel für das Suchfeld neben dem Menü fehlt"
    assert "min-width:0" in regel.group(1)
    # Beide Bauformen von Werkzeugleiste, sonst bleibt die Kachel-Übersicht
    # zweizeilig, während die Entitätenliste einzeilig ist.
    assert ".card-browser:has(.list-settings)" in regel.group(0)
def test_the_scroll_hint_only_appears_on_tables_that_actually_overflow() -> None:
    """Der Hinweis an den Rändern von .tbl-wrap deutet an, dass seitlich noch
    etwas kommt. Ob das so ist, weiß nur das Layout — deshalb hängt er an einer
    Klasse, die table-cards.js aus scrollWidth gegen clientWidth setzt. Auf der
    Housekeeping-Seite trägt danach 1 von 6 Tabellen den Hinweis statt aller
    sechs."""
    css = APP_CSS.read_text(encoding="utf-8")
    js = (APP_JS / "table-cards.js").read_text(encoding="utf-8")
    assert "scrollWidth > wrap.clientWidth" in js
    # Der Verlauf hängt an der Klasse, nicht an .tbl-wrap selbst: JEDE Regel,
    # die einen Verlauf setzt und .tbl-wrap anspricht, muss ihn auf
    # .is-scrollable einschränken. Nur zu prüfen, dass es die eine Regel gibt,
    # übersieht eine zweite daneben, die ihn wieder allen gibt.
    setzt_verlauf = False
    for regel in re.finditer(r"([^{}]+)\{([^}]*)\}", css):
        selektor, koerper = regel.group(1), regel.group(2)
        if ".tbl-wrap" not in selektor or "background-image" not in koerper:
            continue
        if "none" in koerper.split("background-image")[1].split(";")[0]:
            continue  # die Kartenform schaltet ihn ab, das ist der Sinn
        # Je Teilselektor prüfen: ".tbl-wrap, .tbl-wrap.is-scrollable" enthält
        # das Wort, gilt aber trotzdem für jede Tabelle.
        for teil in selektor.split(","):
            if ".tbl-wrap" not in teil:
                continue
            assert "is-scrollable" in teil, f"Verlauf ohne Messung: {teil.strip()}"
            setzt_verlauf = True
    assert setzt_verlauf, "gar keine Regel setzt den Hinweis"


def test_the_sort_menu_moves_into_the_view_menu_when_there_is_one() -> None:
    """Sortieren ist eine Einstellung der Liste wie Filter und Spalten; auf dem
    Telefon soll es dafür eine Stelle geben. Zwei Fallstricke hält der Test
    fest: bei mehreren Tabellen auf einer Seite wäre im Menü nicht mehr
    erkennbar, welche gemeint ist — und ein Menü, das den htmx-Austausch der
    Liste überlebt, zeigt danach auf Spaltenköpfe, die nicht mehr im Dokument
    stehen."""
    js = (APP_JS / "table-cards.js").read_text(encoding="utf-8")
    assert "list-settings-popover" in js
    assert "querySelectorAll('table.dt').length === 1" in js
    assert "_head.isConnected" in js


def test_long_entity_ids_are_cut_instead_of_wrapped_on_cards() -> None:
    """Die ID ist ein einziges langes Wort. Mit overflow-wrap:anywhere — nötig
    für die Werte darunter — kostete sie in jeder zweiten Karte eine Zeile, in
    der nur zwei, drei Zeichen standen."""
    block = _media_block(APP_CSS.read_text(encoding="utf-8"), "max-width:640px")
    regel = re.search(r"td\.dt-card-title \.ts-time,[^{]*\{([^}]*)\}", block, re.S)
    assert regel, "Regel für die gekürzte Entity-ID fehlt"
    assert "white-space:nowrap" in regel.group(1)
    assert "text-overflow:ellipsis" in regel.group(1)
    assert ".id" in regel.group(0), "die Entitätenliste setzt die ID als span.id"


def test_the_view_menu_needs_a_search_field_to_appear() -> None:
    """#controls heißt auf der Bereinigungs-Seite ein Container, in dem die
    Zeitraum-Leiste steckt — die Hauptbedienung der Seite, nicht ihre
    Einstellungen. Ohne eigenes Suchfeld daneben ist es keine Werkzeugleiste
    dieser Bauart, und das Menü bleibt weg."""
    js = (APP_JS / "list-settings-menu.js").read_text(encoding="utf-8")
    assert "':scope > input[type=\"search\"]'" in js
    assert "if (!suche) return;" in js


def test_the_consumer_share_table_stays_on_one_line_in_a_narrow_container() -> None:
    """Die Verbrauchertabelle steht auf 375px in einer 305px breiten Kachel und
    im zweispaltigen Raster ab 820px neben dem Donut sogar nur in 285px. Mit
    350px Mindestbreite lief sie beide Male seitlich über UND brach lange Namen
    trotzdem um. Die kompakte Fassung hängt deshalb an der Container- statt an
    der Fensterbreite; zwei Details entscheiden, ob sie greift: der Farbpunkt
    darf keine Zeilenbreite belegen, sonst rutscht der Name als Ganzes in die
    zweite Zeile und der Punkt bleibt allein oben stehen — und die Namensspalte
    muss den Rest bekommen, den die nowrap-Zahlenspalten übrig lassen."""
    html = page_text("energiedashboard.html")
    assert re.search(
        r"\.edash-share-wrap \.tbl-wrap\{[^}]*container-type:\s*inline-size", html
    ), "ohne container-type auf .tbl-wrap ist der @container-Block wirkungslos"
    # Der Block endet bei der ersten Zeile, die auf Einrückung der Regelebene
    # nur eine schließende Klammer enthält — verschachtelt ist er nicht.
    block = re.search(r"@container \(max-width:\d+px\)\{(.*?)\n  \}", html, re.S)
    assert block, "kein @container-Block für die Verbrauchertabelle"
    block = block.group(1)
    punkt = re.search(r"\.edash-share-dot\{([^}]*)\}", block)
    assert punkt, "der Farbpunkt bleibt im Textfluss"
    assert "position:absolute" in punkt.group(1)
    breite = re.search(r"([^{}]*td\.label[^{}]*)\{([^}]*)\}", block)
    assert breite, "keine Regel für die Namensspalte"
    assert "width:100%" in breite.group(2), (
        "ohne width:100% verteilt die automatische Tabellenaufteilung den freien "
        "Platz anteilig, und der Name bricht um, obwohl rechts noch Luft ist"
    )


def test_the_pager_text_gets_smaller_on_phones_than_at_the_desk() -> None:
    """Die Blätter-Reihe passt auf 375px auf ein Zeichen genau: "1–20 von 86
    « ‹ Seite 1 / 5 › »" braucht 335px von 343px, eine Stelle mehr im
    Gesamtwert bricht sie um. Mit 11px und 7px Abstand trägt sie "1–20 von
    4.312 … Seite 116 / 216" (339px). Die Knopfgröße bleibt außen vor — die
    Knöpfe sind die Treffflächen der Reihe.

    Mitgeprüft wird die Reihenfolge in der Datei: beide Regeln heißen `.pager`
    und sind gleich spezifisch, ein @media-Block VOR der Grundregel verliert
    also lautlos."""
    css = APP_CSS.read_text(encoding="utf-8")

    def groesse(regel: str) -> float:
        treffer = re.search(r"font-size:calc\(([\d.]+)px", regel)
        assert treffer, f"keine font-size in {regel[:60]}"
        return float(treffer.group(1))

    grund = re.search(r"\n\.pager\{[^}]*font-size[^}]*\}", css)
    assert grund, "Grundregel des Pagers fehlt"
    klein = re.search(r"@media \(max-width:640px\)\{[^@]*?\n  \.pager\{[^}]*font-size[^}]*\}", css)
    assert klein, "der Pager wird auf dem Telefon nicht kleiner gesetzt"
    assert groesse(klein.group(0)) < groesse(grund.group(0))
    assert klein.start() > grund.start(), (
        "die Telefon-Regel steht vor der Grundregel und wird von ihr überschrieben"
    )
    # select und Seiteneingabe tragen ihre eigene font-size und blieben sonst groß.
    block = _media_block(css, "max-width:640px")
    assert re.search(r"\.pager select,\.pager-page-input\{[^}]*font-size", block)


def test_the_row_count_sits_at_the_left_edge_of_the_pager_on_phones() -> None:
    """Der Zählstand ist eine Angabe zur Liste und gehört an dieselbe Kante wie
    deren Namen; rechtsbündig stand er ohne Bezug unter der letzten Karte.
    Alles Übrige bleibt rechts, deshalb margin-right:auto statt einer eigenen
    Ausrichtung. Der flex-grow ist der Teil, der leicht fehlt: die Gruppe um
    den Zählstand ist sonst nur so breit wie ihr Inhalt, und „links" wäre nur
    ihr eigener linker Rand."""
    block = _media_block(APP_CSS.read_text(encoding="utf-8"), "max-width:640px")
    assert re.search(r"\.pager-range\{[^}]*margin-right:auto", block)
    assert re.search(r"\.pager > div:has\(\.pager-range\)\{[^}]*flex-grow:1", block)


def test_the_big_numbers_in_the_stat_tiles_shrink_on_phones() -> None:
    """Zwei Kacheln nebeneinander lassen 136px für den Wert. Bei 19px reicht das
    für elf Zeichen ("486.911.150"); ab einer Milliarde bricht der Wert um, und
    weil word-break:break-word greifen muss, damit er nicht aus der Kachel
    läuft, bricht er mitten in der Zahl. 17px verschiebt die Grenze auf 13
    Zeichen (gemessen 132,5px)."""
    css = APP_CSS.read_text(encoding="utf-8")

    def groesse(regel: str) -> float:
        treffer = re.search(r"font-size:calc\(([\d.]+)px", regel)
        assert treffer, f"keine font-size in {regel[:60]}"
        return float(treffer.group(1))

    grund = re.search(r"\n\.stat \.value\{[^}]*\}", css)
    assert grund, "Grundregel für den Kachelwert fehlt"
    klein = re.search(r"@media \(max-width:640px\)\{\s*\.stat \.value\{[^}]*\}", css)
    assert klein, "der Kachelwert wird auf dem Telefon nicht kleiner gesetzt"
    assert groesse(klein.group(0)) < groesse(grund.group(0))
    # Gleiche Spezifität wie die Grundregel: davor stehend verliert sie lautlos.
    assert klein.start() > grund.start()
    # Der Umbruch bleibt als letzter Ausweg — ohne ihn liefe eine noch längere
    # Zahl über den Kachelrand hinaus.
    assert "word-break:break-word" in grund.group(0)


def test_the_timestamp_tiles_use_the_two_line_form_on_phones() -> None:
    """Auf der Entitäts-Konfiguration steht die Uhrzeit neben dem Datum statt
    darunter, damit "Erster/Letzter Wert" nicht höher werden als die übrigen
    Kacheln derselben Reihe. Auf dem Telefon lässt eine Kachel 133px für den
    Wert, und "17.09.2023 09:41:41" braucht in der großen Schrift knapp 200px —
    es brach mitten in der Uhrzeit um. Dort trägt die Reihe ohnehin nur die
    beiden Zeitstempel-Kacheln, der Grund für die Regel entfällt also genau da,
    wo sie schadet."""
    html = page_text("entity_config.html")
    assert re.search(r"@media \(min-width:641px\)\{\s*\.stat \.ts-time\{", html), (
        "die Inline-Uhrzeit gilt nicht mehr nur am Schreibtisch"
    )
    # Eine zweite, unbedingte Regel würde die Grundform auf dem Telefon wieder
    # aushebeln — die Abfrage allein sagt darüber nichts.
    ohne_media = re.sub(r"@media[^{]*\{.*?\n  \}", "", html, flags=re.S)
    assert ".stat .ts-time{" not in ohne_media


def test_the_card_buttons_lie_above_the_name_link() -> None:
    """Der Namenslink trägt position:relative (für die Tooltip-Box) und steht im
    DOM NACH Stern und Aufklapper. Bei gleichem z-index gewinnt der spätere —
    der Link lag über beiden, und von ihrer Fläche waren real nur die obersten
    Pixel erreichbar (mit elementFromPoint über einem 4px-Raster nachgemessen:
    vom Stern 8 seiner 22px, vom Pfeil 8 seiner 28px). Jeder Griff daneben
    sprang in den Entitätenchart. Ohne den z-index bringt auch die größere
    Fläche nichts, deshalb hängen beide Prüfungen zusammen."""
    block = _media_block(APP_CSS.read_text(encoding="utf-8"), "max-width:640px")
    for selektor in (".dt-card-toggle", "table.dt.dt-cards tbody tr > td.dt-card-pre"):
        regel = re.search(re.escape(selektor) + r"\{([^}]*)\}", block)
        assert regel, f"{selektor} fehlt"
        assert "z-index:1" in regel.group(1), f"{selektor} liegt wieder unter dem Link"
    pfeil = re.search(r"\.dt-card-toggle\{([^}]*)\}", block).group(1)
    assert "width:44px" in pfeil and "height:44px" in pfeil, (
        "28px ist kleiner als eine Fingerkuppe und rundum von einem Link umgeben"
    )


def test_the_card_browser_menu_matches_the_entity_menu() -> None:
    """Dashboards, Charts und Tabellen bringen andere Bauteile mit als die
    Entitätenliste — einen .chip ohne Checkbox dahinter und eine Sortierung,
    deren Beschriftung NEBEN dem Knopf steht. Ohne Zutun sah ihr Menü deshalb
    anders aus als das der Entitätenliste, die hier der Maßstab ist: eine
    mittige gefüllte Pille und eine Zeile aus zwei Kästchen statt gleich
    breiter, linksbündiger Zeilen."""
    block = _media_block(APP_CSS.read_text(encoding="utf-8"), "max-width:640px")
    regel = re.search(r"\.list-settings-popover \.btn,[^{]*\{([^}]*)\}", block, re.S)
    assert regel, "Regel für die Menüzeilen fehlt"
    assert ".list-settings-popover .chip" in regel.group(0), "der Chip bleibt mittig"
    assert "text-align:left" in regel.group(1)
    assert re.search(
        r"\.list-settings-popover \.card-browser-sort-label\{[^}]*display:none", block
    ), "die Beschriftung steht weiterhin neben dem Knopf"
    # Als Pseudo-Element, nicht als Kindknoten: selectDDOption() setzt beim
    # Umschalten textContent und würde einen echten Knoten mitlöschen.
    assert re.search(
        r"\.list-settings-popover \.card-browser-sortgroup \.btn::before\{[^}]*content:'Sortierung: '",
        block,
    )
    # Zwei inline-Hüllen zwischen Menü und Knopf — ohne display:block bezieht
    # sich sein width:100% auf eine Box in Inhaltsbreite.
    assert re.search(
        r"\.list-settings-popover \.card-browser-sortgroup > \.dd-picker-wrap\{[^}]*display:block",
        block,
    )


def test_the_three_card_browsers_order_their_controls_alike() -> None:
    """Im Menü stehen die Bedienelemente in DOM-Reihenfolge untereinander.
    Charts und Tabellen führten "Favoriten zuerst" vor der Sortierung,
    Dashboards und die Entitätenliste danach — im Menü war das direkt
    nebeneinander sichtbar."""
    for name in ("dashboards.html", "charts.html", "tables.html"):
        html = (TEMPLATES / name).read_text(encoding="utf-8")
        assert html.index("card-browser-sortgroup") < html.index("card-browser-fav"), name
