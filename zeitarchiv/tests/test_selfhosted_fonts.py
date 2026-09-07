"""ZG-14: Die Schriften liegen im Add-on, nicht bei Google.

Bis September 2026 lud jede Seite IBM Plex Sans und IBM Plex Mono über einen
`<link>` von `fonts.googleapis.com`. In Netzen ohne Internetzugang oder mit
DNS-Filter — bei Home Assistant nicht selten — wartete dort jeder Seitenaufbau
erst auf den Timeout, bevor die Ersatzschrift griff; nebenbei erfuhr Google bei
jedem Aufruf die IP des Nutzers, und die CSP musste zwei fremde Hosts offen
halten.

Diese Datei hält die Umkehrung fest. Sie prüft nicht, dass die Schriften
„schön" aussehen, sondern die vier Zusagen, an denen der Umstieg still
zerbrechen könnte: die Dateien sind da, sie werden gebraucht, niemand fragt
mehr nach draußen, und die Pfade lösen auch unter dem Ingress-Präfix auf.
"""

from __future__ import annotations

import re

from _paths import APP, APP_CSS, APP_JS, STATIC, TEMPLATES

# Bewusst hier statt in _paths.py: die Testpfade dort sind über drei Bäume
# byte-identisch zu halten (auch über den der Integration, die kein static/
# besitzt) — eine Konstante, die nur diese eine Datei braucht, gehört nicht
# in diese Synchronisationspflicht.
FONTS = STATIC / "fonts"

DECLARED = re.findall(r"url\(\.\./fonts/([^)]+)\)", APP_CSS.read_text(encoding="utf-8"))


def _ohne_kommentare(quelle: str) -> str:
    """Ein Kommentar lädt nichts. Die Prüfungen unten fragen, was eine Datei
    TUT — und ausgerechnet der @font-face-Kommentar in app.css erklärt
    ausführlich, wovon die App losgekommen ist, samt Hostnamen. Ohne diesen
    Schritt zählte diese Erklärung als Verstoß."""
    for anfang, ende in (("/*", "*/"), ("{#", "#}"), ("<!--", "-->")):
        while anfang in quelle:
            kopf, _, rest = quelle.partition(anfang)
            _, _, schwanz = rest.partition(ende)
            quelle = kopf + schwanz
    return quelle


def _stylesheets() -> dict[str, str]:
    return {
        str(path.relative_to(APP)): _ohne_kommentare(path.read_text(encoding="utf-8"))
        for path in (STATIC / "css").glob("**/*.css")
    }


def test_every_declared_font_file_exists() -> None:
    """Ein `url()` ins Leere fällt im Browser nicht auf: die Schrift wird
    einfach durch die Ersatzschrift ersetzt, und zwar erst nach einem 404. Ein
    Tippfehler im Dateinamen wäre damit unsichtbar."""
    assert DECLARED, "app.css deklariert überhaupt keine Schriftdatei"
    for name in DECLARED:
        assert (FONTS / name).is_file(), f"app.css verweist auf fehlende Datei {name}"


def test_no_font_file_is_shipped_without_being_used() -> None:
    """Die Gegenrichtung. Eine Datei, die niemand referenziert, wandert
    trotzdem ins Image und wird bei jedem Schriften-Update mitgeschleppt."""
    vorhanden = {path.name for path in FONTS.glob("*.woff2")}
    assert vorhanden, "static/fonts/ ist leer"
    assert vorhanden == set(DECLARED), (
        f"nicht referenziert: {sorted(vorhanden - set(DECLARED))}; "
        f"referenziert, aber nicht vorhanden: {sorted(set(DECLARED) - vorhanden)}"
    )


def test_the_font_paths_survive_the_ingress_prefix() -> None:
    """Der Kern des Umstiegs, und die Stelle, an der er am ehesten
    „repariert" würde: die Pfade sind relativ zum Stylesheet, nicht zur Seite.

    Ein Browser wertet `url()` gegen die URL der CSS-Datei aus. Die trägt den
    Ingress-Präfix bereits (`{{ app_root }}/static/css/app.css`), also trägt
    ihn auch die Schrift. Ein absolutes `/static/fonts/…` sähe daneben
    aufgeräumter aus und wäre unter Ingress ein 404 — dort liegt die App nicht
    an der Wurzel. `{{ app_root }}` wiederum kann hier nicht helfen: eine
    CSS-Datei läuft nicht durch Jinja."""
    for name, css in _stylesheets().items():
        for url in re.findall(r"url\(([^)]*fonts/[^)]+)\)", css):
            assert url.startswith("../fonts/"), (
                f"{name}: {url!r} ist nicht relativ zum Stylesheet"
            )
        assert "{{ app_root }}" not in css, f"{name}: CSS wird nicht durch Jinja gerendert"


def test_no_stylesheet_template_or_script_asks_an_external_host() -> None:
    """Die eigentliche Zusage von ZG-14: die Oberfläche baut sich vollständig
    ohne Internetzugang auf."""
    quellen = _stylesheets()
    quellen.update(
        {
            f"templates/{p.name}": _ohne_kommentare(p.read_text(encoding="utf-8"))
            for p in TEMPLATES.glob("*.html")
        }
    )
    quellen.update(
        {
            f"js/{p.name}": _ohne_kommentare(p.read_text(encoding="utf-8"))
            for p in APP_JS.glob("**/*.js")
        }
    )
    for name, quelle in quellen.items():
        for host in ("fonts.googleapis.com", "fonts.gstatic.com"):
            assert host not in quelle, f"{name} lädt Schriften von {host}"

    for name, css in _stylesheets().items():
        assert not re.search(r"url\(\s*['\"]?https?:", css), f"{name} lädt eine externe Ressource"


def test_the_content_security_policy_no_longer_opens_googles_hosts() -> None:
    """Die CSP war der Preis der externen Schriften. Bleibt sie offen, während
    niemand mehr hinausgeht, ist die Erlaubnis eine Zusage ohne Deckung — genau
    die Sorte Grenzwert, die ZG-26 nebenan zum Befund gemacht hat."""
    quelle = (APP / "main.py").read_text(encoding="utf-8")
    assert '"font-src \'self\'; "' in quelle
    assert '"style-src \'self\' \'unsafe-inline\'; "' in quelle
    for host in ("fonts.googleapis.com", "fonts.gstatic.com"):
        assert f"https://{host}" not in quelle, f"CSP öffnet weiterhin {host}"


def test_the_shipped_files_really_are_woff2() -> None:
    """Ein fehlgeschlagener Download hinterlässt keine leere Datei, sondern
    eine HTML-Fehlerseite mit passendem Dateinamen. Die Signatur ist die
    einzige Prüfung, die das bemerkt."""
    for path in sorted(FONTS.glob("*.woff2")):
        kopf = path.read_bytes()[:4]
        assert kopf == b"wOF2", f"{path.name} ist kein WOFF2 (beginnt mit {kopf!r})"
