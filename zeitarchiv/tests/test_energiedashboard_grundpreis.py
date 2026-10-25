"""Grundpreis im Energiedashboard: €/Monat, tageweise anteilig (× 12 / 365).

Aufbau wie test_energiedashboard_flow.py (gleiche Beispielanlage, feste Uhr).
Der Grundpreis ist eine Entität (Zustand, gilt bis zur nächsten Änderung) oder
ein fester Wert; ein fehlender Wert wird als Lücke in Tagen gemeldet.
"""

from __future__ import annotations

import datetime as _dt
import tempfile
from pathlib import Path

import pytest
from test_energiedashboard_flow import (
    TZ,
    _beispielanlage,
    _FesteUhr,
    _preis_anlage_mit_ende,
    _ts,
    ed,
    hotbuffer,
)

TAG_ANTEIL = 12.0 / 365.0


@pytest.fixture
def tmp():
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


def _check(flow: dict, label: str) -> dict:
    return next(p for p in flow["quality"]["checks"] if p["label"] == label)


def test_fixed_base_price_is_prorated_per_day_and_enters_the_balance(monkeypatch, tmp: Path) -> None:
    a, config = _beispielanlage(tmp)
    config["kosten"] = {"preis_netzbezug_fixed": 0.30, "grundpreis_fixed": 12.5}
    monkeypatch.setattr(ed, "datetime", _FesteUhr)
    try:
        kpi = a.service.compute_flow(config, "day", -1)["kpi"]
        assert kpi["grundpreis_cost"] == pytest.approx(12.5 * TAG_ANTEIL, abs=0.005)
        assert kpi["grundpreis_info"] == {"tage": 1.0, "monat": 12.5}
        # 6 kWh × 0,30 € + Grundpreis eines Tages
        assert kpi["net_cost"] == pytest.approx(1.80 + 12.5 * TAG_ANTEIL, abs=0.01)
        assert kpi["kosten_luecke"] == []
    finally:
        a.close()


def test_base_price_alone_gives_a_balance(monkeypatch, tmp: Path) -> None:
    a, config = _beispielanlage(tmp)
    config["kosten"] = {"grundpreis_fixed": 30.0}
    monkeypatch.setattr(ed, "datetime", _FesteUhr)
    try:
        kpi = a.service.compute_flow(config, "day", -1)["kpi"]
        assert kpi["netzbezug_cost"] is None
        assert kpi["net_cost"] == pytest.approx(30.0 * TAG_ANTEIL, abs=0.005)
    finally:
        a.close()


def test_without_base_price_nothing_changes(monkeypatch, tmp: Path) -> None:
    a, config = _beispielanlage(tmp)
    config["kosten"] = {"preis_netzbezug_fixed": 0.30}
    monkeypatch.setattr(ed, "datetime", _FesteUhr)
    try:
        kpi = a.service.compute_flow(config, "day", -1)["kpi"]
        assert kpi["grundpreis_cost"] is None and kpi["grundpreis_info"] is None
        assert kpi["net_cost"] == pytest.approx(1.80, abs=0.01)
    finally:
        a.close()


def _helfer(a, tmp: Path, *punkte: tuple[float, float]) -> None:
    a.index.get_or_create_entity("input_number.grundpreis", "input_number", "measurement", "EUR")
    for stunde, wert in punkte:
        hotbuffer.append(tmp, "input_number.grundpreis", _ts(stunde), wert, TZ)
        a.index.record_write("input_number.grundpreis", _ts(stunde))


def test_a_helper_that_changed_only_once_before_the_period_covers_it(monkeypatch, tmp: Path) -> None:
    """Ein Helfer meldet sich nur bei Änderungen. Sein Wert vom Vortag gilt auch
    dann noch, wenn die Lückenschwelle der Entität (Standard 15 Minuten) längst
    verstrichen ist — anders als beim Arbeitspreis, der ein Sensor sein kann."""
    a, config = _beispielanlage(tmp)
    _helfer(a, tmp, (-30, 20.0))
    config["kosten"] = {"grundpreis": "input_number.grundpreis"}
    monkeypatch.setattr(ed, "datetime", _FesteUhr)
    try:
        flow = a.service.compute_flow(config, "day", -1)
        assert flow["kpi"]["grundpreis_cost"] == pytest.approx(20.0 * TAG_ANTEIL, abs=0.005)
        assert flow["kpi"]["kosten_luecke"] == []
        assert _check(flow, "Preise und Faktoren vollständig")["ok"] is True
    finally:
        a.close()


def test_the_entity_wins_over_the_fixed_value(monkeypatch, tmp: Path) -> None:
    a, config = _beispielanlage(tmp)
    _helfer(a, tmp, (-30, 20.0))
    config["kosten"] = {"grundpreis": "input_number.grundpreis", "grundpreis_fixed": 99.0}
    monkeypatch.setattr(ed, "datetime", _FesteUhr)
    try:
        kpi = a.service.compute_flow(config, "day", -1)["kpi"]
        assert kpi["grundpreis_cost"] == pytest.approx(20.0 * TAG_ANTEIL, abs=0.005)
    finally:
        a.close()


def test_a_value_set_during_the_day_counts_for_that_day(monkeypatch, tmp: Path) -> None:
    """Ein Wert, der erst um 12 Uhr gesetzt wird, gilt für den ganzen Tag — sonst wäre
    schon der Tag der Einrichtung eine Lücke, die niemand beheben kann."""
    a, config = _beispielanlage(tmp)
    _helfer(a, tmp, (12, 30.0))
    config["kosten"] = {"grundpreis": "input_number.grundpreis"}
    monkeypatch.setattr(ed, "datetime", _FesteUhr)
    try:
        flow = a.service.compute_flow(config, "day", -1)
        assert flow["kpi"]["grundpreis_cost"] == pytest.approx(30.0 * TAG_ANTEIL, abs=0.005)
        assert flow["kpi"]["kosten_luecke"] == []
    finally:
        a.close()


def test_a_value_that_starts_later_leaves_a_gap_in_days(monkeypatch, tmp: Path) -> None:
    """Der Helfer bekommt seinen ersten Wert erst am Tag nach dem Zeitraum: kein Betrag,
    und die Lücke steht in Tagen im Status."""
    a, config = _beispielanlage(tmp)
    _helfer(a, tmp, (36, 30.0))
    config["kosten"] = {"grundpreis": "input_number.grundpreis"}
    monkeypatch.setattr(ed, "datetime", _FesteUhr)
    try:
        flow = a.service.compute_flow(config, "day", -1)
        assert flow["kpi"]["grundpreis_cost"] is None
        assert flow["kpi"]["kosten_luecke"] == [
            {"key": "grundpreis", "label": "Grundpreis", "menge": 1.0, "anteil": 100, "einheit": "Tage"}
        ]
        pruefung = _check(flow, "Preise und Faktoren vollständig")
        assert pruefung["ok"] is False
        assert "Grundpreis (1.0 Tage, 100 %)" in pruefung["detail"]
    finally:
        a.close()


def test_in_a_month_only_the_days_before_the_first_value_are_a_gap(monkeypatch, tmp: Path) -> None:
    """Zeitraum Monat (15.03. 12 Uhr = 14,5 Tage verstrichen): Der Helfer hat seinen ersten
    Wert am 10.03. — die Tage davor fehlen, die übrigen werden angesetzt."""
    a, config = _beispielanlage(tmp)
    _helfer(a, tmp, (-5 * 24, 30.0))  # 10.03. 0:00
    config["kosten"] = {"grundpreis": "input_number.grundpreis"}
    monkeypatch.setattr(ed, "datetime", _FesteUhr)
    try:
        flow = a.service.compute_flow(config, "month", 0)
        kpi = flow["kpi"]
        # 1.–9.3. ohne Wert (9 Tage), 10.–16.3. 12 Uhr mit Wert (6,5 Tage)
        assert kpi["kosten_luecke"][0]["menge"] == pytest.approx(9.0)
        assert kpi["grundpreis_cost"] == pytest.approx(6.5 * 30.0 * TAG_ANTEIL, abs=0.02)
    finally:
        a.close()


def test_a_dead_work_price_sensor_is_reported_as_a_cost_gap(monkeypatch, tmp: Path) -> None:
    a, config = _preis_anlage_mit_ende(tmp, letzte_stunde=3)
    monkeypatch.setattr(ed, "datetime", _FesteUhr)
    try:
        luecke = a.service.compute_flow(config, "day", -1)["kpi"]["kosten_luecke"]
        assert [eintrag["key"] for eintrag in luecke] == ["netzbezug"]
        assert luecke[0]["label"] == "Netzbezug-Preis" and luecke[0]["einheit"] == "kWh"
    finally:
        a.close()


def test_co2_gaps_do_not_count_as_cost_gaps(monkeypatch, tmp: Path) -> None:
    a, config = _beispielanlage(tmp)
    a.index.get_or_create_entity("sensor.co2", "sensor", "measurement", "g/kWh")
    hotbuffer.append(tmp, "sensor.co2", _ts(12), 300.0, TZ)
    a.index.record_write("sensor.co2", _ts(12))
    config["co2"] = {"faktor_netzbezug_entity": "sensor.co2"}
    config["kosten"] = {"preis_netzbezug_fixed": 0.30}
    monkeypatch.setattr(ed, "datetime", _FesteUhr)
    try:
        flow = a.service.compute_flow(config, "day", -1)
        assert _check(flow, "Preise und Faktoren vollständig")["ok"] is False
        assert flow["kpi"]["kosten_luecke"] == []
    finally:
        a.close()


def test_a_price_helper_with_gap_detection_off_is_not_reported_stale(monkeypatch, tmp: Path) -> None:
    """„Lückenerkennung Aus“ ist die Ansage „sendet nur bei Änderungen“. Ein
    solcher Preis-Helfer darf nicht als veralteter Sensor in der Prüfung
    „Sensorwerte aktuell“ auftauchen — sonst wäre die Empfehlung, einen Helfer
    zu nehmen, mit einer Dauerwarnung erkauft."""
    monkeypatch.setattr(ed, "STALE_SECONDS", 3600)
    a, config = _preis_anlage_mit_ende(tmp, letzte_stunde=3, schwelle="off")
    monkeypatch.setattr(ed, "datetime", _FesteUhr)
    try:
        flow = a.service.compute_flow(config, "day", -1)
        stale = [p for p in flow["quality"]["checks"] if p["label"] == "Sensorwerte aktuell"][0]
        assert "Preis Netzbezug" not in stale["detail"]
    finally:
        a.close()


def test_a_price_sensor_with_default_gap_detection_still_counts_as_stale(monkeypatch, tmp: Path) -> None:
    monkeypatch.setattr(ed, "STALE_SECONDS", 3600)
    a, config = _preis_anlage_mit_ende(tmp, letzte_stunde=3)
    monkeypatch.setattr(ed, "datetime", _FesteUhr)
    try:
        flow = a.service.compute_flow(config, "day", -1)
        stale = [p for p in flow["quality"]["checks"] if p["label"] == "Sensorwerte aktuell"][0]
        assert "Preis Netzbezug" in stale["detail"]
    finally:
        a.close()


def test_a_day_with_a_clock_change_still_counts_as_one_day(tmp: Path) -> None:
    a, _config = _beispielanlage(tmp)
    try:
        start = _dt.datetime(2024, 3, 31, 0, 0, tzinfo=TZ)  # Sommerzeit: 23 Stunden
        ende = _dt.datetime(2024, 4, 1, 0, 0, tzinfo=TZ)
        tage = a.service._grundpreis_tage(start, ende, ende + _dt.timedelta(days=5))
        assert sum(anteil for _start, _ende, anteil in tage) == pytest.approx(1.0)
    finally:
        a.close()


def test_the_running_period_only_counts_the_elapsed_time(tmp: Path) -> None:
    a, _config = _beispielanlage(tmp)
    try:
        start = _dt.datetime(2024, 3, 1, 0, 0, tzinfo=TZ)
        ende = _dt.datetime(2024, 4, 1, 0, 0, tzinfo=TZ)
        jetzt = _dt.datetime(2024, 3, 10, 12, 0, tzinfo=TZ)
        tage = a.service._grundpreis_tage(start, ende, jetzt)
        assert sum(anteil for _start, _ende, anteil in tage) == pytest.approx(9.5)
    finally:
        a.close()


def test_a_rarely_changing_helper_keeps_its_value_for_the_whole_running_month(monkeypatch, tmp: Path) -> None:
    """Der laufende Bucket wird durch die feinere Auflösung ersetzt. Hatte der Helfer im
    Monat nur einen einzigen Punkt (seine letzte Änderung), wurde dieser mitgelöscht, und der
    Preis galt erst ab heute — der Rest des Monats stand ohne Preis da."""
    a, _config = _beispielanlage(tmp)
    _helfer(a, tmp, (-120, 30.0))  # 10.03.
    monkeypatch.setattr(ed, "datetime", _FesteUhr)
    try:
        from test_energiedashboard_flow import NOW, query_mod

        serie, _stale = a.service._entity_series("input_number.grundpreis", "month", 0, NOW, query_mod.QueryReadCache())
        assert min(serie) == _ts(-120)
    finally:
        a.close()


# --- Ø-Preis Netzbezug (Kostenanalyse) -------------------------------------

def test_avg_price_is_cost_divided_by_grid_energy_with_price_entity(monkeypatch, tmp: Path) -> None:
    from test_energiedashboard_flow import _preis_anlage

    a, config = _preis_anlage(tmp, versatz_minuten=0, takt_minuten=60)
    monkeypatch.setattr(ed, "datetime", _FesteUhr)
    try:
        kpi = a.service.compute_flow(config, "day", -1)["kpi"]
        # 6 kWh × 0,30 € = 1,80 € → 0,30 €/kWh; Tag = stündliche Buckets
        assert kpi["netzbezug_avg_price"] == pytest.approx(0.30, abs=0.0005)
        assert kpi["kosten_aufloesung"] == "stunde"
    finally:
        a.close()


def test_avg_price_missing_when_no_energy_has_a_price(monkeypatch, tmp: Path) -> None:
    a, config = _preis_anlage_mit_ende(tmp, letzte_stunde=3)
    monkeypatch.setattr(ed, "datetime", _FesteUhr)
    try:
        kpi = a.service.compute_flow(config, "day", -1)["kpi"]
        # Der Sensor verstummt um 3 Uhr, der ganze Netzbezug liegt danach: keine Energie
        # mit Preis, also kein Durchschnitt (statt 0 €/kWh aus 0 € Kosten).
        assert kpi["netzbezug_avg_price"] is None
    finally:
        a.close()


def test_avg_price_missing_with_fixed_price(monkeypatch, tmp: Path) -> None:
    a, config = _beispielanlage(tmp)
    config["kosten"] = {"preis_netzbezug_fixed": 0.30}
    monkeypatch.setattr(ed, "datetime", _FesteUhr)
    try:
        kpi = a.service.compute_flow(config, "day", -1)["kpi"]
        assert kpi["netzbezug_cost"] == pytest.approx(1.80, abs=0.01)
        assert kpi["netzbezug_avg_price"] is None
    finally:
        a.close()
