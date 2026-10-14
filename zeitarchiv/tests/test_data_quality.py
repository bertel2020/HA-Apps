"""Housekeeping → Datenqualität: die Matrix aus den vorhandenen Messungen.

Die Matrix rechnet nichts nach, sie führt Duplikat-Schnappschuss,
Zählerrückgang-Schnappschuss und Ausreißer-Quoten über die entity_id zusammen.
Geprüft wird deshalb vor allem, dass nichts verloren geht, nichts doppelt zählt
und dass "noch nicht gemessen" von "nichts gefunden" unterscheidbar bleibt.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pytest

from app import data_quality
from app.storage.cleanup import OUTLIER_RULE_VERSION
from app.storage.index import Index


@pytest.fixture()
def index():
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-dq-"))
    idx = Index(tmp / "index.sqlite")
    try:
        yield idx
    finally:
        idx.close()
        shutil.rmtree(tmp, ignore_errors=True)


def _entity(index, entity_id, *, name=None, schwelle="50"):
    index.get_or_create_entity(entity_id, "sensor", "measurement", "kWh")
    index.set_config(entity_id, outlier_threshold=schwelle, custom_name=name)


def _gesamt(index, entity_id, *, duplicates=0, counter_decreases=0, outliers=0):
    """Legt der Entität die Gesamt-Zählung unter, wie sie der Wartungsplaner nach einem
    Lauf über die komplette Historie hinterlässt (dieselbe Quelle wie die
    Trefferzahlen der Bereinigungsseite)."""
    index.set_config(entity_id, outlier_threshold="50")
    index.set_cleanup_alltime_stats(
        entity_id,
        {"all": 1000, "outliers": outliers, "gaps": 0, "duplicates": duplicates,
         "repetitions": 0, "counter_decreases": counter_decreases},
        "50", OUTLIER_RULE_VERSION, gap_threshold="15",
    )


def _mit_daten(index, entity_id, **kwargs):
    """Eine Entität, die schon Werte hat (first_ts gesetzt) und damit gemessen werden soll."""
    _entity(index, entity_id, **kwargs)
    index.set_first_ts_and_add_rows(entity_id, 1_700_000_000.0, 0)


def test_ohne_messung_ist_leer_und_ausstehend(index):
    _mit_daten(index, "sensor.a")
    matrix = data_quality.build_matrix(index)
    assert matrix["rows"] == []
    assert matrix["affected"] == 0
    # Keine Zählung ist nicht "keine Befunde".
    assert matrix["pending"] == 1


def test_entitaet_ohne_daten_zaehlt_nicht_als_ausstehend(index):
    _entity(index, "sensor.a")
    assert data_quality.build_matrix(index)["pending"] == 0


def test_gemessen_ohne_befund_ist_nicht_ausstehend(index):
    _mit_daten(index, "sensor.a")
    _gesamt(index, "sensor.a")
    matrix = data_quality.build_matrix(index)
    assert matrix["pending"] == 0
    assert matrix["rows"] == []


def test_entitaet_mit_allen_drei_befunden_eine_zeile(index):
    _mit_daten(index, "sensor.a", name="Gesamtenergie")
    _gesamt(index, "sensor.a", duplicates=12, counter_decreases=3, outliers=7)
    matrix = data_quality.build_matrix(index)
    assert len(matrix["rows"]) == 1
    zeile = matrix["rows"][0]
    assert zeile["friendly_name"] == "Gesamtenergie"
    assert zeile["total"] == 22
    assert zeile["cells"]["duplicates"]["count"] == 12
    assert zeile["cells"]["counter_decreases"]["count"] == 3
    assert zeile["cells"]["outliers"]["count"] == 7
    assert matrix["totals"]["duplicates"]["count"] == 12


def test_entitaet_nur_mit_einem_befund_hat_leere_zellen(index):
    _mit_daten(index, "sensor.a")
    _gesamt(index, "sensor.a", duplicates=5)
    zeile = data_quality.build_matrix(index)["rows"][0]
    assert zeile["cells"]["duplicates"]["count"] == 5
    assert zeile["cells"]["counter_decreases"] is None
    assert zeile["cells"]["outliers"] is None


def test_ohne_ausreisser_erkennung_zaehlen_duplikate_trotzdem(index):
    # Der Wartungsplaner misst auch Entitäten, deren Ausreißer-Erkennung aus ist,
    # sonst fehlten ihre Duplikate und Zählerrückgänge in der Matrix.
    _mit_daten(index, "sensor.a", schwelle="off")
    index.set_cleanup_alltime_stats(
        "sensor.a", {"all": 10, "duplicates": 4, "counter_decreases": 0}, "off", OUTLIER_RULE_VERSION,
        gap_threshold="15"
    )
    matrix = data_quality.build_matrix(index)
    assert matrix["pending"] == 0
    assert matrix["rows"][0]["cells"]["duplicates"]["count"] == 4


def test_sortiert_nach_summe_absteigend(index):
    for eid in ("sensor.a", "sensor.b", "sensor.c"):
        _mit_daten(index, eid)
    _gesamt(index, "sensor.a", duplicates=2, counter_decreases=1)
    _gesamt(index, "sensor.b", duplicates=9)
    _gesamt(index, "sensor.c", duplicates=5)
    reihenfolge = [r["entity_id"] for r in data_quality.build_matrix(index)["rows"]]
    assert reihenfolge == ["sensor.b", "sensor.c", "sensor.a"]


def test_ausreisser_unter_der_auffaelligkeitsmarke_zaehlen_mit(index):
    # 5 von 1000 = 0,5 %: für Housekeeping → Ausreißer unauffällig, als Anzahl
    # in der Matrix aber ein Befund.
    _mit_daten(index, "sensor.a")
    _gesamt(index, "sensor.a", outliers=5)
    zeile = data_quality.build_matrix(index)["rows"][0]
    assert zeile["cells"]["outliers"]["count"] == 5


def test_ausreisser_mit_anderer_schwelle_gezaehlt_gilt_als_ausstehend(index):
    _mit_daten(index, "sensor.a", schwelle="50")
    index.set_cleanup_alltime_stats(
        "sensor.a", {"all": 1000, "outliers": 9, "duplicates": 0, "counter_decreases": 0}, "100", OUTLIER_RULE_VERSION
    )
    matrix = data_quality.build_matrix(index)
    assert matrix["pending"] == 1
    assert matrix["rows"] == []


def test_links_tragen_filter_und_zeitraum(index):
    _mit_daten(index, "sensor.a")
    _gesamt(index, "sensor.a", duplicates=1, counter_decreases=1, outliers=2)
    zellen = data_quality.build_matrix(index)["rows"][0]["cells"]
    assert zellen["duplicates"]["href"] == "entities/sensor.a/cleanup?filter=duplicates&range=all"
    assert zellen["counter_decreases"]["href"] == (
        "entities/sensor.a/cleanup?filter=counter_decreases&range=all"
    )
    assert zellen["outliers"]["href"] == "entities/sensor.a/cleanup?filter=outliers&range=all"


@pytest.mark.parametrize(
    ("filter_", "range_", "erwartet"),
    [
        ("duplicates", "all", ("duplicates", "all")),
        ("duplicates", "month", ("duplicates", "month")),
        ("unsinn", "month", ("all", "month")),
        ("duplicates", "unsinn", ("duplicates", "day")),
        ("", "", ("all", "day")),
    ],
)
def test_startansicht_prueft_filter_und_zeitraum(filter_, range_, erwartet):
    assert data_quality.initial_cleanup_view(
        filter_, range_, outliers=True, gaps=True, counter_decreases=True
    ) == erwartet


def test_startansicht_verwirft_filter_ohne_aktive_erkennung():
    ansicht = data_quality.initial_cleanup_view(
        "outliers", "all", outliers=False, gaps=True, counter_decreases=True
    )
    assert ansicht == ("all", "all")
    ansicht = data_quality.initial_cleanup_view(
        "counter_decreases", "all", outliers=True, gaps=True, counter_decreases=False
    )
    assert ansicht == ("all", "all")


def test_paginate_teilt_in_seiten_zu_50():
    rows = [{"n": i} for i in range(120)]
    seite, info = data_quality.paginate(rows, 1, data_quality.DEFAULT_PAGE_SIZE)
    assert len(seite) == 50
    assert (info["start"], info["end"], info["total"], info["total_pages"]) == (1, 50, 120, 3)
    seite, info = data_quality.paginate(rows, 3, 50)
    assert [r["n"] for r in seite] == list(range(100, 120))
    assert (info["start"], info["end"]) == (101, 120)


def test_paginate_klemmt_seite_und_verwirft_unbekannte_groessen():
    rows = [{"n": i} for i in range(30)]
    _, info = data_quality.paginate(rows, 99, 50)
    assert info["page"] == 1
    _, info = data_quality.paginate(rows, 1, 7)
    assert info["page_size"] == data_quality.DEFAULT_PAGE_SIZE
    _, info = data_quality.paginate(rows, 4, 10)
    assert info["page"] == 3


def test_paginate_leere_liste():
    seite, info = data_quality.paginate([], 1, 50)
    assert seite == []
    assert (info["start"], info["end"], info["total_pages"]) == (0, 0, 1)


def test_erlaubte_zaehlerrueckgaenge_werden_gekennzeichnet(index):
    """Ein Zähler mit "Zählerrückgänge = Erlauben" zählt nie Rückgänge; die leere
    Zelle soll nicht wie "nichts gefunden" aussehen. Gekennzeichnet wird nur dort,
    wo es zutrifft (Zähler, Modus allow), und nur in Zeilen, die ohnehin einen Befund haben."""
    for eid in ("sensor.zaehler", "sensor.normal"):
        index.get_or_create_entity(eid, "sensor", "total_increasing", "kWh")
        index.set_first_ts_and_add_rows(eid, 1_700_000_000.0, 0)
        _gesamt(index, eid, duplicates=3)
    index.set_counter_decreases("sensor.zaehler", "allow")
    rows = {r["entity_id"]: r for r in data_quality.build_matrix(index)["rows"]}
    assert rows["sensor.zaehler"]["counter_allowed"] is True
    assert rows["sensor.zaehler"]["cells"]["counter_decreases"] is None
    assert rows["sensor.normal"]["counter_allowed"] is False


def test_luecken_zaehlen_nur_mit_passender_schwelle(index):
    """Eine Lücken-Zahl gehört zu der Schwelle, mit der sie gezählt wurde. Nach einer
    Änderung der Schwelle darf die alte Zahl nicht stehen bleiben; die Entität gilt
    als nicht gemessen, bis der Planer sie neu gerechnet hat."""
    _mit_daten(index, "sensor.a")
    index.set_cleanup_alltime_stats(
        "sensor.a", {"all": 100, "outliers": 0, "gaps": 7, "duplicates": 0, "repetitions": 0,
                     "counter_decreases": 0},
        "50", OUTLIER_RULE_VERSION, gap_threshold="15",
    )
    matrix = data_quality.build_matrix(index)
    assert matrix["rows"][0]["cells"]["gaps"]["count"] == 7
    assert matrix["rows"][0]["cells"]["gaps"]["href"].startswith("entities/sensor.a/cleanup?filter=gaps")
    assert matrix["pending"] == 0

    index.set_config("sensor.a", gap_threshold="60")
    matrix = data_quality.build_matrix(index)
    assert matrix["rows"] == []
    assert matrix["pending"] == 1


def test_luecken_ausgeschaltet_gibt_nichts_zu_messen(index):
    _mit_daten(index, "sensor.a")
    index.set_config("sensor.a", gap_threshold="off")
    index.set_cleanup_alltime_stats(
        "sensor.a", {"all": 100, "outliers": 0, "gaps": 0, "duplicates": 2, "repetitions": 0,
                     "counter_decreases": 0},
        "50", OUTLIER_RULE_VERSION, gap_threshold="off",
    )
    matrix = data_quality.build_matrix(index)
    assert matrix["pending"] == 0
    assert matrix["rows"][0]["cells"]["gaps"] is None


def test_aufgeklappte_duplikate_fassen_ein_paar_zu_einem_eintrag_zusammen(tmp_path):
    """Ein Duplikat-Paar besteht aus zwei Zeilen mit gleichem Zeitstempel; in der
    aufgeklappten Zeile steht es einmal. Gezählt bleiben Zeilen (6 Zeilen = 3 Paare),
    damit die Zahl mit Matrix und Filter der Bereinigungsseite übereinstimmt."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from app.storage import hotbuffer

    tz = ZoneInfo("Europe/Berlin")
    idx = Index(tmp_path / "index.sqlite")
    try:
        idx.get_or_create_entity("sensor.d", "sensor", "measurement", "kWh")
        idx.set_config("sensor.d", outlier_threshold="off", gap_threshold="off")
        first = datetime(2024, 7, 1, 8, tzinfo=tz).timestamp()
        for hour, value in enumerate((1.0, 2.0, 3.0, 4.0)):
            ts = first + hour * 3600
            hotbuffer.append(tmp_path, "sensor.d", ts, value, tz)
            if hour in (1, 2):  # zwei Paare
                hotbuffer.append(tmp_path, "sensor.d", ts, value, tz)
            idx.record_write("sensor.d", ts)
        idx.set_first_ts_and_add_rows("sensor.d", first, 0)
        now = datetime(2024, 7, 1, 12, tzinfo=tz)
        groups = data_quality.findings_for_entity(tmp_path, idx, tz, idx.get_entity("sensor.d"), now=now)
        duplikate = next(g for g in groups if g["key"] == "duplicates")
        assert duplikate["total"] == 4
        assert duplikate["shown"] == 4
        assert len(duplikate["rows"]) == 2
        assert duplikate["more"] == 0
    finally:
        idx.close()
