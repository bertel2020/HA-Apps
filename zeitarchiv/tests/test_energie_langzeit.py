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
from app.storage.cleanup import OUTLIER_RULE_VERSION
from app.storage.index import Index

TZ = ZoneInfo("Europe/Berlin")
NOW = datetime(2026, 8, 20, 12, 0, tzinfo=TZ)


def _monat(jahr: int, monat: int) -> float:
    return datetime(jahr, monat, 1, tzinfo=TZ).timestamp()


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


# -- Monatswerte über alle Jahre (Monatsvergleich, Matrix „Jahre × Monate“) ---------------------


def _monatsreihen(daten: dict):
    """Ersatz für EnergieDashboardService._entity_series: Monatsbuckets je Jahr aus fest vorgegebenen Werten."""
    def reihe(entity_id, range_key, offset, now, read_cache):
        assert range_key == "year"
        jahr = now.year + offset
        return {_monat(j, m): wert for (j, m), wert in daten.get(entity_id, {}).items() if j == jahr}, False
    return reihe


def _archiv(service, entity_id, seit: float) -> None:
    service.index.get_or_create_entity(entity_id, "sensor", "total_increasing", "kWh")
    service.index.set_first_ts_and_add_rows(entity_id, seit, 0)


def _monate_service(service, daten: dict):
    service.entity_series = _monatsreihen(daten)
    return service


def _rolle(daten: dict, schluessel: str) -> dict:
    return next(r for r in daten["roles"] if r["key"] == schluessel)


def test_monatswerte_je_rolle_jahr_und_verbrauch_als_bilanz(service) -> None:
    for eid in ("sensor.pv", "sensor.netz", "sensor.einsp"):
        _archiv(service, eid, _monat(2025, 1))
    daten = {
        "sensor.pv": {(2025, m): 100.0 for m in range(1, 13)} | {(2026, m): 120.0 for m in range(1, 9)},
        "sensor.netz": {(2025, m): 40.0 for m in range(1, 13)} | {(2026, m): 30.0 for m in range(1, 9)},
        "sensor.einsp": {(2025, m): 25.0 for m in range(1, 13)} | {(2026, m): 50.0 for m in range(1, 9)},
    }
    config = _config(netzbezug="sensor.netz", einspeisung="sensor.einsp", erzeuger=[{"entity_id": "sensor.pv"}])
    ergebnis = _monate_service(service, daten).compute_months(config, NOW)
    assert ergebnis["years"] == [2026, 2025]
    assert ergebnis["current"] == {"year": 2026, "month": 8, "day": 20}
    assert [r["key"] for r in ergebnis["roles"]] == ["erzeugung", "einspeisung", "netzbezug", "verbrauch"]
    erz = _rolle(ergebnis, "erzeugung")["values"]
    assert erz["2025"] == [100.0] * 12
    assert erz["2026"] == [120.0] * 8 + [None] * 4          # Zukunft ist leer, nicht 0
    verbrauch = _rolle(ergebnis, "verbrauch")["values"]
    assert verbrauch["2025"][0] == pytest.approx(100.0 + 40.0 - 25.0)
    assert verbrauch["2026"][7] == pytest.approx(120.0 + 30.0 - 50.0)
    assert verbrauch["2026"][8] is None


def test_monate_vor_dem_archivbeginn_sind_leer_statt_null(service) -> None:
    _archiv(service, "sensor.netz", _monat(2025, 3) + 5 * 86400)   # Archiv beginnt am 6. März 2025
    daten = {"sensor.netz": {(2025, m): 10.0 for m in range(3, 13)} | {(2026, m): 12.0 for m in range(1, 9)}}
    ergebnis = _monate_service(service, daten).compute_months(_config(netzbezug="sensor.netz"), NOW)
    werte = _rolle(ergebnis, "netzbezug")["values"]
    assert werte["2025"][:2] == [None, None]          # Januar und Februar: noch kein Archiv
    assert werte["2025"][2] == 10.0                   # der Monat, in dem das Archiv beginnt, zählt
    assert ergebnis["years"] == [2026, 2025]


def test_einspeisung_und_netzbezug_zaehlen_nicht_negativ(service) -> None:
    _archiv(service, "sensor.netz", _monat(2026, 1))
    daten = {"sensor.netz": {(2026, 1): -5.0, (2026, 2): 7.0}}
    werte = _rolle(_monate_service(service, daten).compute_months(_config(netzbezug="sensor.netz"), NOW), "netzbezug")["values"]
    assert werte["2026"][:2] == [0.0, 7.0]


def test_monatswerte_tragen_luecken_und_zaehlersprung_je_monat(service) -> None:
    for eid in ("sensor.pv", "sensor.netz"):
        _archiv(service, eid, _monat(2025, 1))
    service.index.set_config("sensor.pv", outlier_threshold="50")
    service.index.set_cleanup_alltime_stats(
        "sensor.pv",
        {"all": 10, "outliers": 0, "gaps": 2, "duplicates": 0, "repetitions": 0, "counter_decreases": 1},
        "50", OUTLIER_RULE_VERSION,
        daily={"2025-03-04": {"gaps": 40}, "2025-03-20": {"counter_decreases": 1}}, gap_threshold="15",
    )
    daten = {"sensor.pv": {(2025, 3): 10.0}, "sensor.netz": {(2025, 3): 5.0}}
    config = _config(netzbezug="sensor.netz", erzeuger=[{"entity_id": "sensor.pv"}])
    ergebnis = _monate_service(service, daten).compute_months(config, NOW)
    assert _rolle(ergebnis, "erzeugung")["flags"] == {"2025-03": {"gaps": 40, "gaps_typical": 0, "counter_decreases": 1}}
    assert _rolle(ergebnis, "erzeugung")["unchecked"] == 0
    # Der Netzbezug hat noch keine Tageszähler: „nicht geprüft“, nicht „ohne Befund“.
    assert _rolle(ergebnis, "netzbezug")["flags"] == {} and _rolle(ergebnis, "netzbezug")["unchecked"] == 1
    # Der Verbrauch erbt die Befunde seiner Bestandteile.
    assert _rolle(ergebnis, "verbrauch")["flags"] == {"2025-03": {"gaps": 40, "gaps_typical": 0, "counter_decreases": 1}}
    assert _rolle(ergebnis, "verbrauch")["unchecked"] == 1


def _tageszaehler(service, entity_id: str, daily: dict) -> None:
    service.index.set_config(entity_id, outlier_threshold="50")
    service.index.set_cleanup_alltime_stats(
        entity_id,
        {"all": 10, "outliers": 0, "gaps": 0, "duplicates": 0, "repetitions": 0, "counter_decreases": 0},
        "50", OUTLIER_RULE_VERSION, daily=daily, gap_threshold="15",
    )


def test_dauerhafte_luecken_markieren_nicht_jeden_monat(service) -> None:
    """Ein Zähler, der selten meldet, hat in jedem Monat viele Lücken. Markiert wird nur, was deutlich
    über dem üblichen Wert der Rolle liegt, sonst trüge jedes Feld ein Dreieck."""
    _archiv(service, "sensor.netz", _monat(2025, 1))
    tage = {f"2025-{m:02d}-10": {"gaps": 700} for m in range(1, 13)}
    tage["2025-06-10"] = {"gaps": 3000}          # ein Monat mit viermal so vielen Lücken wie sonst
    _tageszaehler(service, "sensor.netz", tage)
    daten = {"sensor.netz": {(2025, m): 5.0 for m in range(1, 13)}}
    ergebnis = _monate_service(service, daten).compute_months(_config(netzbezug="sensor.netz"), NOW)
    assert _rolle(ergebnis, "netzbezug")["flags"] == {"2025-06": {"gaps": 3000, "gaps_typical": 700}}


def test_lueckenmonat_unter_der_mindestzahl_bleibt_unmarkiert(service) -> None:
    _archiv(service, "sensor.netz", _monat(2025, 1))
    _tageszaehler(service, "sensor.netz", {"2025-03-10": {"gaps": 4}})
    daten = {"sensor.netz": {(2025, m): 5.0 for m in range(1, 13)}}
    ergebnis = _monate_service(service, daten).compute_months(_config(netzbezug="sensor.netz"), NOW)
    assert _rolle(ergebnis, "netzbezug")["flags"] == {}


def test_der_anfangsmonat_des_archivs_ist_als_teilmonat_markiert(service) -> None:
    _archiv(service, "sensor.netz", _monat(2025, 3) + 14 * 86400)        # Beginn am 15. März
    _archiv(service, "sensor.pv", _monat(2025, 1))                       # Beginn am 1. Januar
    config = _config(netzbezug="sensor.netz", erzeuger=[{"entity_id": "sensor.pv"}])
    ergebnis = _monate_service(service, {}).compute_months(config, NOW)
    assert _rolle(ergebnis, "netzbezug")["partial_start"] == [2025, 3]
    assert _rolle(ergebnis, "erzeugung")["partial_start"] is None
    # Der Verbrauch hängt an beiden: der später begonnene Zähler bestimmt den Teilmonat.
    assert _rolle(ergebnis, "verbrauch")["partial_start"] == [2025, 3]


def test_einzige_entitaet_einer_rolle_ist_verlinkbar_mehrere_nicht(service) -> None:
    for eid in ("sensor.pv1", "sensor.pv2", "sensor.netz"):
        _archiv(service, eid, _monat(2026, 1))
    config = _config(netzbezug="sensor.netz", erzeuger=[{"entity_id": "sensor.pv1"}, {"entity_id": "sensor.pv2"}])
    ergebnis = _monate_service(service, {}).compute_months(config, NOW)
    assert _rolle(ergebnis, "netzbezug")["entity_id"] == "sensor.netz"
    assert _rolle(ergebnis, "erzeugung")["entity_id"] is None
    assert _rolle(ergebnis, "verbrauch")["entity_id"] is None


def test_months_route_antwortet_409_ohne_einrichtung_und_sonst_mit_den_rollen(client) -> None:
    from app.main import index

    index.set_setting(ed.SETTING_CONFIG, "")
    assert client.get("/energiedashboard/langzeit/months").status_code == 409
    ed._save_config(index, _config(netzbezug="sensor.langzeit_test_netz"))
    try:
        antwort = client.get("/energiedashboard/langzeit/months")
        assert antwort.status_code == 200
        daten = antwort.json()
        assert [r["key"] for r in daten["roles"]] == ["netzbezug", "verbrauch"]
        assert daten["years"][0] == daten["current"]["year"]
    finally:
        index.set_setting(ed.SETTING_CONFIG, "")
