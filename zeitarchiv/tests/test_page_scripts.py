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


def test_what_stays_inline_is_only_what_the_server_renders() -> None:
    """Die eigentliche Regel des Schritts.

    Ohne sie bliebe „ein bisschen Code oben, der Rest in der Datei" zulässig,
    und die Kante wäre nach dem dritten Template nicht mehr auffindbar.

    Kommentare zählen nicht als Code. Ein `const DECIMALS = {{ decimals }}`
    braucht seine Begründung neben sich, nicht in einer anderen Datei — bei
    chart_editor sind das acht Zeilen, die erklären, warum "auto" hier eine
    Übersteuerung ist und warum compare/compareMode nie gespeichert werden.
    Sie in die ausgelagerte Datei zu schieben hieße, sie von ihrem Gegenstand
    zu trennen.
    """
    for name, source in _converted().items():
        for block in INLINE.finditer(source):
            zeilen = [z.strip() for z in block.group("body").splitlines() if z.strip()]
            code = [z for z in zeilen if not z.startswith("//")]
            ohne_jinja = [z for z in code if "{{" not in z and "{%" not in z]
            assert not ohne_jinja, (
                f"{name}: {len(ohne_jinja)} Codezeile(n) ohne Jinja im Inline-Block, "
                f"z. B. {ohne_jinja[0][:60]!r} — gehört in die Datei"
            )


def test_the_page_script_is_loaded_after_its_data() -> None:
    """Reihenfolge, nicht Vorhandensein.

    Die Präambel legt die Konstanten an, die Datei benutzt sie auf oberster
    Ebene. Stünde der <script src> davor, wäre jede davon in der temporalen
    Todeszone — die Seite bliebe leer, und zwar erst zur Laufzeit.
    """
    for name, source in _converted().items():
        link = LINK.search(source)
        letzter_inline = max(
            (m.end() for m in INLINE.finditer(source) if m.end() < len(source)), default=-1
        )
        assert letzter_inline != -1, f"{name}: kein Inline-Block mehr — dann fehlen die Daten"
        assert link.start() > letzter_inline, (
            f"{name}: das Seitenskript wird VOR seiner Datenpräambel geladen"
        )


def test_the_bytes_really_left_the_template() -> None:
    """Sonst bliebe alles grün, wenn jemand den Code zusätzlich stehen lässt."""
    for name, source in _converted().items():
        datei = PAGE_JS / LINK.search(source).group("file")
        inline = sum(len(m.group("body")) for m in INLINE.finditer(source))
        assert datei.stat().st_size > 10 * inline, (
            f"{name}: {inline} B inline gegenüber {datei.stat().st_size} B in der Datei — "
            "sieht nach einer Kopie aus, nicht nach einer Verschiebung"
        )
