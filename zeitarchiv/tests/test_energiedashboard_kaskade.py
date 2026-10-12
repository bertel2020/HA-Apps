"""Zwischenzähler-Kaskade im Energiedashboard: Geräte hinter einem Zwischenzähler.

Der Zwischenzähler enthält seine Geräte schon. Entscheidend ist deshalb, dass nichts doppelt
zählt: Grundlast und Verbrauch dürfen sich nicht ändern, wenn man dieselben Geräte hinter einen
Zähler hängt, der genau ihre Summe misst. Aufbau wie test_energiedashboard_flow.py.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest
from fastapi import HTTPException
from test_energiedashboard_flow import _beispielanlage, _FesteUhr, ed


@pytest.fixture
def tmp():
    with tempfile.TemporaryDirectory() as d:
        yield Path(d)


def _kaskade(a, config: dict, zaehler_kwh: float) -> dict:
    """Waschmaschine (1 kWh) und Trockner (2 kWh) hängen hinter einem Zwischenzähler, der
    ``zaehler_kwh`` über den Tag zählt; die Wallbox (4 kWh) bleibt eigenständig."""
    a.zaehler("sensor.werkstatt", (0, 200.0), (12, 200.0 + zaehler_kwh / 2), (23, 200.0 + zaehler_kwh))
    config["verbraucher"] = [
        {"entity_id": "sensor.werkstatt", "name": "Werkstatt", "gruppe": None},
        {"entity_id": "sensor.waschmaschine", "name": "Waschmaschine", "gruppe": None, "parent": "sensor.werkstatt"},
        {"entity_id": "sensor.trockner", "name": "Trockner", "gruppe": None, "parent": "sensor.werkstatt"},
        {"entity_id": "sensor.wallbox", "name": "Wallbox", "gruppe": None},
    ]
    return config


def _flach(config: dict) -> dict:
    config["verbraucher"] = [
        {"entity_id": "sensor.waschmaschine", "name": "Waschmaschine", "gruppe": None},
        {"entity_id": "sensor.trockner", "name": "Trockner", "gruppe": None},
        {"entity_id": "sensor.wallbox", "name": "Wallbox", "gruppe": None},
    ]
    return config


def _breakdown(flow: dict) -> dict[str, dict]:
    return {item["name"]: item for item in flow["verbraucher_breakdown"]}


def test_a_meter_that_counts_exactly_its_devices_leaves_the_balance_unchanged(monkeypatch, tmp: Path) -> None:
    a, config = _beispielanlage(tmp)
    monkeypatch.setattr(ed, "datetime", _FesteUhr)
    try:
        flach = a.service.compute_flow(_flach(config), "day", -1)
        kaskade = a.service.compute_flow(_kaskade(a, config, zaehler_kwh=3.0), "day", -1)
        assert kaskade["kpi"]["verbrauch"] == pytest.approx(flach["kpi"]["verbrauch"])
        assert _breakdown(kaskade)["Grundlast"]["value"] == pytest.approx(_breakdown(flach)["Grundlast"]["value"])
        assert _breakdown(kaskade)["Werkstatt"]["value"] == pytest.approx(3.0)
        assert not any(i["name"].endswith("übrige Verbraucher") for i in kaskade["verbraucher_breakdown"])
    finally:
        a.close()


def test_the_rest_behind_the_meter_is_meter_minus_devices(monkeypatch, tmp: Path) -> None:
    a, config = _beispielanlage(tmp)
    monkeypatch.setattr(ed, "datetime", _FesteUhr)
    try:
        flow = a.service.compute_flow(_kaskade(a, config, zaehler_kwh=5.0), "day", -1)
        namen = [item["name"] for item in flow["verbraucher_breakdown"]]
        # Wallbox 4 > Werkstatt 5? Nein: Werkstatt 5 steht vorn, dahinter seine Geräte (Wert absteigend), zuletzt der Rest.
        assert namen.index("Werkstatt") < namen.index("Trockner") < namen.index("Waschmaschine")
        assert namen.index("Waschmaschine") < namen.index("Werkstatt, übrige Verbraucher")
        eintraege = _breakdown(flow)
        assert eintraege["Werkstatt, übrige Verbraucher"]["value"] == pytest.approx(2.0)
        assert eintraege["Werkstatt, übrige Verbraucher"]["parent"] == "Werkstatt"
        assert eintraege["Werkstatt"]["kind"] == "meter"
        assert eintraege["Trockner"]["parent"] == "Werkstatt"
        # Wallbox 4 + Werkstatt 5: die Kinder zählen nicht ein zweites Mal
        assert flow["kpi"]["verbrauch"] == pytest.approx(9.0 + _breakdown(flow)["Grundlast"]["value"])
    finally:
        a.close()


def test_the_sankey_hangs_the_devices_behind_the_meter(monkeypatch, tmp: Path) -> None:
    a, config = _beispielanlage(tmp)
    monkeypatch.setattr(ed, "datetime", _FesteUhr)
    try:
        flow = a.service.compute_flow(_kaskade(a, config, zaehler_kwh=5.0), "day", -1)
        quellen = {(link["source"], link["target"]): link["value"] for link in flow["links"]}
        assert quellen[("Haus", "Werkstatt")] == pytest.approx(5.0)
        assert quellen[("Werkstatt", "Trockner")] == pytest.approx(2.0)
        assert quellen[("Werkstatt", "Waschmaschine")] == pytest.approx(1.0)
        assert quellen[("Werkstatt", "Werkstatt, übrige Verbraucher")] == pytest.approx(2.0)
        assert ("Haus", "Trockner") not in quellen and ("Haus", "Waschmaschine") not in quellen
    finally:
        a.close()


def test_devices_above_the_meter_are_reported(monkeypatch, tmp: Path) -> None:
    a, config = _beispielanlage(tmp)
    monkeypatch.setattr(ed, "datetime", _FesteUhr)
    try:
        flow = a.service.compute_flow(_kaskade(a, config, zaehler_kwh=2.0), "day", -1)
        pruefung = next(p for p in flow["quality"]["checks"] if p["label"] == "Zwischenzähler plausibel")
        assert pruefung["ok"] is False
        assert "Werkstatt" in pruefung["detail"] and "3.0 kWh" in pruefung["detail"]
        assert flow["quality"]["plausible"] is False
        # Der Knoten zeigt die Summe der Geräte, damit der Sankey aufgeht
        assert _breakdown(flow)["Werkstatt"]["value"] == pytest.approx(3.0)
    finally:
        a.close()


def test_a_consistent_cascade_adds_no_check(monkeypatch, tmp: Path) -> None:
    a, config = _beispielanlage(tmp)
    monkeypatch.setattr(ed, "datetime", _FesteUhr)
    try:
        flow = a.service.compute_flow(_kaskade(a, config, zaehler_kwh=5.0), "day", -1)
        assert all(p["label"] != "Zwischenzähler plausibel" for p in flow["quality"]["checks"])
    finally:
        a.close()


def test_the_costs_of_the_rest_are_valued_like_every_other_row(monkeypatch, tmp: Path) -> None:
    a, config = _beispielanlage(tmp)
    config["kosten"] = {"preis_netzbezug_fixed": 0.30}
    monkeypatch.setattr(ed, "datetime", _FesteUhr)
    try:
        flow = a.service.compute_flow(_kaskade(a, config, zaehler_kwh=5.0), "day", -1)
        eintraege = _breakdown(flow)
        assert eintraege["Werkstatt"]["kosten"] == pytest.approx(1.50)
        assert eintraege["Werkstatt, übrige Verbraucher"]["kosten"] == pytest.approx(0.60)
        assert eintraege["Trockner"]["kosten"] == pytest.approx(0.60)
    finally:
        a.close()


def test_the_hourly_path_reads_only_the_top_level(monkeypatch, tmp: Path) -> None:
    """Heatmap/Tageslastprofil: Geräte hinter einem Zwischenzähler sind in dessen Wert schon
    enthalten und dürfen nicht zusätzlich in die Verbrauchssumme gehen."""
    a, config = _beispielanlage(tmp)
    monkeypatch.setattr(ed, "datetime", _FesteUhr)
    angefragt: list[str] = []
    echt = ed.query_mod.query_hourly_counter_series

    def spion(data_dir, index, entity_id, *args, **kwargs):
        angefragt.append(entity_id)
        return echt(data_dir, index, entity_id, *args, **kwargs)

    monkeypatch.setattr(ed.query_mod, "query_hourly_counter_series", spion)
    try:
        a.service.compute_heatmap_weekday(_kaskade(a, config, zaehler_kwh=3.0), "month")
        assert "sensor.werkstatt" in angefragt and "sensor.wallbox" in angefragt
        assert "sensor.waschmaschine" not in angefragt and "sensor.trockner" not in angefragt
    finally:
        a.close()


def test_the_structure_allows_exactly_one_level() -> None:
    rows = [
        {"entity_id": "a", "parent": "b"},
        {"entity_id": "b", "parent": "c"},
        {"entity_id": "c"},
    ]
    kinder, kind_ids = ed.cascade_structure(rows)
    # b ist selbst ein Zwischenzähler und gilt deshalb nicht als Kind; a hängt hinter b
    assert kind_ids == {"a"}
    assert [row["entity_id"] for row in kinder["b"]] == ["a"]


@pytest.mark.parametrize("parent", ["gibt-es-nicht", "a"])
def test_an_unknown_or_self_pointing_parent_is_ignored(parent: str) -> None:
    kinder, kind_ids = ed.cascade_structure([{"entity_id": "a", "parent": parent}, {"entity_id": "b"}])
    assert kinder == {} and kind_ids == set()


def test_saving_rejects_pointers_the_form_never_offers() -> None:
    rows = [{"entity_id": "a", "parent": "a"}]
    with pytest.raises(HTTPException) as fehler:
        ed._check_cascade(rows)
    assert fehler.value.status_code == 422
    with pytest.raises(HTTPException):
        ed._check_cascade([{"entity_id": "a", "parent": "x"}])
    with pytest.raises(HTTPException):
        ed._check_cascade([{"entity_id": "a", "parent": "b"}, {"entity_id": "b", "parent": "c"}, {"entity_id": "c"}])
    ed._check_cascade([{"entity_id": "a", "parent": "b"}, {"entity_id": "b"}])
