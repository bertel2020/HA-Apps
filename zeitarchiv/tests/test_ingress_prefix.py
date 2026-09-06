"""Assets müssen auch unter dem Ingress-Präfix im richtigen Verzeichnis landen.

Home Assistant reicht Zeitarchiv nicht unter `/` durch, sondern unter einem
Präfix wie `/api/hassio_ingress/<token>/`. Die App erfährt das über den Header
`X-Ingress-Path` (siehe `_app_root_context()` in main.py). Lokal — also genau
dort, wo entwickelt und getestet wird — fehlt dieser Header, und ein falsch
gebildeter Asset-Pfad fällt deshalb nicht auf. Er fällt beim Nutzer auf.

Vor dieser Datei hat **keine** Zeile der Suite jemals `X-Ingress-Path`
geschickt. Die Templates schreiben denselben Präfix aber auf vier Arten
(bloß relativ, ein `base` mit `".."`, eines mit `"../.."`, und `app_root`) —
siehe ZG-03 in CODE_ANALYSE.md. Welche Schreibweise richtig war, hing an der
URL-Tiefe der jeweiligen Seite.

Deshalb prüft diese Datei nicht die **Schreibweise**, sondern die **Wirkung**:
Was der Browser aus der Angabe macht, muss unter dem Präfix liegen. Genau das
hat sie über die Vereinheitlichung auf `app_root` hinweg gültig gehalten (ZG-04
Schritt 2) — sie beschreibt vorher wie nachher dieselbe Zusage. Die
Tiefenabdeckung unten bleibt aus demselben Grund wertvoll: dass `app_root`
tiefenunabhängig ist, wird hier belegt statt behauptet.
"""

from __future__ import annotations

import re
from urllib.parse import urljoin

import pytest

INGRESS = "/api/hassio_ingress/aBc123XyZ"

ENTITY = "sensor.zg04_ingress_probe"

# Die URL-Tiefe ist das Einzige, worauf es hier ankommt — eine relative
# Angabe, die auf Tiefe 1 stimmt, zeigt auf Tiefe 3 zwei Ebenen daneben.
# Deshalb je Tiefe mindestens eine Seite, und test_the_page_list_still_covers
# _every_url_depth hält das fest.
PAGES = [
    "/uebersicht",                    # Tiefe 1
    "/entities",                      # Tiefe 1
    "/settings",                      # Tiefe 1
    "/backup",                        # Tiefe 1
    "/charts/new",                    # Tiefe 2
    "/tables/new",                    # Tiefe 2
    "/dashboards/new",                # Tiefe 2
    "/statistik/index",               # Tiefe 2, {{ app_root }}
    f"/entities/{ENTITY}",            # Tiefe 2
    f"/entities/{ENTITY}/config",     # Tiefe 3
    f"/entities/{ENTITY}/cleanup",    # Tiefe 3
]

# href/src, die irgendwo static/ enthalten — egal in welcher Schreibweise.
ASSET = re.compile(r'(?:src|href)="([^"]*static/[^"]*)"')


@pytest.fixture(scope="module", autouse=True)
def _entity(client):
    """Die Tiefe-3-Seiten brauchen eine Entität, sonst antworten sie mit 404."""
    from app.main import index

    index.get_or_create_entity(ENTITY, "sensor", "measurement", "kWh", "ZG-04 Ingress-Probe")
    return ENTITY


def _assets(client, path: str, headers: dict | None = None) -> list[str]:
    resp = client.get(path, headers=headers or {})
    assert resp.status_code == 200, f"{path} antwortet mit {resp.status_code}"
    refs = ASSET.findall(resp.text)
    assert refs, f"{path} bindet gar kein static/-Asset ein — Test greift ins Leere"
    return refs


@pytest.mark.parametrize("path", PAGES)
def test_assets_resolve_under_the_ingress_prefix(client, path) -> None:
    """Der Browser sieht die Seite unter <präfix><pfad> und löst jede relative
    Angabe dagegen auf. Genau diese Auflösung wird hier nachgebaut."""
    erwartet = f"{INGRESS}/static/"
    for ref in _assets(client, path, {"X-Ingress-Path": INGRESS}):
        aufgeloest = urljoin(f"{INGRESS}{path}", ref)
        assert aufgeloest.startswith(erwartet), (
            f"{path}: {ref!r} landet unter Ingress bei {aufgeloest!r} statt unter {erwartet!r}"
        )


@pytest.mark.parametrize("path", PAGES)
def test_assets_still_resolve_without_the_ingress_header(client, path) -> None:
    """Die Gegenrichtung: ohne Header muss alles weiter direkt unter /static/
    landen. Ein Umbau, der nur den Ingress-Fall richtig macht, wäre kein
    Fortschritt, sondern ein Tausch des einen Fehlers gegen den anderen."""
    for ref in _assets(client, path):
        aufgeloest = urljoin(path, ref)
        assert aufgeloest.startswith("/static/"), (
            f"{path}: {ref!r} landet lokal bei {aufgeloest!r} statt unter '/static/'"
        )


def test_the_page_list_still_covers_every_url_depth() -> None:
    """Ohne diese Zusage könnte die Liste oben auf lauter Tiefe-1-Seiten
    zusammenschrumpfen und bliebe grün, während genau der Fall wegfiele, den
    sie prüfen soll."""
    tiefen = {p.strip("/").count("/") + 1 for p in PAGES}
    assert {1, 2, 3} <= tiefen, f"Tiefen {sorted(tiefen)} — 1, 2 und 3 müssen dabei sein"


def test_the_ingress_prefix_is_actually_reaching_the_templates(client) -> None:
    """Schutz gegen einen stumpfen Test: würde der Header ignoriert, wären
    beide Prüfungen oben trotzdem grün, solange alle Seiten relativ schreiben.
    Mindestens eine Seite muss den Präfix wörtlich ausgeben."""
    resp = client.get("/statistik/index", headers={"X-Ingress-Path": INGRESS})
    assert INGRESS in resp.text, "app_root kommt nicht in der Ausgabe an"


# --- Nach ZG-04 Schritt 2: die alten Schreibweisen dürfen nicht zurückkommen ---
#
# Die Prüfungen oben messen die WIRKUNG und bleiben deshalb auch dann grün,
# wenn jemand eine neue Seite wieder relativ verlinkt und sie zufällig auf
# Tiefe 1 liegt. Die folgenden zwei halten die SCHREIBWEISE fest — erst
# zusammen decken sie den Fall ab, dass eine neue Seite auf einer neuen Tiefe
# entsteht, für die noch niemand einen Testfall angelegt hat.

def test_no_template_writes_a_url_prefix_of_its_own_any_more() -> None:
    """`base` gab es in drei Ausprägungen: als `{{ base }}` im Markup, als
    Jinja-Variable in `{% set %}`, und — am weitesten getragen — als
    Query-Parameter, den die Kachel-Endpunkte entgegennahmen, damit das
    zurückgelieferte Fragment wusste, auf welcher Seitentiefe es landet.
    `app_root` kommt stattdessen aus dem Request-Header und ist überall gleich.
    """
    from _paths import TEMPLATES

    fehler = []
    for pfad in sorted(TEMPLATES.glob("*.html")):
        quelle = pfad.read_text(encoding="utf-8")
        if pfad.name == "base.html":  # heißt so, meint aber das Rahmen-Template
            continue
        for muster, was in (
            ("{{ base }}", "{{ base }}"),
            ("{{ base |", "{{ base | … }}"),
            ("{% set base", "{% set base %}"),
            ("base={{", "base= als Query-Parameter"),
            ('data-base="', 'data-base (heißt jetzt data-app-root)'),
            ('src="static/', 'relativer Skript-/Asset-Pfad'),
            ('href="static/', 'relativer Asset-Pfad'),
            ('"../static/', 'relativer Asset-Pfad'),
        ):
            if muster in quelle:
                fehler.append(f"{pfad.name}: {was}")
    assert not fehler, "alte Präfix-Schreibweise wieder aufgetaucht: " + "; ".join(fehler)


def test_no_route_hands_a_url_prefix_to_a_template_any_more() -> None:
    """Die Gegenseite: solange irgendeine Route noch ein `base` in den Kontext
    legt, kann ein Template es auch wieder benutzen. 15 Stellen taten das."""
    from _paths import APP

    fehler = []
    for pfad in sorted(APP.rglob("*.py")):
        for nr, zeile in enumerate(pfad.read_text(encoding="utf-8").split("\n"), 1):
            if re.search(r'"base":\s', zeile) or re.search(r'\bbase: str = "', zeile):
                fehler.append(f"{pfad.name}:{nr}")
    assert not fehler, "Route reicht wieder einen URL-Präfix durch: " + ", ".join(fehler)
