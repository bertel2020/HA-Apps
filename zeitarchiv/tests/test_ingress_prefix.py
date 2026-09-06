"""Assets müssen auch unter dem Ingress-Präfix im richtigen Verzeichnis landen.

Home Assistant reicht Zeitarchiv nicht unter `/` durch, sondern unter einem
Präfix wie `/api/hassio_ingress/<token>/`. Die App erfährt das über den Header
`X-Ingress-Path` (siehe `_app_root_context()` in main.py). Lokal — also genau
dort, wo entwickelt und getestet wird — fehlt dieser Header, und ein falsch
gebildeter Asset-Pfad fällt deshalb nicht auf. Er fällt beim Nutzer auf.

Vor dieser Datei hat **keine** Zeile der Suite jemals `X-Ingress-Path`
geschickt. Die Templates schreiben denselben Präfix aber auf vier Arten
(bloß relativ, `{{ base }}` mit `".."`, `{{ base }}` mit `"../.."`,
`{{ app_root }}`) — siehe ZG-03 in CODE_ANALYSE.md. Welche Schreibweise
richtig ist, hängt an der URL-Tiefe der jeweiligen Seite.

Deshalb prüft diese Datei nicht die **Schreibweise**, sondern die **Wirkung**:
Was der Browser aus der Angabe macht, muss unter dem Präfix liegen. Damit
bleibt der Test gültig, während ZG-04 die Schreibweise auf `{{ app_root }}`
vereinheitlicht — er beschreibt vorher wie nachher dieselbe Zusage.
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
    "/uebersicht",                    # Tiefe 1, relativ
    "/entities",                      # Tiefe 1, relativ
    "/settings",                      # Tiefe 1, relativ
    "/backup",                        # Tiefe 1, relativ
    "/charts/new",                    # Tiefe 2, {{ base }} = ".."
    "/tables/new",                    # Tiefe 2, {{ base }} = ".."
    "/dashboards/new",                # Tiefe 2, {{ base }} = ".."
    "/statistik/index",               # Tiefe 2, {{ app_root }}
    f"/entities/{ENTITY}",            # Tiefe 2, {{ base }} = ".."
    f"/entities/{ENTITY}/config",     # Tiefe 3, {{ base }} = "../.."
    f"/entities/{ENTITY}/cleanup",    # Tiefe 3, {{ base }} = "../.."
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
