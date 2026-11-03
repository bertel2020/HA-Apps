"""Energiedashboard, Reiter „Langzeit“ (ROADMAP 1.45, P1): Vergleichstabelle aus Zählersummen.

Die Daten liegen im Hot Buffer des laufenden Monats (wie in test_wetter.py): Der Vorjahresmonat ist
dort leer, geprüft werden also die Summen je Rolle, die Bilanz des Verbrauchs, der Stichtag und die
Regel „fehlende Daten sind kein 0“.
"""

from __future__ import annotations

import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app import energie_langzeit as lz
from app import energiedashboard_routes as ed
from app.storage import hotbuffer
from app.storage.index import Index

TZ = ZoneInfo("Europe/Berlin")
NOW = datetime(2026, 8, 20, 12, 0, tzinfo=TZ)


def _ts(day: int, hour: int = 0) -> float:
    return datetime(2026, 8, day, hour, tzinfo=TZ).timestamp()


@pytest.fixture()
def service():
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-langzeit-test-"))
    index = Index(tmp / "index.sqlite")
    yield lz.EnergieLangzeitService(tmp, index, TZ)
    index.close()
    shutil.rmtree(tmp, ignore_errors=True)


def _zaehler(service, entity_id, *punkte):
    service.index.get_or_create_entity(entity_id, "sensor", "total_increasing", "kWh")
    for ts, wert in punkte:
        hotbuffer.append(service.data_dir, entity_id, ts, wert, TZ)
        service.index.record_write(entity_id, ts, wert)


def _config(**rollen) -> dict:
    return {**ed._empty_config(), **rollen}


def _anlage(service, **extra) -> dict:
    """Netzbezug +6, Erzeugung +10, Einspeisung +3 im August (Tag 2 bis 12)."""
    _zaehler(service, "sensor.netz", (_ts(2), 100.0), (_ts(12), 106.0))
    _zaehler(service, "sensor.pv", (_ts(2), 500.0), (_ts(12), 510.0))
    _zaehler(service, "sensor.einsp", (_ts(2), 50.0), (_ts(12), 53.0))
    return _config(
        netzbezug="sensor.netz", einspeisung="sensor.einsp",
        erzeuger=[{"entity_id": "sensor.pv", "name": "PV"}], **extra,
    )


def _zelle(daten: dict, zeile: str, spalte: str) -> dict:
    return next(r for r in daten["rows"] if r["key"] == zeile)["cells"][spalte]


def test_summen_je_rolle_und_verbrauch_als_bilanz(service) -> None:
    config = _anlage(service)
    daten = service.compute_table(config, lz.TableRequest(columns=["monat"], vorjahr=False), NOW)
    assert [r["key"] for r in daten["rows"]] == ["erzeugung", "einspeisung", "netzbezug", "verbrauch"]
    assert _zelle(daten, "erzeugung", "monat")["cur"] == pytest.approx(10.0)
    assert _zelle(daten, "einspeisung", "monat")["cur"] == pytest.approx(3.0)
    assert _zelle(daten, "netzbezug", "monat")["cur"] == pytest.approx(6.0)
    assert _zelle(daten, "verbrauch", "monat")["cur"] == pytest.approx(13.0)  # 10 + 6 − 3
    assert "vj" not in _zelle(daten, "verbrauch", "monat")


def test_speicher_geht_in_die_bilanz_ein(service) -> None:
    _zaehler(service, "sensor.laden", (_ts(2), 200.0), (_ts(12), 205.0))
    _zaehler(service, "sensor.entladen", (_ts(2), 300.0), (_ts(12), 303.0))
    config = _anlage(service, speicher=[{
        "name": "Speicher", "laden_entity_id": "sensor.laden", "entladen_entity_id": "sensor.entladen",
    }])
    daten = service.compute_table(config, lz.TableRequest(columns=["monat"], vorjahr=False), NOW)
    assert _zelle(daten, "verbrauch", "monat")["cur"] == pytest.approx(11.0)  # 10 + 6 + 3 − 3 − 5


def test_ohne_werte_im_fenster_ist_die_zelle_leer_statt_null(service) -> None:
    config = _anlage(service)
    daten = service.compute_table(config, lz.TableRequest(columns=["monat"], vorjahr=True), NOW)
    zelle = _zelle(daten, "netzbezug", "monat")
    assert zelle["vj"] is None  # kein Wert im Vorjahresmonat, nicht 0
    assert _zelle(daten, "verbrauch", "monat")["vj"] is None


def test_zeilen_ohne_zugeordnete_rolle_fehlen(service) -> None:
    _zaehler(service, "sensor.pv", (_ts(2), 500.0), (_ts(12), 510.0))
    config = _config(erzeuger=[{"entity_id": "sensor.pv", "name": "PV"}])
    daten = service.compute_table(config, lz.TableRequest(columns=["monat"], vorjahr=False), NOW)
    assert [r["key"] for r in daten["rows"]] == ["erzeugung", "verbrauch"]
    assert _zelle(daten, "verbrauch", "monat")["cur"] == pytest.approx(10.0)


def test_stichtag_begrenzt_das_fenster_auf_das_ende_dieses_tages(service) -> None:
    _zaehler(service, "sensor.netz", (_ts(2), 100.0), (_ts(8), 104.0), (_ts(15), 111.0))
    config = _config(netzbezug="sensor.netz")
    daten = service.compute_table(
        config, lz.TableRequest(columns=["monat"], vorjahr=False, at="2026-08-10"), NOW,
    )
    assert daten["at"] == "2026-08-10"
    assert _zelle(daten, "netzbezug", "monat")["cur"] == pytest.approx(4.0)  # die 7 kWh vom 15. fehlen noch
    spalte = daten["columns"][0]
    assert datetime.fromtimestamp(spalte["cur"]["end"], TZ) == datetime(2026, 8, 11, tzinfo=TZ)


def test_ungueltiger_stichtag_und_unbekannte_spalten(service) -> None:
    from fastapi import HTTPException

    config = _anlage(service)
    with pytest.raises(HTTPException) as fehler:
        service.compute_table(config, lz.TableRequest(at="quatsch"), NOW)
    assert fehler.value.status_code == 400
    daten = service.compute_table(config, lz.TableRequest(columns=["gibt_es_nicht", "jahr"]), NOW)
    assert [c["key"] for c in daten["columns"]] == ["jahr"]


def test_bilanz_laesst_nichts_vortaeuschen() -> None:
    teile = {"erzeugung": None, "netzbezug": None, "einspeisung": 3.0, "entladung": 1.0, "ladung": 0.0}
    assert lz.balance(teile) is None  # weder Erzeugung noch Bezug: kein Verbrauch
    # Bezug und Einspeisung zählen nicht negativ, wie in den Kacheln und im Monatsverlauf.
    assert lz.balance({**teile, "netzbezug": -2.0, "erzeugung": 5.0}) == pytest.approx(5.0 + 0.0 + 1.0 - 3.0)


def test_rollen_aus_der_konfiguration() -> None:
    config = _config(
        netzbezug="sensor.netz", einspeisung="sensor.einsp",
        erzeuger=[{"entity_id": "sensor.pv"}, {"name": "ohne Entität"}],
        speicher=[{"laden_entity_id": "sensor.laden"}, {"entladen_entity_id": "sensor.entladen"}],
    )
    rollen = lz.role_entities(config)
    assert rollen == {
        "erzeugung": ["sensor.pv"], "einspeisung": ["sensor.einsp"], "netzbezug": ["sensor.netz"],
        "entladung": ["sensor.entladen"], "ladung": ["sensor.laden"],
    }
    assert lz.is_configured(config) and not lz.is_configured(_config())


def test_route_antwortet_409_ohne_einrichtung_und_sonst_mit_tabelle(client) -> None:
    from app.main import index

    index.set_setting(ed.SETTING_CONFIG, "")
    assert client.post("/energiedashboard/langzeit/table", json={}).status_code == 409
    ed._save_config(index, _config(netzbezug="sensor.langzeit_test_netz"))
    try:
        antwort = client.post("/energiedashboard/langzeit/table", json={"columns": ["monat"], "vorjahr": False})
        assert antwort.status_code == 200
        daten = antwort.json()
        assert [c["key"] for c in daten["columns"]] == ["monat"]
        assert [r["key"] for r in daten["rows"]] == ["netzbezug", "verbrauch"]
        assert daten["rows"][0]["cells"]["monat"]["cur"] is None  # Entität ohne Daten, kein 0
    finally:
        index.set_setting(ed.SETTING_CONFIG, "")
