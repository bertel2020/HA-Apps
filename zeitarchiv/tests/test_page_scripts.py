"""Seitenlokales JavaScript liegt in Dateien (ZG-04 Schritt 3b).

Das Gegenstück zu test_page_css.py, mit einem Unterschied: beim CSS ließ sich
der Bestand in einem Zug heben, weil 99,9 % der Blöcke kein Jinja enthielten.
Beim JavaScript sind es 9 % — gezählt über die Vorkommen. Über ihre *Position*
sieht es anders aus: das Jinja steht als zusammenhängende Datenpräambel am
Blockanfang (`const X = {{ … | tojson }}`), danach folgen bei den großen
Editoren über 90 % reiner Code. Der Schnitt läuft deshalb an dieser Kante, eine
Seite nach der anderen.

Diese Datei prüft nicht, wie viele Seiten schon umgestellt sind — das wäre eine
Zahl, die bei jedem Schritt nachgezogen werden müsste. Sie prüft die REGEL für
die bereits umgestellten: was inline bleibt, muss vom Server gerendert werden,
und die ausgelagerte Datei muss danach geladen werden, sonst fehlen ihr die
Konstanten.
"""

from __future__ import annotations

import re

from _paths import PAGE_JS, TEMPLATES

LINK = re.compile(
    r'<script src="\{\{ app_root \}\}/static/js/pages/(?P<file>[a-z_]+\.js)\?v=\{\{ js_v \}\}"></script>'
)
INLINE = re.compile(r"<script>\n(?P<body>.*?)\n *</script>", re.S)


def _pages() -> dict[str, str]:
    return {
        path.name: path.read_text(encoding="utf-8")
        for path in sorted(TEMPLATES.glob("*.html"))
        if path.read_text(encoding="utf-8").startswith('{% extends "base.html" %}')
    }


def _converted() -> dict[str, str]:
    return {name: source for name, source in _pages().items() if LINK.search(source)}


def test_at_least_one_page_is_converted() -> None:
    """Sonst prüfte alles Folgende die leere Menge."""
    assert _converted(), "keine Seite verlinkt ein Seitenskript — Test greift ins Leere"


def test_every_page_script_is_named_after_its_page_and_exists() -> None:
    for name, source in _converted().items():
        datei = LINK.search(source).group("file")
        assert datei == name.removesuffix(".html").lstrip("_") + ".js", f"{name} -> {datei}"
        assert (PAGE_JS / datei).is_file(), f"{name}: {datei} fehlt"


def test_no_page_script_is_orphaned_or_shared() -> None:
    """Von zwei Seiten geteilt heißt: gehört als Modul nach static/js/."""
    verwendet: dict[str, list[str]] = {}
    for name, source in _pages().items():
        for datei in LINK.findall(source):
            verwendet.setdefault(datei, []).append(name)
    auf_platte = {p.name for p in PAGE_JS.glob("*.js")}
    assert auf_platte == set(verwendet), (
        f"ohne Verweis: {sorted(auf_platte - set(verwendet))}, "
        f"ohne Datei: {sorted(set(verwendet) - auf_platte)}"
    )
    geteilt = {d: s for d, s in verwendet.items() if len(s) > 1}
    assert not geteilt, f"von mehreren Seiten verlinkt, gehört nach static/js/: {geteilt}"


def test_page_scripts_contain_no_jinja() -> None:
    """Eine .js unter static/ wird ausgeliefert, nicht gerendert.

    Ein `{{ … }}` darin wäre kein Fehler, den irgendwer meldet — es stünde
    wörtlich im Skript und risse es beim Parsen ab.
    """
    for path in sorted(PAGE_JS.glob("*.js")):
        text = path.read_text(encoding="utf-8")
        assert "{{" not in text and "{%" not in text, f"{path.name} enthält Jinja"


#: Was Verhalten ausmacht. Eine Präambel darf Werte enthalten, auch über
#: mehrere Zeilen ({ … } als Objektliteral), aber nichts, was etwas TUT.
VERHALTEN = ("function ", "=>", "if (", "if(", "for (", "for(", "while (",
             "return ", "addEventListener", "querySelector", "fetch(")


def test_what_stays_inline_is_only_what_the_server_renders() -> None:
    """Die eigentliche Regel des Schritts: inline Daten, in der Datei Verhalten.

    Erst über Zeilen mit Jinja formuliert, dann zweimal nachgeschärft, weil die
    Formulierung enger war als die Absicht:

    - Kommentare sind kein Code. Ein `const DECIMALS = {{ decimals }}` braucht
      seine Begründung neben sich, nicht in einer anderen Datei (chart_editor:
      acht solche Zeilen, entity_detail: 24).
    - Ein Wert darf mehrzeilig sein. `dashboard_editor` reicht vier gerenderte
      Startwerte als ein Objekt durch; dessen `{`- und `};`-Zeilen tragen selbst
      kein Jinja und wären nach der alten Formulierung verboten gewesen.

    Geprüft wird deshalb, was gemeint war: im Inline-Block steht nichts, was
    etwas tut.
    """
    for name, source in _converted().items():
        for block in INLINE.finditer(source):
            for zeile in block.group("body").splitlines():
                nackt = zeile.strip()
                if not nackt or nackt.startswith("//"):
                    continue
                treffer = [w for w in VERHALTEN if w in nackt]
                assert not treffer, (
                    f"{name}: {nackt[:70]!r} im Inline-Block — {treffer[0]!r} ist "
                    "Verhalten und gehört in die Datei"
                )


def test_the_page_script_is_loaded_after_its_data() -> None:
    """Reihenfolge, nicht Vorhandensein.

    Die Präambel legt die Konstanten an, die Datei benutzt sie auf oberster
    Ebene. Stünde der <script src> davor, wäre jede davon in der temporalen
    Todeszone — die Seite bliebe leer, und zwar erst zur Laufzeit.

    Seiten ganz ohne gerenderte Werte haben keine Präambel (acht der siebzehn).
    Dort gibt es nichts zu ordnen.
    """
    for name, source in _converted().items():
        link = LINK.search(source)
        letzter_inline = max((m.end() for m in INLINE.finditer(source)), default=None)
        if letzter_inline is None:
            continue
        assert link.start() > letzter_inline, (
            f"{name}: das Seitenskript wird VOR seiner Datenpräambel geladen"
        )


def test_the_bytes_really_left_the_templates() -> None:
    """Sonst bliebe alles grün, wenn jemand den Code zusätzlich stehen lässt.

    Über alle umgestellten Seiten zusammen statt je Seite: eine kleine Seite
    mit vier gerenderten Startwerten (dashboard_editor) hat naturgemäß ein
    ungünstiges Verhältnis, ohne dass daran etwas falsch wäre. In der Summe
    fällt eine stehengebliebene Kopie trotzdem sofort auf.
    """
    inline = sum(
        len(m.group("body"))
        for source in _converted().values()
        for m in INLINE.finditer(source)
    )
    ausgelagert = sum(p.stat().st_size for p in PAGE_JS.glob("*.js"))
    assert ausgelagert > 20 * inline, (
        f"{inline} B inline gegenüber {ausgelagert} B in Dateien — "
        "sieht nach Kopien aus, nicht nach Verschiebungen"
    )
