"""Diagramm-Popup der Kennzahlen-Kacheln: EnergieDashboardService.compute_tile() und die Route.

Phase 1 der Popups (Verbrauch, Speicher). Die Bucket-Achse selbst steht in test_energiedashboard_buckets.py."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pytest
from fastapi import HTTPException

from _paths import APP

from test_energiedashboard_flow import _FesteUhr, _beispielanlage, _gauge, ed


@pytest.fixture()
def tmp(tmp_path_factory) -> Path:
    d = Path(tempfile.mkdtemp(prefix="zeitarchiv-edash-tile-", dir=tmp_path_factory.mktemp("edash")))
    yield d
    shutil.rmtree(d, ignore_errors=True)


def _tile(monkeypatch, tmp: Path, kpi: str, config_mutator=None, range_key: str = "day", offset: int = -1):
    a, config = _beispielanlage(tmp)
    if config_mutator:
        config_mutator(a, config)
    monkeypatch.setattr(ed, "datetime", _FesteUhr)
    return a, a.service.compute_tile(config, kpi, range_key, offset)


def test_detail_is_not_part_of_the_normal_flow(monkeypatch, tmp: Path) -> None:
    """/data, der Bericht und die KI-Werkzeuge bekommen das Feld nicht mitgeschleppt."""
    a, config = _beispielanlage(tmp)
    monkeypatch.setattr(ed, "datetime", _FesteUhr)
    try:
        assert "detail" not in a.service.compute_flow(config, "day", -1)
        assert "detail" in a.service.compute_flow(config, "day", -1, with_detail=True)
    finally:
        a.close()


def test_verbrauch_stacks_the_top_level_rows_and_grundlast(monkeypatch, tmp: Path) -> None:
    """Waschmaschine und Trockner (Gruppe Haushalt), Wallbox (Gruppe mit einem Mitglied, aufgelöst), Grundlast."""
    a, tile = _tile(monkeypatch, tmp, "verbrauch")
    try:
        names = [row["name"] for row in tile["series"]]
        assert sorted(names) == sorted(["Waschmaschine", "Trockner", "Wallbox", "Grundlast"])
        assert tile["unit"] == "kWh"
        assert len(tile["keys"]) == len(tile["labels"]) == 24
        assert tile["labels"][0] == "00:00" and tile["labels"][23] == "23:00"
        # Tabellenwerte kommen aus dem Breakdown und stimmen mit der Kachel überein.
        assert sum(row["value"] for row in tile["series"]) == pytest.approx(tile["total"])
        grundlast = next(row for row in tile["series"] if row["name"] == "Grundlast")
        assert grundlast["entity_id"] is None
        wallbox = next(row for row in tile["series"] if row["name"] == "Wallbox")
        assert wallbox["entity_id"] == "sensor.wallbox"
        assert wallbox["value"] == pytest.approx(4.0)
    finally:
        a.close()


def test_verbrauch_series_sum_to_the_row_values_without_clamping(monkeypatch, tmp: Path) -> None:
    """Die Serie einer Entitätszeile summiert sich auf ihren Wert (Zähler, kein Klammern)."""
    a, tile = _tile(monkeypatch, tmp, "verbrauch")
    try:
        for row in tile["series"]:
            if row["entity_id"]:
                assert sum(v for v in row["values"] if v is not None) == pytest.approx(row["value"])
    finally:
        a.close()


def test_verbrauch_does_not_double_count_behind_a_meter(monkeypatch, tmp: Path) -> None:
    """Hinter einem Zwischenzähler hängende Geräte stecken in dessen Wert: nur der Zähler wird gestapelt."""
    def mutate(a, config):
        a.zaehler("sensor.zwischen", (0, 0.0), (12, 1.5), (23, 3.0))
        a.zaehler("sensor.kind", (0, 0.0), (12, 0.5), (23, 1.0))
        config["verbraucher"].append({
            "entity_id": "sensor.zwischen", "name": "Werkstatt",
            "kinder": [{"entity_id": "sensor.kind", "name": "Fräse"}],
        })

    a, tile = _tile(monkeypatch, tmp, "verbrauch", mutate)
    try:
        names = [row["name"] for row in tile["series"]]
        assert "Werkstatt" in names
        assert "Fräse" not in names
        assert not any("übrige" in name for name in names)
    finally:
        a.close()


def test_speicher_has_charge_and_discharge_series_and_unit_rows(monkeypatch, tmp: Path) -> None:
    def mutate(a, config):
        config["speicher"] = [{
            "name": "Heimspeicher", "laden_entity_id": "sensor.laden", "entladen_entity_id": "sensor.entladen",
        }]
        a.zaehler("sensor.laden", (0, 200.0), (12, 203.0), (23, 205.0))
        a.zaehler("sensor.entladen", (0, 300.0), (12, 301.0), (23, 303.0))

    a, tile = _tile(monkeypatch, tmp, "speicher", mutate)
    try:
        by_role = {row["role"]: row for row in tile["series"]}
        assert sum(v for v in by_role["laden"]["values"] if v is not None) == pytest.approx(5.0)
        assert sum(v for v in by_role["entladen"]["values"] if v is not None) == pytest.approx(3.0)
        assert tile["total"] == {"laden": pytest.approx(5.0), "entladen": pytest.approx(3.0)}
        assert tile["units"] == [{
            "name": "Heimspeicher",
            "laden_entity_id": "sensor.laden", "laden": pytest.approx(5.0),
            "entladen_entity_id": "sensor.entladen", "entladen": pytest.approx(3.0),
        }]
    finally:
        a.close()


def test_speicher_without_storage_is_empty_not_an_error(monkeypatch, tmp: Path) -> None:
    a, tile = _tile(monkeypatch, tmp, "speicher")
    try:
        assert tile["units"] == []
        assert tile["total"] == {"laden": 0.0, "entladen": 0.0}
        assert all(v is None for row in tile["series"] for v in row["values"])
    finally:
        a.close()


def test_missing_entity_gives_an_empty_series(monkeypatch, tmp: Path) -> None:
    """Eine Rolle ohne Archivdaten bricht das Popup nicht ab (das Dashboard degradiert wie sonst auch)."""
    def mutate(a, config):
        config["verbraucher"].append({"entity_id": "sensor.gibt_es_nicht", "name": "Geist"})

    a, tile = _tile(monkeypatch, tmp, "verbrauch", mutate)
    try:
        ghost = next(row for row in tile["series"] if row["name"] == "Geist")
        assert ghost["value"] == 0
        assert all(v in (None, 0.0) for v in ghost["values"])
    finally:
        a.close()


def test_running_month_uses_calendar_days_on_the_axis(monkeypatch, tmp: Path) -> None:
    a, config = _beispielanlage(tmp)
    monkeypatch.setattr(ed, "datetime", _FesteUhr)
    try:
        tile = a.service.compute_tile(config, "verbrauch", "month", 0)
        assert tile["keys"] == list(range(1, 32))  # März hat 31 Tage
        assert tile["labels"][0] == "1"
    finally:
        a.close()


def test_unknown_kpi_and_range_are_rejected(monkeypatch, tmp: Path) -> None:
    a, config = _beispielanlage(tmp)
    monkeypatch.setattr(ed, "datetime", _FesteUhr)
    try:
        for kpi in ("unsinn", "kosten", ""):
            with pytest.raises(HTTPException) as err:
                a.service.compute_tile(config, kpi, "day", 0)
            assert err.value.status_code == 400
        with pytest.raises(HTTPException) as err:
            a.service.compute_tile(config, "verbrauch", "century", 0)
        assert err.value.status_code == 400
    finally:
        a.close()


def test_the_route_is_not_public() -> None:
    """Die Route ist nur über den Ingress erreichbar: kein Eintrag in PUBLIC_ENDPOINTS, kein eigener nginx-Block."""
    nginx = (APP.parent / "nginx.conf").read_text(encoding="utf-8")
    boundary = (Path(__file__).parent / "test_nginx_boundary.py").read_text(encoding="utf-8")
    assert "energiedashboard/tile" not in nginx
    assert "energiedashboard/tile" not in boundary


def _speicher_mit_soc(a, config, *soc_werte, capacity_kwh=None) -> None:
    speicher = {
        "name": "Heimspeicher", "laden_entity_id": "sensor.laden", "entladen_entity_id": "sensor.entladen",
        "soc_entity_id": "sensor.soc",
    }
    if capacity_kwh:
        speicher["capacity_kwh"] = capacity_kwh
    config["speicher"] = [speicher]
    a.zaehler("sensor.laden", (0, 200.0), (23, 205.0))
    a.zaehler("sensor.entladen", (0, 300.0), (23, 303.0))
    _gauge(a, "sensor.soc", "%", *soc_werte)


def test_speicher_day_has_the_state_of_charge_per_hour(monkeypatch, tmp: Path) -> None:
    """Ladestand je Achsenposition (lokale Stunde), Mittel der Messwerte darin; Stunden ohne Messwert nach dem
    Fensterrand bleiben leer statt 0 % zu zeigen."""
    def mutate(a, config):
        _speicher_mit_soc(a, config, (0, 40.0), (6, 50.0), (6.5, 60.0), (12, 80.0))

    a, tile = _tile(monkeypatch, tmp, "speicher", mutate)
    try:
        soc = tile["soc"]
        assert soc is not None and len(soc) == 24
        assert soc[0] == pytest.approx(40.0)
        assert soc[6] == pytest.approx(55.0)  # Mittel von 50 und 60 in dieser Stunde
        assert soc[12] == pytest.approx(80.0)
        assert all(v is None or 0.0 <= v <= 100.0 for v in soc)
    finally:
        a.close()


def test_speicher_soc_is_weighted_by_capacity(monkeypatch, tmp: Path) -> None:
    def mutate(a, config):
        _speicher_mit_soc(a, config, (0, 40.0), (12, 80.0), capacity_kwh=10.0)
        config["speicher"].append({
            "name": "Zweitspeicher", "laden_entity_id": "sensor.laden2", "soc_entity_id": "sensor.soc2",
            "capacity_kwh": 30.0,
        })
        a.zaehler("sensor.laden2", (0, 0.0), (23, 1.0))
        _gauge(a, "sensor.soc2", "%", (0, 80.0), (12, 80.0))

    a, tile = _tile(monkeypatch, tmp, "speicher", mutate)
    try:
        # (40 * 10 + 80 * 30) / 40 = 70 %
        assert tile["soc"][0] == pytest.approx(70.0)
    finally:
        a.close()


def test_speicher_soc_is_missing_without_sensor_and_for_long_ranges(monkeypatch, tmp: Path) -> None:
    a, tile = _tile(monkeypatch, tmp, "speicher")
    try:
        assert tile["soc"] is None
    finally:
        a.close()

    def mutate(a, config):
        _speicher_mit_soc(a, config, (0, 40.0), (12, 80.0))

    a, tile = _tile(monkeypatch, tmp / "monat", "speicher", mutate, range_key="month", offset=0)
    try:
        assert tile["soc"] is None
    finally:
        a.close()


def test_erzeugung_stacks_each_generator(monkeypatch, tmp: Path) -> None:
    def mutate(a, config):
        a.zaehler("sensor.pv2", (0, 0.0), (12, 1.0), (23, 3.0))
        config["erzeuger"].append({"entity_id": "sensor.pv2", "name": "Balkon"})

    a, tile = _tile(monkeypatch, tmp, "erzeugung", mutate)
    try:
        assert [row["name"] for row in tile["series"]] == ["Dach-PV", "Balkon"]
        assert tile["total"] == pytest.approx(13.0)
        for row in tile["series"]:
            assert sum(v for v in row["values"] if v is not None) == pytest.approx(row["value"])
        assert sum(row["share"] for row in tile["series"]) == pytest.approx(100.0, abs=0.2)
        assert tile["series"][0]["entity_id"] == "sensor.pv"
    finally:
        a.close()


def test_netzbezug_has_one_series_and_the_previous_period_on_the_same_axis(monkeypatch, tmp: Path) -> None:
    def mutate(a, config):
        # Vortag (14.03.): +10 über den Tag; der letzte Stand (100) ist zugleich der Anfangsstand des 15.03.
        a.zaehler("sensor.netz", (-24, 90.0), (-12, 95.0), (-1, 100.0))

    a, tile = _tile(monkeypatch, tmp, "netzbezug", mutate)
    try:
        assert tile["entity_id"] == "sensor.netz"
        assert len(tile["series"]) == 1 and tile["series"][0]["name"] == "Netzbezug"
        assert tile["total"] == pytest.approx(6.0)
        assert sum(v for v in tile["series"][0]["values"] if v is not None) == pytest.approx(6.0)
        previous = tile["previous"]
        assert previous["name"] == "Vortag"
        assert len(previous["values"]) == len(tile["keys"]) == 24
        assert sum(v for v in previous["values"] if v is not None) == pytest.approx(10.0)
    finally:
        a.close()


def test_einspeisung_uses_the_configured_name_and_needs_no_flow(monkeypatch, tmp: Path) -> None:
    def mutate(a, config):
        config["einspeisung_name"] = "Rückspeisung"

    a, config = _beispielanlage(tmp)
    mutate(a, config)
    monkeypatch.setattr(ed, "datetime", _FesteUhr)
    monkeypatch.setattr(a.service, "compute_flow", lambda *args, **kwargs: pytest.fail("compute_flow darf nicht laufen"))
    try:
        tile = a.service.compute_tile(config, "einspeisung", "day", -1)
        assert tile["series"][0]["name"] == "Rückspeisung"
        assert tile["total"] == pytest.approx(3.0)
        assert tile["entity_id"] == "sensor.einspeisung"
    finally:
        a.close()


def test_unconfigured_single_tile_is_empty_not_an_error(monkeypatch, tmp: Path) -> None:
    def mutate(a, config):
        config["einspeisung"] = ""

    a, tile = _tile(monkeypatch, tmp, "einspeisung", mutate)
    try:
        assert tile["series"] == [] and tile["previous"] is None and tile["entity_id"] is None
        assert tile["total"] == 0
    finally:
        a.close()


def test_single_tile_rejects_an_invalid_range(monkeypatch, tmp: Path) -> None:
    a, config = _beispielanlage(tmp)
    monkeypatch.setattr(ed, "datetime", _FesteUhr)
    try:
        with pytest.raises(HTTPException) as err:
            a.service.compute_tile(config, "netzbezug", "century", 0)
        assert err.value.status_code == 400
    finally:
        a.close()


def test_running_day_folds_nothing_and_leaves_future_hours_empty(monkeypatch, tmp: Path) -> None:
    """Laufender Tag (offset 0, NOW 12:00 am 16.03.): bis jetzt Werte, danach None."""
    a, config = _beispielanlage(tmp)
    a.zaehler("sensor.netz", (24, 106.0), (30, 108.0))
    monkeypatch.setattr(ed, "datetime", _FesteUhr)
    try:
        tile = a.service.compute_tile(config, "netzbezug", "day", 0)
        values = tile["series"][0]["values"]
        assert values[20] is None and values[23] is None
    finally:
        a.close()
