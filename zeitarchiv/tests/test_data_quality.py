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


def _entity(index, entity_id, *, name=None, schwelle="off"):
    index.get_or_create_entity(entity_id, "sensor", "measurement", "kWh")
    index.set_config(entity_id, outlier_threshold=schwelle, custom_name=name)


def _outliers(index, entity_id, markiert, gesamt=1000, schwelle="50"):
    index.set_config(entity_id, outlier_threshold=schwelle)
    index.set_cleanup_alltime_stats(
        entity_id, {"all": gesamt, "outliers": markiert}, schwelle, OUTLIER_RULE_VERSION
    )


def _row(entity_id, count):
    return {"entity_id": entity_id, "friendly_name": entity_id, "count": count}


def test_ohne_messung_ist_leer_und_ausstehend(index):
    _entity(index, "sensor.a")
    matrix = data_quality.build_matrix(index)
    assert matrix["rows"] == []
    assert matrix["affected"] == 0
    # Kein Schnappschuss ist nicht "keine Duplikate".
    assert matrix["pending"]["duplicates"] is True
    assert matrix["pending"]["counter_decreases"] is True


def test_leerer_schnappschuss_ist_gemessen_ohne_befund(index):
    _entity(index, "sensor.a")
    index.set_duplicate_snapshot([])
    index.set_counter_decrease_snapshot([])
    matrix = data_quality.build_matrix(index)
    assert matrix["pending"]["duplicates"] is False
    assert matrix["pending"]["counter_decreases"] is False
    assert matrix["rows"] == []


def test_entitaet_in_allen_drei_quellen_eine_zeile(index):
    _entity(index, "sensor.a", name="Gesamtenergie")
    _outliers(index, "sensor.a", markiert=7)
    index.set_duplicate_snapshot([_row("sensor.a", 12)])
    index.set_counter_decrease_snapshot([_row("sensor.a", 3)])
    matrix = data_quality.build_matrix(index)
    assert len(matrix["rows"]) == 1
    zeile = matrix["rows"][0]
    assert zeile["friendly_name"] == "Gesamtenergie"
    assert zeile["total"] == 22
    assert zeile["cells"]["duplicates"]["count"] == 12
    assert zeile["cells"]["counter_decreases"]["count"] == 3
    assert zeile["cells"]["outliers"]["count"] == 7
    assert matrix["totals"]["duplicates"]["count"] == 12


def test_entitaet_nur_in_einer_quelle_hat_leere_zellen(index):
    _entity(index, "sensor.a")
    index.set_duplicate_snapshot([_row("sensor.a", 5)])
    index.set_counter_decrease_snapshot([])
    zeile = data_quality.build_matrix(index)["rows"][0]
    assert zeile["cells"]["duplicates"]["count"] == 5
    assert zeile["cells"]["counter_decreases"] is None
    assert zeile["cells"]["outliers"] is None


def test_sortiert_nach_summe_absteigend(index):
    for eid in ("sensor.a", "sensor.b", "sensor.c"):
        _entity(index, eid)
    index.set_duplicate_snapshot([_row("sensor.a", 2), _row("sensor.b", 9), _row("sensor.c", 5)])
    index.set_counter_decrease_snapshot([_row("sensor.a", 1)])
    reihenfolge = [r["entity_id"] for r in data_quality.build_matrix(index)["rows"]]
    assert reihenfolge == ["sensor.b", "sensor.c", "sensor.a"]


def test_ausreisser_unter_der_auffaelligkeitsmarke_zaehlen_mit(index):
    # 5 von 1000 = 0,5 %: für Housekeeping → Ausreißer unauffällig, als Anzahl
    # in der Matrix aber ein Befund.
    _entity(index, "sensor.a")
    _outliers(index, "sensor.a", markiert=5)
    index.set_duplicate_snapshot([])
    index.set_counter_decrease_snapshot([])
    zeile = data_quality.build_matrix(index)["rows"][0]
    assert zeile["cells"]["outliers"]["count"] == 5


def test_ausreisser_ohne_treffer_erzeugt_keine_zeile(index):
    _entity(index, "sensor.a")
    _outliers(index, "sensor.a", markiert=0)
    index.set_duplicate_snapshot([])
    index.set_counter_decrease_snapshot([])
    assert data_quality.build_matrix(index)["rows"] == []


def test_ausreisser_ohne_messung_werden_als_ausstehend_gezaehlt(index):
    _entity(index, "sensor.a", schwelle="50")
    _entity(index, "sensor.b", schwelle="50")
    _outliers(index, "sensor.b", markiert=1)
    assert data_quality.build_matrix(index)["pending"]["outliers"] == 1


def test_links_tragen_filter_und_zeitraum(index):
    _entity(index, "sensor.a")
    _outliers(index, "sensor.a", markiert=2)
    index.set_duplicate_snapshot([_row("sensor.a", 1)])
    index.set_counter_decrease_snapshot([_row("sensor.a", 1)])
    zellen = data_quality.build_matrix(index)["rows"][0]["cells"]
    assert zellen["duplicates"]["href"] == "entities/sensor.a/cleanup?filter=duplicates&range=all"
    assert zellen["counter_decreases"]["href"] == (
        "entities/sensor.a/cleanup?filter=counter_decreases&range=all"
    )
    assert zellen["outliers"]["href"] == "entities/sensor.a/cleanup?filter=outliers&range=all"


def test_schnappschuss_zeile_ohne_stammdaten_faellt_auf_entity_id_zurueck(index):
    # Eine Entität kann aus dem Index verschwunden sein, während der
    # Schnappschuss sie noch kennt.
    index.set_duplicate_snapshot([_row("sensor.weg", 4)])
    index.set_counter_decrease_snapshot([])
    zeile = data_quality.build_matrix(index)["rows"][0]
    assert zeile["friendly_name"] == "sensor.weg"


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
