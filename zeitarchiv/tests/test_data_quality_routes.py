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
    # Feste Stelle im laufenden Monat statt "gestern": am Monatsersten läge gestern im Archiv.
    ts = datetime.now(ZoneInfo(str(TZ))).replace(day=1, hour=0, minute=1, second=0, microsecond=0).timestamp()
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

    # Die Charge des Laufs steckt als Kennung im Formular des Rückgängig-Knopfs.
    import re

    batch_at = re.search(r'name="batch_at" value="([^"]+)"', geloescht).group(1)
    fremd = {"entity_id": ["sensor.pytest_dq_bulk"], "batch_at": ["1.0"]}
    assert "0</strong> Zeile(n) wiederhergestellt" in client.post(
        "/housekeeping/datenqualitaet/duplicates/undo", data=fremd).text
    zurueck = client.post(
        "/housekeeping/datenqualitaet/duplicates/undo",
        data={"entity_id": ["sensor.pytest_dq_bulk"], "batch_at": [batch_at]},
    ).text
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


def test_seite_zeigt_gesamtstatus_und_stand_je_zeile(client) -> None:
    from app.main import index

    from app.storage.cleanup import OUTLIER_RULE_VERSION

    _entity(index, "sensor.pytest_dq_stand")
    index.set_first_ts_and_add_rows("sensor.pytest_dq_stand", 1_700_000_000.0, 0)
    index.set_cleanup_alltime_stats(
        "sensor.pytest_dq_stand", {"all": 100, "duplicates": 3, "counter_decreases": 0},
        "off", OUTLIER_RULE_VERSION,
    )
    html = client.get("/housekeeping/datenqualitaet").text
    assert 'class="dq-status' in html
    assert "Entitäten gemessen" in html
    assert 'class="dq-stand"' in html
    assert "dq-age" in html
    # Die Sortierung nach Stand beginnt mit der ältesten Messung.
    assert "sort=measured&amp;dir=asc" in html
    antwort = client.get("/housekeeping/datenqualitaet?sort=measured&dir=asc")
    assert antwort.status_code == 200


def test_duplikat_bestaetigung_doppelt_abgeschickt_markiert_nur_einmal(client) -> None:
    """Die Bestätigung bestimmt die Duplikate unter der Sperre neu: ein zweites
    Absenden (oder eine veraltete Vorschau) markiert nichts mehr, sonst würde auch das
    letzte verbliebene Vorkommen ausgeblendet. Rückgängig nimmt genau die Charge zurück,
    die die Vorschau zeigte, auch wenn danach eine andere entstand."""
    from app.main import index

    entity_id = "sensor.pytest_dq_doppelt"
    _mit_duplikaten(index, entity_id)  # dreimal derselbe Zeitstempel
    form = {"source": "duplicates", "filter": "all", "range": "month", "offset": "0", "page_size": "20"}

    client.post(f"/entities/{entity_id}/rows/delete", data=form)
    assert sum(index.get_deleted_counts_for_entity(entity_id).values()) == 2
    client.post(f"/entities/{entity_id}/rows/delete", data=form)  # zweites Absenden
    assert sum(index.get_deleted_counts_for_entity(entity_id).values()) == 2

    vorschau = client.post(
        f"/entities/{entity_id}/rows/undo-preview", data={"filter": "all", "range": "month", "offset": "0"}
    ).text
    batch_at = re.search(r'name="batch_at" value="([^"]+)"', vorschau).group(1)
    index.mark_deleted(entity_id, [1.0], deleted_at=float(batch_at) + 100)  # neuere, fremde Charge
    client.post(
        f"/entities/{entity_id}/rows/undo",
        data={"filter": "all", "range": "month", "offset": "0", "batch_at": batch_at},
    )
    assert index.get_deleted_counts_for_entity(entity_id) == {1.0: 1}


def test_hinzufuegen_und_korrigieren_lehnen_nan_und_unendlich_ab(client) -> None:
    from app.main import index

    entity_id = "sensor.pytest_dq_nan"
    _entity(index, entity_id)
    kopf = {"Content-Type": "application/json"}
    for body in ('{"ts": 1700000000, "value": NaN}', '{"ts": NaN, "value": 1}', '{"ts": 1700000000, "value": 1e400}'):
        assert client.post(f"/entities/{entity_id}/rows/add", content=body, headers=kopf).status_code == 400
    korrektur = '{"ts": 1700000000, "old_value": 1, "new_value": Infinity}'
    assert client.post(f"/entities/{entity_id}/rows/correct", content=korrektur, headers=kopf).status_code == 400


def test_ungueltige_eingaben_geben_400_statt_500(client) -> None:
    from app.main import index

    entity_id = "sensor.pytest_dq_eingabe"
    _entity(index, entity_id)
    form = {"filter": "all", "range": "day", "offset": "0", "page_size": "20"}
    for schlecht in ("abc", "nan", "inf"):
        assert client.post(f"/entities/{entity_id}/rows/delete", data={**form, "ts": schlecht}).status_code == 400
    for schlecht in ("1700000000:abc", "1700000000:nan", "abc:1"):
        assert client.post(f"/entities/{entity_id}/rows/delete", data={**form, "ts": schlecht}).status_code == 400
    assert client.get(f"/entities/{entity_id}/data-days?year=0&month=5").status_code == 400
    assert client.get(f"/entities/{entity_id}/data-days?year=10000&month=5").status_code == 400
    assert client.get(f"/entities/{entity_id}/data-days?year=2026&month=5").status_code == 200


def test_rueckgaengig_mit_unbekannter_entitaet_legt_nichts_an(client) -> None:
    antwort = client.post(
        "/housekeeping/datenqualitaet/duplicates/undo",
        data={"entity_id": ["sensor.pytest_gibt_es_nicht"], "batch_at": ["1.0"]},
    )
    assert antwort.status_code == 200
    assert "0</strong> Zeile(n) wiederhergestellt" in antwort.text


def test_nach_dem_loeschen_zeigt_die_ansicht_alte_gesamtzahlen_ohne_neu_zu_zaehlen(client, monkeypatch) -> None:
    """Löschen/Rückgängig entwerten die Gesamt-Zählung; die Antwort wartet nicht auf die Neuzählung (7–22 s an großen
    Entitäten), sondern zeigt den alten Stand mit Hinweis. Der Wartungstakt zählt neu."""
    from app import cleanup_stats
    from app.main import index

    entity_id = "sensor.pytest_dq_stale"
    _mit_duplikaten(index, entity_id)
    form = {"filter": "all", "range": "month", "offset": "0", "page_size": "20"}
    erste = client.get(f"/entities/{entity_id}/rows", params={"filter": "all", "range": "month", "offset": 0}).text
    assert "wird gerade neu gezählt" not in erste
    assert index.get_cleanup_alltime_stats(entity_id)["computed_at"] > 0

    def darf_nicht_laufen(*_args, **_kwargs):
        raise AssertionError("Gesamt-Zählung lief synchron in der Antwort")

    monkeypatch.setattr(cleanup_stats, "alltime_counts", darf_nicht_laufen)
    html = client.post(f"/entities/{entity_id}/rows/delete", data={**form, "source": "duplicates"}).text
    assert "wird gerade neu gezählt" in html
    assert 'hx-trigger="load delay:30s"' in html
    assert index.get_cleanup_alltime_stats(entity_id)["computed_at"] == 0  # der Wartungstakt misst als Nächstes


def test_a_ticked_row_marks_exactly_its_own_value_among_equal_timestamps(client) -> None:
    from app.main import index

    entity_id = "sensor.pytest_dq_wertzeile"
    _entity(index, entity_id)
    form = {"filter": "all", "range": "day", "offset": "0", "page_size": "20"}
    assert client.post(f"/entities/{entity_id}/rows/delete", data={**form, "ts": "1700000000.0:7.5"}).status_code == 200
    assert index.get_deleted_counts_for_entity(entity_id).values_by_ts == {1700000000.0: [7.5]}
    client.post(f"/entities/{entity_id}/rows/delete", data={**form, "ts": "1700000100.0"})  # älteres Formular
    assert index.get_deleted_counts_for_entity(entity_id)[1700000100.0] == 1
