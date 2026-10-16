"""Housekeeping → Datenqualität auf Routenebene: der Abschnitt, die
aufgeklappte Zeile und die Startwerte der Bereinigungsseite aus dem Link.

Die Zusammenführung der Zahlen selbst deckt test_data_quality.py ab; hier geht
es um das, was nur die Seiten beisteuern.
"""

from __future__ import annotations

import re


def _entity(index, entity_id: str) -> None:
    index.get_or_create_entity(entity_id, "sensor", "measurement", "°C")


def test_datenqualitaet_seite_zeigt_die_matrix_mit_link_auf_die_bereinigung(client) -> None:
    from app.main import index

    from app.storage.cleanup import OUTLIER_RULE_VERSION

    _entity(index, "sensor.pytest_dq_matrix")
    index.set_first_ts_and_add_rows("sensor.pytest_dq_matrix", 1_700_000_000.0, 0)
    index.set_cleanup_alltime_stats(
        "sensor.pytest_dq_matrix", {"all": 100, "duplicates": 12, "counter_decreases": 0},
        "off", OUTLIER_RULE_VERSION,
    )
    html = client.get("/housekeeping/datenqualitaet").text
    assert 'href="/entities/sensor.pytest_dq_matrix/cleanup?filter=duplicates&amp;range=all"' in html
    # Die Zeile lädt ihre Einzelbefunde über einen absoluten Pfad: die Seite
    # liegt unter /housekeeping/, ein relativer Link würde dort ins Leere laufen.
    assert 'hx-get="/entities/sensor.pytest_dq_matrix/data-quality"' in html
    # Ohne htmx bliebe die aufgeklappte Zeile bei „Lädt …“ stehen.
    assert "vendor/htmx" in html


def test_housekeeping_verweist_auf_die_datenqualitaet_statt_sie_einzubetten(client) -> None:
    html = client.get("/housekeeping").text
    assert 'id="datenqualitaet"' in html
    assert 'href="/housekeeping/datenqualitaet"' in html
    assert "dq-table" not in html


def test_aufgeklappte_zeile_ohne_rohdaten_ist_leer_aber_erreichbar(client) -> None:
    from app.main import index

    _entity(index, "sensor.pytest_dq_detail")
    antwort = client.get("/entities/sensor.pytest_dq_detail/data-quality")
    assert antwort.status_code == 200
    assert "Keine Einzelbefunde" in antwort.text


def test_aufgeklappte_zeile_unbekannte_entitaet_ist_404(client) -> None:
    assert client.get("/entities/sensor.pytest_dq_gibt_es_nicht/data-quality").status_code == 404


def _start(html: str) -> tuple[str, str]:
    filter_ = re.search(r'id="rows-filter-input" value="([^"]*)"', html).group(1)
    range_ = re.search(r'id="range-field" value="([^"]*)"', html).group(1)
    return filter_, range_


def test_bereinigungsseite_uebernimmt_filter_und_zeitraum_aus_dem_link(client) -> None:
    from app.main import index

    _entity(index, "sensor.pytest_dq_start")
    html = client.get("/entities/sensor.pytest_dq_start/cleanup?filter=duplicates&range=all").text
    assert _start(html) == ("duplicates", "all")
    assert re.search(r'class="active" data-range="all"', html)
    assert not re.search(r'class="active" data-range="day"', html)


def test_bereinigungsseite_ohne_parameter_startet_wie_bisher(client) -> None:
    from app.main import index

    _entity(index, "sensor.pytest_dq_default")
    html = client.get("/entities/sensor.pytest_dq_default/cleanup").text
    assert _start(html) == ("all", "day")
    assert re.search(r'class="active" data-range="day"', html)


def test_bereinigungsseite_verwirft_ungueltige_startwerte(client) -> None:
    from app.main import index

    _entity(index, "sensor.pytest_dq_invalid")
    html = client.get("/entities/sensor.pytest_dq_invalid/cleanup?filter=blah&range=blub").text
    assert _start(html) == ("all", "day")


def test_startzeitraum_der_bereinigung_von_der_datenqualitaet(client) -> None:
    from datetime import datetime, timedelta

    from app.main import TZ, index
    from app.storage.cleanup import OUTLIER_RULE_VERSION

    _entity(index, "sensor.pytest_dq_heat")
    index.set_first_ts_and_add_rows("sensor.pytest_dq_heat", 1_700_000_000.0, 0)
    gestern = (datetime.now(TZ) - timedelta(days=1)).strftime("%Y-%m-%d")
    index.set_cleanup_alltime_stats(
        "sensor.pytest_dq_heat", {"all": 100, "duplicates": 2}, "off", OUTLIER_RULE_VERSION,
        daily={gestern: {"duplicates": 2}}, gap_threshold="15",
    )
    # Die Heatmap ist vorerst nicht erreichbar (ROADMAP.md): auch mit view=heatmap kommt die Tabelle.
    html = client.get("/housekeeping/datenqualitaet?view=heatmap").text
    assert "dq-heat" not in html and "view=heatmap" not in html
    # Der Link öffnet die Bereinigung auf diesem Tag: einen Tag zurück.
    seite = client.get(f"/entities/sensor.pytest_dq_heat/cleanup?filter=duplicates&range=day&date={gestern}").text
    assert re.search(r'id="offset-field" value="-1"', seite)
    assert re.search(r'id="offset-field" value="0"', client.get("/entities/sensor.pytest_dq_heat/cleanup").text)


def _mit_duplikaten(index, entity_id: str) -> None:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from app.main import DATA_DIR, TZ
    from app.storage import hotbuffer

    _entity(index, entity_id)
    ts = datetime.now(ZoneInfo(str(TZ))).replace(hour=3, minute=0, second=0, microsecond=0).timestamp() - 86_400
    for _ in range(3):
        hotbuffer.append(DATA_DIR, entity_id, ts, 5.0, TZ)
        index.record_write(entity_id, ts)


def test_sammelaktion_vorschau_loeschen_und_rueckgaengig(client) -> None:
    from app.main import index

    _mit_duplikaten(index, "sensor.pytest_dq_bulk")
    daten = {"entity_id": ["sensor.pytest_dq_bulk", "sensor.pytest_dq_gibt_es_nicht"]}

    vorschau = client.post("/housekeeping/datenqualitaet/duplicates/preview", data=daten).text
    assert "Bestätigung" in vorschau
    assert 'name="entity_id" value="sensor.pytest_dq_bulk"' in vorschau
    assert "pytest_dq_gibt_es_nicht" not in vorschau
    assert "/housekeeping/datenqualitaet/duplicates/delete" in vorschau

    geloescht = client.post("/housekeeping/datenqualitaet/duplicates/delete", data=daten).text
    assert "entfernt" in geloescht
    assert "/housekeeping/datenqualitaet/duplicates/undo" in geloescht

    nochmal = client.post("/housekeeping/datenqualitaet/duplicates/preview", data=daten).text
    assert "keine Duplikate" in nochmal

    zurueck = client.post("/housekeeping/datenqualitaet/duplicates/undo", data=daten).text
    assert "wiederhergestellt" in zurueck
    assert "Bestätigung" in client.post("/housekeeping/datenqualitaet/duplicates/preview", data=daten).text


def test_tabelle_bietet_auswahl_nur_bei_duplikaten(client) -> None:
    from app.main import index

    from app.storage.cleanup import OUTLIER_RULE_VERSION

    for name, dup in (("sensor.pytest_dq_mit", 4), ("sensor.pytest_dq_ohne", 0)):
        _entity(index, name)
        index.set_first_ts_and_add_rows(name, 1_700_000_000.0, 0)
        index.set_cleanup_alltime_stats(
            name, {"all": 100, "duplicates": dup, "outliers": 1, "gaps": 0, "counter_decreases": 0},
            "off", OUTLIER_RULE_VERSION, daily={}, gap_threshold="15",
        )
    html = client.get("/housekeeping/datenqualitaet?page_size=500").text
    assert 'name="entity_id" value="sensor.pytest_dq_mit"' in html
    assert 'name="entity_id" value="sensor.pytest_dq_ohne"' not in html
    assert 'id="dq-bulk-panel"' in html


def test_zeitraum_auswahl_standard_gesamt_und_ohne_sammelaktion_im_fenster(client) -> None:
    from app.main import index

    _mit_duplikaten(index, "sensor.pytest_dq_zeitraum")
    html = client.get("/housekeeping/datenqualitaet").text
    assert re.search(r'<option value="all"\s+selected>', html)
    assert 'class="dq-check" name="entity_id"' in html
    fenster = client.get("/housekeeping/datenqualitaet?period=7d").text
    assert re.search(r'<option value="7d"\s+selected>', fenster)
    assert 'name="entity_id"' not in fenster  # Entfernen gilt für die ganze Historie


def test_alle_auswaehlen_liefert_gueltiges_attribut(client) -> None:
    from app.main import index

    _mit_duplikaten(index, "sensor.pytest_dq_alle")
    html = client.get("/housekeeping/datenqualitaet").text
    # tojson maskiert keine doppelten Anführungszeichen: das Attribut muss in einfachen stehen,
    # sonst bricht die Entitätsliste es auf und die Kopf-Checkbox tut nichts.
    assert "@change='selected = $event.target.checked ? [" in html


def test_ansicht_behaelt_suche_filter_und_sortierung_in_den_links(client) -> None:
    from app.main import index

    from app.storage.cleanup import OUTLIER_RULE_VERSION

    for n in range(12):
        eid = f"sensor.pytest_dq_sort_{n:02d}"
        _entity(index, eid)
        index.set_first_ts_and_add_rows(eid, 1_700_000_000.0, 0)
        index.set_cleanup_alltime_stats(
            eid, {"all": 100, "duplicates": 2}, "off", OUTLIER_RULE_VERSION, daily={}, gap_threshold="15",
        )
    html = client.get("/housekeeping/datenqualitaet?q=pytest_dq_sort&kind=duplicates&sort=name&dir=asc&page_size=10").text
    assert 'value="pytest_dq_sort"' in html
    assert "q=pytest_dq_sort" in html and "kind=duplicates" in html and "sort=name" in html and "dir=asc" in html
    assert "page=2" in html  # Pager
    # Standardwerte tauchen nicht in den Links auf
    standard = client.get("/housekeeping/datenqualitaet").text
    assert "sort=total" not in standard and "kind=all" not in standard


def test_detail_der_zaehlerrueckgaenge_verweist_auf_die_buendelung_in_housekeeping(client, monkeypatch) -> None:
    from app import data_quality
    from app.main import index

    _entity(index, "sensor.pytest_dq_zaehler")
    gruppe = {
        "key": "counter_decreases", "severity": "crit", "total": 3, "total_label": "3", "shown": 1,
        "href": "entities/sensor.pytest_dq_zaehler/cleanup?filter=counter_decreases&range=all",
        "rows": [{"ts_label": "01.10.", "value_label": "5", "reasons": ["Rückgang"]}],
    }
    monkeypatch.setattr(data_quality, "findings_for_entity", lambda *a, **k: [gruppe, {**gruppe, "key": "gaps"}])
    html = client.get("/entities/sensor.pytest_dq_zaehler/data-quality").text
    assert html.count("/housekeeping#zaehlerrueckgaenge") == 1  # nur bei Zählerrückgängen, nicht bei Lücken
