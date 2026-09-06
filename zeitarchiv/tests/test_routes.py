"""Seiten-Smoke-Tests über die echte FastAPI-App (siehe conftest.py).

Anders als die restliche Suite (die Module/Templates isoliert testet) geht
das hier end-to-end durch Route-Handler, Context-Builder und Template —
die einzige Ebene, die z. B. eine falsche Funktionssignatur in einem
Context-Builder wie collect_notices() beim Aufruf durch einen Route-Handler
zuverlässig fängt.
"""

from __future__ import annotations

import pytest

PAGES = ["/", "/entities", "/statistik", "/housekeeping", "/settings", "/import"]


@pytest.mark.parametrize("path", PAGES)
def test_page_loads(client, path) -> None:
    resp = client.get(path)
    assert resp.status_code == 200


# Die Formular-Ziele der Housekeeping-Seite. Anlass: beim Auslagern des
# Bereichs nach housekeeping_routes.py hat eine textuelle Ersetzung
# ("index" -> "deps.index") im URL-String zugeschlagen und aus
# /settings/storage-index/check ein /settings/storage-deps.index/check
# gemacht. Die ganze Suite blieb grün — die beiden Knöpfe hätten ins Leere
# gezeigt, weil kein Test diese POSTs je aufgerufen hat.
HOUSEKEEPING_FORM_TARGETS = [
    "/settings/archivierung",
    "/settings/rotation",
    "/settings/storage-index/check",
    "/settings/storage-index/repair",
    "/settings/purge",
    "/settings/retention-enforcement",
    "/settings/retention-enforcement/preview",
    "/settings/retention-enforcement/run",
]


@pytest.mark.parametrize("path", HOUSEKEEPING_FORM_TARGETS)
def test_housekeeping_form_targets_exist(client, path) -> None:
    """Nur auf Erreichbarkeit geprüft, nicht auf Wirkung: entscheidend ist,
    dass die Route überhaupt registriert ist. Ein 404 hieße, dass ein Knopf
    auf der Housekeeping-Seite ins Leere zeigt."""
    assert client.post(path).status_code != 404


def test_housekeeping_form_targets_match_the_templates(client) -> None:
    """Gegenprobe zur Liste oben: die Templates dürfen kein hx-post-Ziel
    tragen, das die Liste nicht kennt — sonst wächst die Seite um Knöpfe,
    die niemand prüft."""
    import re
    from pathlib import Path

    vorlagen = Path(__file__).resolve().parents[1] / "app/templates"
    ziele = set()
    for name in ("housekeeping.html", "_settings_storage_index_form.html",
                 "_settings_retention_form.html", "_settings_purge_form.html",
                 "_settings_rotation_form.html", "_settings_archivierung_form.html"):
        quelle = vorlagen / name
        if not quelle.exists():
            continue
        for treffer in re.finditer(r'hx-post="([^"{]+)"', quelle.read_text(encoding="utf-8")):
            ziele.add("/" + treffer.group(1).lstrip("./"))
    unbekannt = ziele - set(HOUSEKEEPING_FORM_TARGETS)
    assert not unbekannt, f"ungeprüfte Formular-Ziele: {sorted(unbekannt)}"


def test_tile_metrics_endpoint_resolves_the_dependencies_itself(client) -> None:
    """Der Endpunkt gibt den fertigen Anzeigezustand zurück, nicht nur "ok" —
    sonst müsste der Browser dieselben Regeln ein zweites Mal bauen: Hauptwert
    raus aus der Zeile, nicht anwendbare Kennzahlen raus, Zeitraum-Etikett."""
    from app.main import index

    index.get_or_create_entity("sensor.kacheltest", "sensor", "measurement", "°C")
    dashboard_id = index.get_default_dashboard_id()
    index.pin_entity_to_dashboard(dashboard_id, "sensor.kacheltest")
    try:
        antwort = client.post("/dashboard/entity-metrics", json={
            "dashboard_id": dashboard_id, "entity_id": "sensor.kacheltest",
            "range_key": "month", "continuous": True,
            "primary_metric": "avg", "stats_metrics": ["min", "avg", "max", "sum"],
        })
        assert antwort.status_code == 200
        daten = antwort.json()
        # Ø ist Hauptwert und fällt aus der Zeile; Σ ist bei einem Messwert
        # keine Aussage und fällt ebenfalls weg.
        assert daten["stats_metrics"] == ["min", "max"]
        assert daten["primary_label"] == "Ø"
        # Rollierend bei "Monat" sind genau 30 Tage, kein Kalendermonat.
        assert daten["range_label"] == "30 Tage"

        ungueltig = client.post("/dashboard/entity-metrics", json={
            "dashboard_id": dashboard_id, "entity_id": "sensor.kacheltest",
            "range_key": "decade",
        })
        assert ungueltig.status_code == 400
    finally:
        index.unpin_entity_from_dashboard(dashboard_id, "sensor.kacheltest")
