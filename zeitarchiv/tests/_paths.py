"""Ein Ort für die Pfade der App — funktioniert in beiden Baum-Layouts.

Zeitarchiv wird in zwei Verzeichnisbäumen gepflegt: im Dev-Baum liegt `tests/`
eine Ebene ÜBER `addon/`, im Git-Repo liegt es NEBEN `app/`. Bis hierher trug
jede Testdatei diesen Unterschied selbst — 48-mal `sys.path.insert(...)` und
über hundert literale `"addon"`-Pfadsegmente, die beim Sync mechanisch
umgeschrieben werden mussten.

Das hatte einen Preis, der nichts mit Schreibarbeit zu tun hat: `diff -rq addon
HA-Apps/zeitarchiv` meldete dadurch IMMER alle Testdateien als verschieden und
war als Sync-Kontrolle wertlos. Eine echte inhaltliche Abweichung ginge in
diesem Rauschen unter — und Abweichungen gibt es: Testdateien, die nur in einem
der beiden Bäume liegen, fallen dort nicht auf.

Hier steht die Fallunterscheidung einmal. Danach sind beide Testbäume
byte-identisch und der Sync-Diff sagt wieder die Wahrheit.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

#: Verzeichnis, das `app/` enthält — im Dev-Baum `addon/`, im Repo der Wurzel.
ADDON = ROOT / "addon" if (ROOT / "addon" / "app").is_dir() else ROOT

APP = ADDON / "app"
DOCS = ADDON / "docs"
TEMPLATES = APP / "templates"
STATIC = APP / "static"
APP_CSS = STATIC / "css" / "app.css"
#: Seitenlokales CSS und JS, seit ZG-04 Schritt 3 aus den Templates gehoben.
PAGE_CSS = STATIC / "css" / "pages"
PAGE_JS = STATIC / "js" / "pages"
APP_JS = STATIC / "js"

# `from app.main import ...` muss in beiden Layouts funktionieren. Steht hier
# statt in conftest.py, damit auch ein einzeln aufgerufenes Testmodul es hat.
if str(ADDON) not in sys.path:
    sys.path.insert(0, str(ADDON))


def german(text: str) -> str:
    """Template-Quelltext mit den `_()`-Aufrufen wieder als deutscher Klartext.

    Seit der Mehrsprachigkeit (Roadmap 1.21) stehen die sichtbaren Texte der Templates
    in `_()`-Aufrufen. Quelltext-Tests, die einen deutschen Text, ein Attribut wie
    `placeholder="Suchen …"` oder einen Ausdruck wie `result.rows_updated_label` prüfen,
    meinen den Text — nicht die Art, wie er eingebunden ist. Deshalb wird hier jeder Aufruf
    zurückgebaut: ein alleinstehendes `{{ _("Text {n}", n=(ausdruck)) }}` wird zu
    `Text {{ ausdruck }}`, ein `_("Text")` mitten in einem Ausdruck zu `"Text"`.
    """
    out: list[str] = []
    pos = 0
    start = re.compile(r"""(?P<open>\{\{-?\s*)?\b_\(\s*(?P<q>['"])""")
    while True:
        m = start.search(text, pos)
        if m is None:
            out.append(text[pos:])
            break
        out.append(text[pos:m.start()])
        key, k = _read_literal(text, m.end() - 1)
        args, k = _read_arguments(text, k)
        if m.group("open"):
            tail = re.compile(r"\s*-?\}\}").match(text, k)
            if tail is None:   # `_()` innerhalb eines größeren Ausdrucks
                out.append(m.group("open") + _literal(key, m.group("q")))
                pos = k
                continue
            k = tail.end()
            out.append(_fill(key, args))
        else:
            out.append(_literal(key, m.group("q")))
        pos = k
    return "".join(out)


def _literal(key: str, quote: str) -> str:
    return quote + key + quote


def _read_literal(text: str, quote_at: int) -> tuple[str, int]:
    quote = text[quote_at]
    k = quote_at + 1
    chars: list[str] = []
    while text[k] != quote:
        if text[k] == "\\":
            k += 1
        chars.append(text[k])
        k += 1
    return "".join(chars), k + 1


def _read_arguments(text: str, k: int) -> tuple[dict[str, str], int]:
    """Liest `, name=ausdruck, …)` nach dem Textliteral; gibt die Argumente und die Position
    hinter der schließenden Klammer zurück."""
    args: dict[str, str] = {}
    while text[k] != ")":
        if text[k] == ",":
            k += 1
            continue
        if text[k].isspace():
            k += 1
            continue
        eq = text.index("=", k)
        name = text[k:eq].strip()
        k = eq + 1
        depth = 0
        begin = k
        while depth or text[k] not in ",)":
            ch = text[k]
            if ch in "([{":
                depth += 1
            elif ch in ")]}":
                depth -= 1
            elif ch in "'\"":
                _, k = _read_literal(text, k)
                continue
            k += 1
        args[name] = text[begin:k].strip()
    return args, k + 1


def _fill(key: str, args: dict[str, str]) -> str:
    def expression(raw: str) -> str:
        raw = raw.strip()
        if raw.startswith("(") and raw.endswith(")"):
            depth = 0
            for idx, ch in enumerate(raw):
                depth += ch == "("
                depth -= ch == ")"
                if depth == 0 and idx < len(raw) - 1:
                    break
            else:
                raw = raw[1:-1].strip()
        return "{{ " + german(raw) + " }}"

    return re.sub(r"\{(\w+)\}", lambda m: expression(args[m.group(1)]) if m.group(1) in args else m.group(0), key)


def template_text(name: str) -> str:
    """Ein Template, deutsch entpackt (siehe `german`)."""
    return german((TEMPLATES / name).read_text(encoding="utf-8"))


def page_text(name: str) -> str:
    """Template samt dem CSS und JavaScript, das diese Seite mitbringt.

    Seit ZG-04 Schritt 3 stehen die seitenlokalen Regeln und Skripte nicht mehr
    als <style>/<script>-Block im Template, sondern als
    static/css/pages/<seite>.css und static/js/pages/<seite>.js daneben. „Was
    diese Seite mitbringt" ist damit auf bis zu drei Dateien verteilt — Tests,
    die eine solche Regel oder Codezeile prüfen, meinen aber weiterhin alles
    zusammen. Deshalb hier einmal zusammengesetzt, statt in jedem betroffenen
    Test mehrere Dateien von Hand zu lesen.
    """
    text = template_text(name)
    # Ohne führendes "/static/", damit beide Schreibweisen passen: die alte
    # ("{{ app_root }}/static/css/pages/x.css?v={{ css_v }}") und die seit
    # ZG-05 gültige ("{{ asset('css/pages/x.css') }}").
    for muster, ordner in (
        (r"css/pages/([a-z_]+\.css)", PAGE_CSS),
        (r"js/pages/([a-z_]+\.js)", PAGE_JS),
    ):
        match = re.search(muster, text)
        if match is not None:
            text += "\n" + (ordner / match.group(1)).read_text(encoding="utf-8")
    return text
