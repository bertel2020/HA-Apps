"""Zähler in Wh liefern im Energiedashboard dieselben Zahlen wie in kWh.

Die Werte liegen in der Einheit der Entität im Archiv; das Dashboard rechnet sie beim Lesen
nach kWh um (energy_units.py). Verglichen wird deshalb dieselbe Anlage einmal in kWh und einmal
in Wh (Zählerstände mal 1000): Kennzahlen, Aufschlüsselungen und die Langzeit-Tabelle müssen
übereinstimmen. Ladezustand (%) und Preise laufen über dieselben Lesewege und dürfen nicht
mitskaliert werden.
"""

from __future__ import annotations

import datetime as _dt
import shutil
import tempfile
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

try:
    import pyarrow  # noqa: F401

    from app import energie_langzeit as lz
    from app import energiedashboard_routes as ed
    from app.energy_units import energy_unit_scale
    from app.storage import hotbuffer
    from app.storage.index import Index

    _PYARROW_AVAILABLE = True
except ImportError:  # pragma: no cover — Umgebung ohne pyarrow
    _PYARROW_AVAILABLE = False

pytestmark = pytest.mark.skipif(not _PYARROW_AVAILABLE, reason="pyarrow fehlt")

TZ = ZoneInfo("Europe/Berlin")
NOW = _dt.datetime(2024, 3, 16, 12, 0, 0, tzinfo=TZ)
TAG = (2024, 3, 15)


def _ts(stunde: float) -> float:
    return _dt.datetime(*TAG, tzinfo=TZ).timestamp() + stunde * 3600


class _FesteUhr(_dt.datetime):
    @classmethod
    def now(cls, tz=None):  # noqa: A003 — bewusst dieselbe Signatur
        return NOW.astimezone(tz) if tz else NOW


@pytest.fixture()
def tmp(tmp_path_factory) -> Path:
    d = Path(tempfile.mkdtemp(prefix="zeitarchiv-edash-wh-", dir=tmp_path_factory.mktemp("edash")))
    yield d
    shutil.rmtree(d, ignore_errors=True)


def _anlage(tmp: Path, einheit: str, faktor: float) -> tuple[ed.EnergieDashboardService, Index, dict]:
    """Beispielanlage; die Zählerstände sind in kWh gedacht und werden mit faktor in die Einheit der
    Entität übersetzt (1 für kWh, 1000 für Wh)."""
    index = Index(tmp / "index.sqlite")
    service = ed.EnergieDashboardService(
        ed.EnergieDashboardDependencies(data_dir=tmp, index=index, tz=TZ, templates=None, app_root_context=None)
    )

    def zaehler(entity_id: str, *werte: tuple[float, float]) -> None:
        index.get_or_create_entity(entity_id, "sensor", "total_increasing", einheit)
        for stunde, stand in werte:
            hotbuffer.append(tmp, entity_id, _ts(stunde), stand * faktor, TZ)
            index.record_write(entity_id, _ts(stunde), stand * faktor)

    zaehler("sensor.netz", (0, 100.0), (12, 103.0), (23, 106.0))
    zaehler("sensor.pv", (0, 500.0), (12, 506.0), (23, 510.0))
    zaehler("sensor.einspeisung", (0, 50.0), (12, 51.5), (23, 53.0))
    zaehler("sensor.wallbox", (0, 30.0), (12, 32.0), (23, 34.0))
    zaehler("sensor.laden", (0, 200.0), (12, 202.0), (23, 205.0))
    zaehler("sensor.entladen", (0, 300.0), (12, 301.0), (23, 303.0))
    # Ladezustand in % bleibt unabhängig von der Energieeinheit der übrigen Rollen.
    index.get_or_create_entity("sensor.soc", "sensor", "measurement", "%")
    for stunde, stand in ((0, 40.0), (12, 55.0), (23, 70.0)):
        hotbuffer.append(tmp, "sensor.soc", _ts(stunde), stand, TZ)
        index.record_write("sensor.soc", _ts(stunde), stand)

    config = ed._empty_config()
    config.update({
        "netzbezug": "sensor.netz",
        "einspeisung": "sensor.einspeisung",
        "erzeuger": [{"entity_id": "sensor.pv", "name": "Dach-PV"}],
        "verbraucher": [{"entity_id": "sensor.wallbox", "name": "Wallbox"}],
        "speicher": [{
            "name": "Speicher", "laden_entity_id": "sensor.laden",
            "entladen_entity_id": "sensor.entladen", "soc_entity_id": "sensor.soc",
        }],
        "hub_name": "Haus",
    })
    return service, index, config


def test_faktor_je_einheit() -> None:
    assert energy_unit_scale("Wh") == pytest.approx(0.001)
    assert energy_unit_scale("kWh") == 1.0
    assert energy_unit_scale("MWh") == pytest.approx(1000.0)
    # Alles andere bleibt unangetastet: Prozent, Preise, keine Einheit.
    assert energy_unit_scale("%") == 1.0
    assert energy_unit_scale("ct/kWh") == 1.0
    assert energy_unit_scale(None) == 1.0


def test_fluss_in_wh_gleich_kwh(monkeypatch, tmp: Path) -> None:
    monkeypatch.setattr(ed, "datetime", _FesteUhr)
    (tmp / "kwh").mkdir()
    (tmp / "wh").mkdir()
    kwh_service, kwh_index, kwh_config = _anlage(tmp / "kwh", "kWh", 1.0)
    wh_service, wh_index, wh_config = _anlage(tmp / "wh", "Wh", 1000.0)
    try:
        erwartet = kwh_service.compute_flow(kwh_config, "day", -1)
        ergebnis = wh_service.compute_flow(wh_config, "day", -1)
        for schluessel, wert in erwartet["kpi"].items():
            if isinstance(wert, float):
                assert ergebnis["kpi"][schluessel] == pytest.approx(wert, rel=1e-6, abs=1e-6), schluessel
        assert ergebnis["kpi"]["netzbezug"] == pytest.approx(6.0)
        assert ergebnis["kpi"]["erzeugung"] == pytest.approx(10.0)
        assert ergebnis["kpi"]["einspeisung"] == pytest.approx(3.0)
        anteile = {i["name"]: i["value"] for i in ergebnis["verbraucher_breakdown"]}
        assert anteile["Wallbox"] == pytest.approx(4.0)
    finally:
        kwh_index.close()
        wh_index.close()


def test_ladezustand_wird_nicht_skaliert(monkeypatch, tmp: Path) -> None:
    monkeypatch.setattr(ed, "datetime", _FesteUhr)
    (tmp / "wh").mkdir()
    service, index, config = _anlage(tmp / "wh", "Wh", 1000.0)
    try:
        serie, _ = service._entity_series("sensor.soc", "day", -1, _FesteUhr.now(TZ), ed.query_mod.QueryReadCache())  # noqa: SLF001
        assert max(serie.values()) < 100.0  # Prozent, nicht durch 1000 geteilt
        assert max(serie.values()) > 1.0
    finally:
        index.close()


def test_einheitenpruefung_meldet_wh_nicht_mehr(tmp: Path) -> None:
    (tmp / "wh").mkdir()
    service, index, _config = _anlage(tmp / "wh", "Wh", 1000.0)
    index.get_or_create_entity("sensor.leistung", "sensor", "measurement", "W")
    try:
        meta = service._check_entity_metadata([  # noqa: SLF001
            ("sensor.netz", "Netzbezug"), ("sensor.leistung", "Leistung"),
        ])
        assert meta["unit_issues"] == ["Leistung (W)"]
    finally:
        index.close()


def test_langzeit_tabelle_in_wh_gleich_kwh(tmp: Path) -> None:
    jetzt = _dt.datetime(2026, 8, 20, 12, 0, tzinfo=TZ)

    def tag(day: int) -> float:
        return _dt.datetime(2026, 8, day, tzinfo=TZ).timestamp()

    ergebnisse = []
    for name, einheit, faktor in (("kwh", "kWh", 1.0), ("wh", "Wh", 1000.0)):
        pfad = tmp / name
        pfad.mkdir()
        index = Index(pfad / "index.sqlite")
        service = lz.EnergieLangzeitService(pfad, index, TZ)
        for entity_id, start, ende in (("sensor.netz", 100.0, 106.0), ("sensor.pv", 500.0, 510.0)):
            index.get_or_create_entity(entity_id, "sensor", "total_increasing", einheit)
            for ts, wert in ((tag(2), start), (tag(12), ende)):
                hotbuffer.append(pfad, entity_id, ts, wert * faktor, TZ)
                index.record_write(entity_id, ts, wert * faktor)
        config = {**ed._empty_config(), "netzbezug": "sensor.netz",
                  "erzeuger": [{"entity_id": "sensor.pv", "name": "PV"}]}
        daten = service.compute_table(config, lz.TableRequest(columns=["monat"], vorjahr=False), jetzt)
        ergebnisse.append({r["key"]: r["cells"]["monat"]["cur"] for r in daten["rows"]})
        index.close()
    assert ergebnisse[1]["netzbezug"] == pytest.approx(6.0)
    assert ergebnisse[1]["erzeugung"] == pytest.approx(10.0)
    assert ergebnisse[0] == pytest.approx(ergebnisse[1])
