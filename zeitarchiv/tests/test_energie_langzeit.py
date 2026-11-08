"""Energiedashboard, Reiter „Langzeit“ (ROADMAP 1.45, P1): Vergleichstabelle aus Zählersummen.

Die Daten liegen im Hot Buffer des laufenden Monats (wie in test_wetter.py): Der Vorjahresmonat ist
dort leer, geprüft werden also die Summen je Rolle, die Bilanz des Verbrauchs, der Stichtag und die
Regel „fehlende Daten sind kein 0“.
"""

from __future__ import annotations

import json
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
    assert [r["key"] for r in daten["rows"]] == [
        "erzeugung", "einspeisung", "netzbezug", "verbrauch", "eigenverbrauch", "autarkie",
    ]
    assert _zelle(daten, "erzeugung", "monat")["cur"] == pytest.approx(10.0)
    assert _zelle(daten, "einspeisung", "monat")["cur"] == pytest.approx(3.0)
    assert _zelle(daten, "netzbezug", "monat")["cur"] == pytest.approx(6.0)
    assert _zelle(daten, "verbrauch", "monat")["cur"] == pytest.approx(13.0)  # 10 + 6 − 3
    assert "vj" not in _zelle(daten, "verbrauch", "monat")
    # Quoten wie in compute_flow: Eigenverbrauch = (Erzeugung − Einspeisung) / Erzeugung, Autarkie = 1 − Bezug / Verbrauch.
    assert _zelle(daten, "eigenverbrauch", "monat")["cur"] == pytest.approx(70.0)
    assert _zelle(daten, "autarkie", "monat")["cur"] == pytest.approx(53.8)
    assert next(r for r in daten["rows"] if r["key"] == "autarkie")["kind"] == "pct"


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
    assert [r["key"] for r in daten["rows"]] == ["erzeugung", "verbrauch", "eigenverbrauch", "autarkie"]
    assert _zelle(daten, "verbrauch", "monat")["cur"] == pytest.approx(10.0)
    assert _zelle(daten, "eigenverbrauch", "monat")["cur"] == pytest.approx(100.0)   # ohne Einspeisung-Rolle: alles selbst verbraucht
    assert _zelle(daten, "autarkie", "monat")["cur"] is None                          # ohne Netzbezug-Rolle kein Autarkiegrad


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


def test_tabelle_kennt_heute_und_gestern_wie_das_wetterdashboard(service) -> None:
    _zaehler(service, "sensor.netz", (_ts(2), 100.0), (_ts(8), 104.0), (_ts(15), 111.0))
    config = _config(netzbezug="sensor.netz")
    assert lz.COLUMN_KEYS == ("heute", "gestern", "monat", "vormonat", "jahr")
    daten = service.compute_table(config, lz.TableRequest(columns=["jahr", "gestern", "heute"], vorjahr=True), NOW)
    spalten = {c["key"]: c for c in daten["columns"]}
    assert [c["key"] for c in daten["columns"]] == ["heute", "gestern", "jahr"]       # in der Reihenfolge der Spaltenschlüssel
    heute, gestern = spalten["heute"], spalten["gestern"]
    # Heute zählt vom Tagesbeginn bis jetzt, Gestern den ganzen Vortag; das Vorjahr ist derselbe Tag des Vorjahres.
    assert heute["partial"] is True and gestern["partial"] is False
    assert datetime.fromtimestamp(heute["cur"]["start"], TZ) == datetime(2026, 8, 20, tzinfo=TZ)
    assert datetime.fromtimestamp(heute["cur"]["end"], TZ) == NOW
    assert datetime.fromtimestamp(heute["vj"]["end"], TZ) == datetime(2025, 8, 20, 12, tzinfo=TZ)
    assert datetime.fromtimestamp(gestern["cur"]["start"], TZ) == datetime(2026, 8, 19, tzinfo=TZ)
    assert datetime.fromtimestamp(gestern["cur"]["end"], TZ) == datetime(2026, 8, 20, tzinfo=TZ)
    assert datetime.fromtimestamp(gestern["vj"]["start"], TZ) == datetime(2025, 8, 19, tzinfo=TZ)


def test_tabelle_laesst_heute_und_gestern_ohne_wunsch_weg(service) -> None:
    # Ohne Angabe bleiben es Monat, Vormonat und Jahr; Heute und Gestern gibt es nur auf Wunsch.
    assert lz.TableRequest().columns == ["monat", "vormonat", "jahr"]
    config = _anlage(service)
    daten = service.compute_table(config, lz.TableRequest(), NOW)
    assert [c["key"] for c in daten["columns"]] == ["monat", "vormonat", "jahr"]


def test_stichtag_verschiebt_heute_und_gestern_auf_den_gewaehlten_tag(service) -> None:
    config = _anlage(service)
    daten = service.compute_table(config, lz.TableRequest(columns=["heute", "gestern"], vorjahr=False, at="2026-08-10"), NOW)
    spalten = {c["key"]: c for c in daten["columns"]}
    assert datetime.fromtimestamp(spalten["heute"]["cur"]["start"], TZ) == datetime(2026, 8, 10, tzinfo=TZ)
    assert datetime.fromtimestamp(spalten["heute"]["cur"]["end"], TZ) == datetime(2026, 8, 11, tzinfo=TZ)
    assert datetime.fromtimestamp(spalten["gestern"]["cur"]["start"], TZ) == datetime(2026, 8, 9, tzinfo=TZ)


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
        assert [r["key"] for r in daten["rows"]] == ["netzbezug", "verbrauch", "autarkie"]
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


# -- Speicher, Quoten und PV wetterbereinigt (P3) -------------------------------------------------


def test_speicherzeilen_zeigen_ladung_und_entladung_neutral(service) -> None:
    _zaehler(service, "sensor.laden", (_ts(2), 200.0), (_ts(12), 205.0))
    _zaehler(service, "sensor.entladen", (_ts(2), 300.0), (_ts(12), 303.0))
    config = _anlage(service, speicher=[{
        "name": "Speicher", "laden_entity_id": "sensor.laden", "entladen_entity_id": "sensor.entladen",
    }])
    daten = service.compute_table(config, lz.TableRequest(columns=["monat"], vorjahr=False), NOW)
    assert _zelle(daten, "ladung", "monat")["cur"] == pytest.approx(5.0)
    assert _zelle(daten, "entladung", "monat")["cur"] == pytest.approx(3.0)
    assert next(r for r in daten["rows"] if r["key"] == "ladung")["better"] is None   # mehr Ladung ist weder gut noch schlecht
    # Die Quoten rechnen mit dem Speicher: Verbrauch 11 (10 + 6 + 3 − 3 − 5), Autarkie 1 − 6 / 11.
    assert _zelle(daten, "autarkie", "monat")["cur"] == pytest.approx(45.5)


def _strahlung(service, entity_id: str, watt: float, von_tag: int, bis_tag: int) -> None:
    """Stündliche Messwerte W/m² vom Beginn des ersten bis zum Ende des letzten Tages."""
    service.index.get_or_create_entity(entity_id, "sensor", "measurement", "W/m²")
    for tag in range(von_tag, bis_tag + 1):
        for stunde in range(24):
            ts = _ts(tag, stunde)
            hotbuffer.append(service.data_dir, entity_id, ts, watt, TZ)
            service.index.record_write(entity_id, ts, watt)


def _pv_anlage(service, kwp: float = 5.0) -> dict:
    _zaehler(service, "sensor.pv", (_ts(2), 500.0), (_ts(12), 566.0))          # 66 kWh Ertrag
    return _config(erzeuger=[{"entity_id": "sensor.pv", "name": "PV", "kwp": kwp}])


def test_wetterbereinigte_zeilen_rechnen_ertrag_je_kwp_und_einstrahlung(service) -> None:
    config = _pv_anlage(service)
    _strahlung(service, "sensor.strahlung", 500.0, 2, 12)                       # 264 h mit 500 W/m² = 132 kWh/m²
    service.radiation_entity = lambda: "sensor.strahlung"
    daten = service.compute_table(
        config, lz.TableRequest(columns=["monat"], vorjahr=False, at="2026-08-12"), NOW,
    )
    assert _zelle(daten, "spezifisch", "monat")["cur"] == pytest.approx(13.2)   # 66 kWh / 5 kWp
    assert _zelle(daten, "einstrahlung", "monat")["cur"] == pytest.approx(10.0)  # 66 / (5 × 132)
    assert next(r for r in daten["rows"] if r["key"] == "spezifisch")["kind"] == "spec"


def test_zu_wenig_einstrahlungswerte_ergeben_keinen_vergleich(service) -> None:
    config = _pv_anlage(service)
    _strahlung(service, "sensor.strahlung", 500.0, 2, 3)                        # nur 48 von 288 Stunden
    service.radiation_entity = lambda: "sensor.strahlung"
    daten = service.compute_table(
        config, lz.TableRequest(columns=["monat"], vorjahr=False, at="2026-08-12"), NOW,
    )
    assert _zelle(daten, "spezifisch", "monat")["cur"] == pytest.approx(13.2)   # der Ertrag je kWp braucht keine Strahlung
    assert _zelle(daten, "einstrahlung", "monat")["cur"] is None               # Lücken würden das Ergebnis verfälschen


def test_wetterbereinigte_zeilen_brauchen_leistung_und_strahlung(service) -> None:
    ohne_kwp = _pv_anlage(service, kwp=0.0)
    daten = service.compute_table(ohne_kwp, lz.TableRequest(columns=["monat"], vorjahr=False), NOW)
    assert "spezifisch" not in [r["key"] for r in daten["rows"]]
    mit_kwp = _pv_anlage(service)
    daten = service.compute_table(mit_kwp, lz.TableRequest(columns=["monat"], vorjahr=False), NOW)
    keys = [r["key"] for r in daten["rows"]]
    assert "spezifisch" in keys and "einstrahlung" not in keys                   # ohne Strahlungs-Entität nur der Ertrag je kWp


def test_nur_erzeuger_mit_leistung_gehen_in_die_wetterbereinigung_ein() -> None:
    config = _config(erzeuger=[
        {"entity_id": "sensor.a", "kwp": 9.8}, {"entity_id": "sensor.b"}, {"entity_id": "sensor.c", "kwp": "5"},
        {"entity_id": "sensor.d", "kwp": 0},
    ])
    assert lz.pv_entities(config) == [("sensor.a", 9.8), ("sensor.c", 5.0)]


def test_anlagenleistung_wird_in_der_einrichtung_je_erzeuger_gespeichert(client) -> None:
    from app.main import index

    index.set_setting(ed.SETTING_CONFIG, "")
    antwort = client.post("/energiedashboard/setup", data={
        "netzbezug": "sensor.setup_netz",
        "erzeuger_entity_id": ["sensor.setup_pv1", "sensor.setup_pv2", "sensor.setup_pv3"],
        "erzeuger_name": ["Dach", "Balkon", "Garage"],
        "erzeuger_kwp": ["9,8", "", "0"],
    })
    try:
        assert antwort.status_code == 200
        erzeuger = ed._load_config(index)["erzeuger"]
        assert erzeuger[0] == {"entity_id": "sensor.setup_pv1", "name": "Dach", "kwp": 9.8}
        assert "kwp" not in erzeuger[1] and "kwp" not in erzeuger[2]            # leer oder nicht positiv: nicht angegeben
    finally:
        index.set_setting(ed.SETTING_CONFIG, "")


def test_kenntage_schwellen_werden_im_setup_gespeichert(client) -> None:
    from app.main import index

    index.set_setting(ed.SETTING_CONFIG, "")
    antwort = client.post("/energiedashboard/setup", data={
        "netzbezug": "sensor.setup_netz",
        "kt_autark": "95", "kt_sonnig": "4,5", "kt_truebe": "", "kt_stark": "5", "kt_schwach": "10", "kt_einsp": "30",
    })
    try:
        assert antwort.status_code == 200
        # Gespeichert wird nur, was gültig ist und von der Vorgabe abweicht: leer, außerhalb der Grenzen und Vorgabe fallen weg.
        assert ed._load_config(index)["kenntage"] == {"autark": 95.0, "sonnig": 4.5, "einspeisung": 30.0}
        assert 'name="kt_autark"' in client.get("/energiedashboard/setup").text
        assert 'name="kt_einsp"' in client.get("/energiedashboard/setup").text
    finally:
        index.set_setting(ed.SETTING_CONFIG, "")


def test_kalender_route_lehnt_unbekannte_kenntage_ab(client) -> None:
    from app.main import index

    index.set_setting(ed.SETTING_CONFIG, json.dumps({**ed._empty_config(), "netzbezug": "sensor.setup_netz"}))
    try:
        assert client.get("/energiedashboard/langzeit/kenntage?field=nope&year=2025").status_code == 422
        antwort = client.get("/energiedashboard/langzeit/kenntage?field=fast_autark&year=2025")
        assert antwort.status_code == 200 and antwort.json()["days"] == []
    finally:
        index.set_setting(ed.SETTING_CONFIG, "")


def test_abgewaehlte_tabellenbloecke_entfallen_energie_bleibt(service) -> None:
    config = _config(
        netzbezug="sensor.netz", einspeisung="sensor.einsp", erzeuger=[{"entity_id": "sensor.pv", "kwp": 9.8}],
        speicher=[{"laden_entity_id": "sensor.lad", "entladen_entity_id": "sensor.ent"}],
    )
    roles = lz.role_entities(config)
    alle = {r["key"] for r in service.visible_rows(config, roles)}
    assert {"eigenverbrauch", "autarkie", "ladung", "entladung", "spezifisch"} <= alle

    config["langzeit_ausblenden"] = ["quoten", "speicher", "pv", "energie", "unbekannt"]
    rest = {r["key"] for r in service.visible_rows(config, roles)}
    assert rest == {"erzeugung", "einspeisung", "netzbezug", "verbrauch"}   # „energie“ lässt sich nicht abwählen
    assert lz.hidden_blocks(config) == {"quoten", "speicher", "pv"}
    assert lz.hidden_blocks(_config(langzeit_ausblenden="kaputt")) == set()


def test_bereiche_des_reiters_werden_im_setup_gespeichert_und_nicht_gerendert(client) -> None:
    from app.main import index

    index.set_setting(ed.SETTING_CONFIG, "")
    try:
        alles = client.post("/energiedashboard/setup", data={
            "netzbezug": "sensor.setup_netz",
            "langzeit_zeigen": ["quoten", "speicher", "pv", "verbraucher", "jahresbilanz", "monatsvergleich", "matrix", "rekorde", "kenntage"],
        })
        assert ed._load_config(index)["langzeit_ausblenden"] == []
        assert 'x-ref="monthlyChart"' in alles.text and 'x-ref="kenntage"' in alles.text and 'x-ref="jbBars"' in alles.text

        wenig = client.post("/energiedashboard/setup", data={
            "netzbezug": "sensor.setup_netz", "langzeit_zeigen": ["quoten", "rekorde"],
        })
        assert ed._load_config(index)["langzeit_ausblenden"] == [
            "speicher", "pv", "jahresbilanz", "monatsvergleich", "matrix", "kenntage", "verbraucher",
        ]
        for ref in ('x-ref="monthlyChart"', 'x-ref="matrixTable"', 'x-ref="kenntage"', 'x-ref="jbBars"'):
            assert ref not in wenig.text, ref
        assert "el-rec-big" in wenig.text                                  # die Rekorde sind angehakt geblieben
        assert 'x-ref="table"' in wenig.text                              # die Tabelle gibt es immer
        assert '"monatsvergleich"' in wenig.text                          # das Frontend lädt die abgewählten Daten nicht
    finally:
        index.set_setting(ed.SETTING_CONFIG, "")


def test_reiter_uebersicht_vergleich_langzeit_und_wohin_die_karten_gehoeren(client) -> None:
    from app.main import index

    index.set_setting(ed.SETTING_CONFIG, "")
    try:
        # Ohne Verbraucher: drei Reiter, die Vergleichstabelle steht im Reiter „Vergleich“, nicht in der Übersicht oder in der Langzeit.
        ed._save_config(index, _config(netzbezug="sensor.setup_netz"))
        ohne = client.get("/energiedashboard").text
        assert ohne.index('id="ed-tab-over"') < ohne.index('id="ed-tab-cmp"') < ohne.index('id="ed-tab-long"')
        assert 'id="ed-tab-cons"' not in ohne and "energieVerbraucher" not in ohne
        assert "energieVergleich({" in ohne and '"consumers": false' in ohne
        assert 'x-ref="sharesTable"' not in ohne and 'x-ref="rank"' not in ohne          # keine Aufteilung ohne Verbraucher
        assert 'x-ref="shares"' not in ohne
        übersicht = ohne[ohne.index('id="ed-panel-over"'):ohne.index('id="ed-panel-cmp"')]
        assert 'x-ref="table"' not in übersicht and "edash-cmp-link" in übersicht        # nur der Link auf den Reiter
        vergleich = ohne[ohne.index('id="ed-panel-cmp"'):ohne.index('id="ed-panel-long"')]
        assert 'x-ref="table"' in vergleich and "dashStichtag" in vergleich              # Tabelle mit Stichtag-Leiste
        langzeit = ohne[ohne.index('id="ed-panel-long"'):]
        assert 'x-ref="table"' not in langzeit
        for karte in ('x-ref="kenntage"', 'x-ref="matrixTable"', 'x-ref="monthlyChart"'):
            assert karte in langzeit, karte
        assert "Was hat sich verändert?" not in ohne                       # die Karte gibt es nicht mehr
        # Mit Verbrauchern kommen Filter, Gruppe und „Aufteilung des Verbrauchs“ in den Reiter „Vergleich“.
        ed._save_config(index, _config(netzbezug="sensor.setup_netz", verbraucher=[{"entity_id": "sensor.waerme", "name": "Wärme"}]))
        mit = client.get("/energiedashboard").text
        assert '"consumers": true' in mit and 'x-ref="shares"' in mit and 'x-ref="sharesLegend"' in mit
        assert "setFilter('cons')" in mit and "wt-yearbar" not in mit and "Summen oder Verbraucher" not in mit
        # Das Setup blendet die Gruppe samt Aufteilung aus.
        ed._save_config(index, _config(netzbezug="sensor.setup_netz", verbraucher=[{"entity_id": "sensor.waerme", "name": "Wärme"}], langzeit_ausblenden=["verbraucher"]))
        weg = client.get("/energiedashboard").text
        assert 'x-ref="shares"' not in weg and "setFilter('cons')" not in weg
    finally:
        index.set_setting(ed.SETTING_CONFIG, "")


# -- Rekorde, Kenntage und Veränderungen (P4) -----------------------------------------------------


def _tagesreihen(monkeypatch, daten: dict) -> None:
    """Ersatz für query_daily_series: Tageswerte je Entität aus {entity_id: {date: kWh}}."""
    from types import SimpleNamespace

    def reihe(data_dir, index, entity_id, start, end, tz, now_local, read_cache=None):
        return [
            SimpleNamespace(day_start=datetime(tag.year, tag.month, tag.day, tzinfo=tz).timestamp(), value=wert)
            for tag, wert in sorted(daten.get(entity_id, {}).items())
            if start <= datetime(tag.year, tag.month, tag.day, tzinfo=tz) < end
        ]
    monkeypatch.setattr(lz.query_mod, "query_daily_series", reihe)


def _tag(offset: int):
    from datetime import date, timedelta

    return date(2025, 7, 1) + timedelta(days=offset)


def _zwei_zaehler(service, seit: float):
    _archiv(service, "sensor.pv", seit)
    _archiv(service, "sensor.netz", seit)
    return _config(netzbezug="sensor.netz", erzeuger=[{"entity_id": "sensor.pv"}])


def test_tag_nach_einem_fehlenden_tag_zaehlt_nicht(service, monkeypatch) -> None:
    config = _zwei_zaehler(service, _monat(2026, 8))
    pv = {_tag(0).replace(year=2026, month=8, day=d): 10.0 for d in range(1, 13) if d != 5}
    netz = {tag: 4.0 for tag in pv}
    _tagesreihen(monkeypatch, {"sensor.pv": pv, "sensor.netz": netz})
    tage = [d["date"].day for d in service.usable_days(config, NOW)]
    assert 5 not in tage and 6 not in tage          # der fehlende Tag und der Tag danach, der sonst die Differenz erbte
    assert tage[:4] == [1, 2, 3, 4] and 7 in tage


def test_rekorde_erst_ab_einem_jahr_archiv_und_der_fruehere_tag_bei_gleichstand(service, monkeypatch) -> None:
    config = _zwei_zaehler(service, _monat(2025, 7))
    tage = [_tag(i) for i in range(400)]                    # 1.7.2025 bis 4.8.2026
    pv = {tag: 5.0 for tag in tage}
    pv[tage[100]] = 40.0
    pv[tage[300]] = 40.0                                    # Gleichstand: der frühere zählt
    netz = {tag: 3.0 for tag in tage}
    netz[tage[200]] = 20.0
    _tagesreihen(monkeypatch, {"sensor.pv": pv, "sensor.netz": netz})
    ergebnis = service.compute_records(config, NOW)
    assert ergebnis["enough"] is True and ergebnis["since"] == "2025-07-01"
    rekorde = {r["key"]: r for r in ergebnis["records"]}
    assert rekorde["erzeugung"]["value"] == 40.0 and rekorde["erzeugung"]["date"] == tage[100].isoformat()
    assert rekorde["erzeugung"]["entity_id"] == "sensor.pv"
    assert rekorde["netzbezug"]["value"] == 20.0 and rekorde["netzbezug"]["date"] == tage[200].isoformat()
    assert rekorde["verbrauch"]["entity_id"] is None        # zusammengesetzt: kein Link
    assert rekorde["erzeugung"]["offset"] == (tage[100] - NOW.date()).days
    kurz = service.compute_records(config, datetime(2025, 12, 1, tzinfo=TZ))
    assert kurz["enough"] is False and kurz["records"] == []   # unter einem Jahr gibt es keine Rekorde


def test_zusatzrekorde_bester_autarkiemonat_und_laengste_serie_fast_autarker_tage(service, monkeypatch) -> None:
    config = _zwei_zaehler(service, _monat(2025, 7))
    tage = [_tag(i) for i in range(400)]                    # 1.7.2025 bis 4.8.2026
    pv = {tag: 5.0 for tag in tage}
    netz = {tag: 3.0 for tag in tage}                       # Verbrauch 8, Bezug 3: weit von „fast autark“ entfernt
    for tag in tage:
        if tag.year == 2025 and tag.month == 7:
            pv[tag], netz[tag] = 20.0, 0.5                  # ganzer Juli 2025: Autarkie 97,6 %, jeder Tag fast autark
        if tag.year == 2025 and tag.month == 10 and tag.day <= 5:
            pv[tag], netz[tag] = 20.0, 0.5                  # kürzere Folge im Oktober
    _tagesreihen(monkeypatch, {"sensor.pv": pv, "sensor.netz": netz})
    rekorde = {r["key"]: r for r in service.compute_records(config, NOW)["records"]}
    monat = rekorde["autarkie_monat"]
    assert monat["unit"] == "pct" and monat["span"] == "month" and monat["date"] == "2025-07-01"
    assert monat["value"] == pytest.approx(97.6, abs=0.1) and monat["entity_id"] is None
    serie = rekorde["autark_serie"]
    assert serie["value"] == 31 and serie["unit"] == "days"
    assert serie["date"] == "2025-07-01" and serie["date_end"] == "2025-07-31"


def test_serie_bricht_an_einem_fehlenden_tag_und_der_unvollstaendige_monat_zaehlt_nicht(service, monkeypatch) -> None:
    config = _zwei_zaehler(service, _monat(2025, 7))
    tage = [_tag(i) for i in range(400)]
    pv = {tag: 20.0 for tag in tage}
    netz = {tag: 0.5 for tag in tage}
    del pv[_tag(10)], netz[_tag(10)]                       # 11.7.2025 fehlt: auch der Tag danach zählt nicht
    _tagesreihen(monkeypatch, {"sensor.pv": pv, "sensor.netz": netz})
    rekorde = {r["key"]: r for r in service.compute_records(config, NOW)["records"]}
    assert rekorde["autark_serie"]["date"] == "2025-07-13"                 # die Folge vor der Lücke (10 Tage) ist kürzer als die danach
    ende = datetime.fromisoformat(rekorde["autark_serie"]["date_end"])
    assert rekorde["autark_serie"]["value"] == (ende - datetime(2025, 7, 13)).days + 1
    assert rekorde["autarkie_monat"]["date"] == "2025-08-01"               # Juli 2025 hat eine Lücke und ist kein ganzer Monat; Gleichstand: der frühere
    ohne_netz = _config(erzeuger=[{"entity_id": "sensor.pv"}])
    assert all(r["key"] not in ("autarkie_monat", "autark_serie") for r in service.compute_records(ohne_netz, NOW)["records"])


def test_jahresbilanz_komplett_und_bis_jetzt_je_jahr(service, monkeypatch) -> None:
    for entity_id in ("sensor.pv", "sensor.netz", "sensor.einsp"):
        _archiv(service, entity_id, datetime(2024, 3, 15, tzinfo=TZ).timestamp())
    config = _config(netzbezug="sensor.netz", einspeisung="sensor.einsp", erzeuger=[{"entity_id": "sensor.pv"}])
    taeglich = {"sensor.pv": 10.0, "sensor.netz": 4.0, "sensor.einsp": 3.0}

    def fenster(entity_ids, start, end, now_local, read_cache):
        # Ersatz für window_total: je Tag ein fester Wert, gezählt werden die Tage des Fensters.
        tage = (end - start).total_seconds() / 86400
        return round(sum(taeglich[e] for e in entity_ids) * tage, 3), None
    monkeypatch.setattr(service, "window_total", fenster)
    daten = service.compute_years(config, NOW)
    assert [j["year"] for j in daten["years"]] == [2026, 2025, 2024]
    assert daten["roles"] == ["erzeugung", "einspeisung", "netzbezug", "verbrauch"] and daten["current"] == 2026
    j2025 = next(j for j in daten["years"] if j["year"] == 2025)
    assert j2025["full"]["erzeugung"] == pytest.approx(3650.0)                     # ganzes Jahr: 365 Tage
    bis = (NOW.replace(year=2025) - datetime(2025, 1, 1, tzinfo=TZ)).total_seconds() / 86400
    assert j2025["ytd"]["erzeugung"] == pytest.approx(10.0 * bis, rel=1e-6)         # bis zum selben Kalendertag wie heute
    assert j2025["ytd"]["erzeugung"] < j2025["full"]["erzeugung"]
    j2026 = daten["years"][0]
    assert j2026["full"] == j2026["ytd"]                                            # das laufende Jahr zählt immer bis jetzt
    assert j2025["full"]["verbrauch"] == pytest.approx(3650 + 1460 - 1095)           # Bilanz: 10 + 4 − 3 je Tag
    assert j2025["full"]["autarkie"] == pytest.approx(round((1 - 1460 / 4015) * 100, 1))
    assert j2025["full"]["eigenverbrauch"] == pytest.approx(round((1 - 1095 / 3650) * 100, 1))
    assert daten["years"][2]["partial_from"] == "2024-03-15" and j2025["partial_from"] is None


def test_jahresbilanz_ohne_wert_ist_leer_statt_null_und_zeigt_nur_vorhandene_rollen(service) -> None:
    _archiv(service, "sensor.netz", datetime(2026, 1, 1, tzinfo=TZ).timestamp())
    daten = service.compute_years(_config(netzbezug="sensor.netz"), NOW)
    assert daten["roles"] == ["netzbezug", "verbrauch"]
    assert daten["years"][0]["full"]["netzbezug"] is None and daten["years"][0]["full"]["autarkie"] is None


def test_years_route(client) -> None:
    from app.main import index

    index.set_setting(ed.SETTING_CONFIG, "")
    assert client.get("/energiedashboard/langzeit/years").status_code == 409
    ed._save_config(index, _config(netzbezug="sensor.langzeit_test_netz"))
    try:
        antwort = client.get("/energiedashboard/langzeit/years")
        assert antwort.status_code == 200 and antwort.json()["roles"] == ["netzbezug", "verbrauch"]
    finally:
        index.set_setting(ed.SETTING_CONFIG, "")


def test_kenntage_je_jahr(service, monkeypatch) -> None:
    config = _zwei_zaehler(service, _monat(2025, 7))
    config["einspeisung"] = "sensor.einsp"
    _archiv(service, "sensor.einsp", _monat(2025, 7))
    tage = [_tag(i) for i in range(10)]                    # zehn Tage im Juli 2025
    pv = {t: 10.0 for t in tage}
    netz = {t: 2.0 for t in tage}
    einsp = {t: 1.0 for t in tage}
    netz[tage[1]] = 0.0                                    # fast autark: kein Netzbezug bei Verbrauch 11 kWh
    netz[tage[4]] = 1.0                                    # fast autark: genau 10 % von 10 kWh
    netz[tage[5]] = 1.5                                    # nicht autark genug: 15 %
    pv[tage[2]] = 0.4                                      # kaum PV
    einsp[tage[3]] = 20.0                                  # mehr eingespeist als verbraucht (10 + 2 − 20 < 0)
    _tagesreihen(monkeypatch, {"sensor.pv": pv, "sensor.netz": netz, "sensor.einsp": einsp})
    kenntage = service.compute_records(config, NOW)["kenntage"]
    # ohne kWp keine Sonnentage, ohne Speicher kein Paar
    assert kenntage["available"] == ["fast_autark", "ohne_netz", "ueberschuss", "einspeisung_hoch", "truebe"]
    felder = kenntage["years"][0]["fields"]
    assert kenntage["years"][0]["year"] == 2025
    assert felder["fast_autark"]["hit"] == 2 and felder["fast_autark"]["days"] == 10
    assert felder["truebe"]["hit"] == 1                    # 0,4 kWh sind unter 10 % des Tagesmittels
    assert felder["ueberschuss"]["hit"] == 0               # Verbrauch negativ: unplausibler Tag zählt nicht als „mehr eingespeist“
    assert felder["ohne_netz"]["hit"] == 1 and felder["ohne_netz"]["days"] == 10   # nur der Tag ohne Netzbezug
    assert felder["einspeisung_hoch"]["hit"] == 0          # 20 kWh sind nicht „über“ der Vorgabeschwelle von 20 kWh
    gesenkt = service.compute_records(_config(**{**config, "kenntage": {"einspeisung": 10}}), NOW)["kenntage"]
    assert gesenkt["years"][0]["fields"]["einspeisung_hoch"]["hit"] == 1
    assert gesenkt["thresholds"]["einspeisung"] == 10.0


def _kenntage_anlage(service, monkeypatch, **extra):
    """Zehn Tage im Juli 2025 und zehn im September 2025 mit Anlagenleistung und Speicher."""
    from datetime import date, timedelta

    for eid in ("sensor.pv", "sensor.netz", "sensor.ent"):
        _archiv(service, eid, _monat(2025, 7))
    tage = [date(2025, 7, 1) + timedelta(days=i) for i in range(10)] + [date(2025, 9, 1) + timedelta(days=i) for i in range(10)]
    pv = {t: 25.0 for t in tage}                           # 5 kWh je kWp bei 5 kWp
    netz = {t: 2.0 for t in tage}
    ent = {t: 6.0 for t in tage}
    ent[tage[1]] = 0.5
    pv[tage[2]] = 10.0                                     # 2 kWh je kWp
    _tagesreihen(monkeypatch, {"sensor.pv": pv, "sensor.netz": netz, "sensor.ent": ent})
    config = _config(
        netzbezug="sensor.netz", erzeuger=[{"entity_id": "sensor.pv", "kwp": 5.0}],
        speicher=[{"entladen_entity_id": "sensor.ent", "capacity_kwh": 6.0}], **extra,
    )
    service.storage_capacity = lambda cfg: sum(sp["capacity_kwh"] for sp in cfg["speicher"])   # 6 kWh
    return config, tage


def test_kenntage_sonnentage_und_speicherpaar(service, monkeypatch) -> None:
    config, _tage = _kenntage_anlage(service, monkeypatch)
    kenntage = service.compute_records(config, NOW)["kenntage"]
    assert kenntage["available"] == ["fast_autark", "ohne_netz", "sonnig", "truebe", "speicher_stark", "speicher_schwach"]
    assert kenntage["kwp"] == 5.0
    felder = kenntage["years"][0]["fields"]
    assert felder["sonnig"]["hit"] == 18 and felder["sonnig"]["days"] == 19   # der 1. September folgt auf einen fehlenden Tag
    assert felder["speicher_stark"]["hit"] == 18 and felder["speicher_schwach"]["hit"] == 1   # 100 % und 8 % von 6 kWh
    assert kenntage["capacity"] == 6.0


def test_kenntage_bis_heute_zaehlt_nur_bis_zum_heutigen_kalendertag(service, monkeypatch) -> None:
    config, _tage = _kenntage_anlage(service, monkeypatch)
    heute = datetime(2025, 8, 15, 12, tzinfo=TZ)           # der Juli liegt davor, der September danach
    feld = service.compute_records(config, datetime(2026, 8, 15, 12, tzinfo=TZ))["kenntage"]["years"][0]["fields"]["sonnig"]
    assert heute.year == 2025 and feld["days"] == 19 and feld["days_ytd"] == 10 and feld["hit_ytd"] == 9


def test_kenntage_schwellen_aus_dem_setup(service, monkeypatch) -> None:
    config, _tage = _kenntage_anlage(service, monkeypatch, kenntage={"sonnig": 6.0, "speicher_stark": 120.0, "speicher_schwach": 5.0})
    felder = service.compute_records(config, NOW)["kenntage"]["years"][0]["fields"]
    assert felder["sonnig"]["hit"] == 0                    # 5 kWh je kWp erreichen 6 nicht
    assert felder["speicher_stark"]["hit"] == 0            # 6 kWh Entladung bei 6 kWh Kapazität sind 100 %, nicht 120 %
    assert felder["speicher_schwach"]["hit"] == 0          # 0,5 kWh sind 8 % der Kapazität, nicht unter 5 %
    schwellen = lz.kenntage_thresholds(_config(kenntage={"autark": 3, "sonnig": "x", "truebe": 99, "stark": 7}))
    assert schwellen == {
        "autark": 50.0, "sonnig": 4.0, "truebe": 50.0, "speicher_stark": 80.0, "speicher_schwach": 10.0, "einspeisung": 20.0,
    }


def test_kenntage_kalender_liefert_die_tage_mit_werten(service, monkeypatch) -> None:
    config, tage = _kenntage_anlage(service, monkeypatch)
    daten = service.compute_kenntage_days(config, "speicher_schwach", 2025, NOW)
    treffer = [d for d in daten["days"] if d["hit"]]
    assert [d["date"] for d in treffer] == [tage[1].isoformat()]
    assert treffer[0]["values"]["entladung"] == 0.5 and len(daten["days"]) == 19
    service.storage_capacity = lambda cfg: None            # ohne bekannte Kapazität gibt es das Speicherpaar nicht
    assert "speicher_schwach" not in service.compute_records(config, NOW)["kenntage"]["available"]
    assert service.compute_kenntage_days(config, "speicher_schwach", 2025, NOW)["days"] == []
    assert service.compute_kenntage_days(config, "ueberschuss", 2025, NOW)["days"] == []   # keine Einspeisung eingerichtet


def test_veraenderungen_ranken_nach_betrag_und_nehmen_den_rest_als_grundlast(service, monkeypatch) -> None:
    from types import SimpleNamespace

    _archiv(service, "sensor.netz", _monat(2025, 1))
    config = _config(netzbezug="sensor.netz", verbraucher=[
        {"entity_id": "sensor.waerme", "name": "Wärmepumpe"},
        {"entity_id": "sensor.auto", "name": "Wallbox"},
        {"entity_id": "sensor.herd", "name": "Herd", "parent": "sensor.waerme"},   # hinter einem Zwischenzähler: zählt dort
        {"entity_id": "sensor.neu", "name": "Neu"},                                   # nur dieses Jahr: kein Vergleich
    ])
    werte = {
        ("sensor.netz", 2026): 1100.0, ("sensor.netz", 2025): 900.0,
        ("sensor.waerme", 2026): 300.0, ("sensor.waerme", 2025): 250.0,
        ("sensor.auto", 2026): 100.0, ("sensor.auto", 2025): 150.0,
        ("sensor.neu", 2026): 50.0,
    }
    for eid in ("sensor.waerme", "sensor.auto", "sensor.neu"):
        service.index.get_or_create_entity(eid, "sensor", "total_increasing", "kWh")

    def reihen(entity_id, start, end, now_local, read_cache):
        wert = werte.get((entity_id, start.year))
        return [SimpleNamespace(value=wert)] if wert is not None else []

    monkeypatch.setattr(service, "_rows", reihen)
    daten = service.compute_changes(config, lz.TableRequest(at="2026-08-12", mode="komplett"), NOW)
    namen = [i["name"] for i in daten["items"]]
    assert "Herd" not in namen and "Neu" not in namen
    wp = next(i for i in daten["items"] if i["name"] == "Wärmepumpe")
    assert wp["diff"] == 50.0 and wp["percent"] == 20.0
    auto = next(i for i in daten["items"] if i["name"] == "Wallbox")
    assert auto["diff"] == -50.0
    # Verbrauch 1100/900 minus gemessene 450/400 (die „Neu“ ohne Vorjahr zählt als gemessen, steht aber nicht in der Liste)
    rest = next(i for i in daten["items"] if i["entity_id"] is None)
    assert rest["cur"] == 650.0 and rest["vj"] == 500.0
    assert [i["name"] for i in daten["items"]][0] == "Übrige (Grundlast)"   # größter Betrag: +150


def test_records_und_changes_routen(client) -> None:
    from app.main import index

    index.set_setting(ed.SETTING_CONFIG, "")
    assert client.get("/energiedashboard/langzeit/records").status_code == 409
    assert client.post("/energiedashboard/langzeit/changes", json={}).status_code == 409
    ed._save_config(index, _config(netzbezug="sensor.langzeit_test_netz"))
    try:
        records = client.get("/energiedashboard/langzeit/records")
        assert records.status_code == 200 and records.json()["enough"] is False
        changes = client.post("/energiedashboard/langzeit/changes", json={"mode": "komplett"})
        assert changes.status_code == 200 and changes.json()["items"] == []
    finally:
        index.set_setting(ed.SETTING_CONFIG, "")


# -- Verbraucher über die Jahre (ROADMAP 1.49, P0) ------------------------------------------------


def _verbraucher_config() -> dict:
    return _config(netzbezug="sensor.netz", verbraucher=[
        {"entity_id": "sensor.waerme", "name": "Wärmepumpe"},
        {"entity_id": "sensor.auto", "name": "Wallbox"},
        {"entity_id": "sensor.herd", "name": "Herd", "parent": "sensor.waerme"},   # hinter einem Zwischenzähler
    ])


def _verbraucher(daten: dict, key: str) -> dict:
    return next(c for c in daten["consumers"] if c["key"] == key)


def test_verbraucher_monatswerte_jahressummen_und_rest(service) -> None:
    for eid in ("sensor.netz", "sensor.waerme", "sensor.auto", "sensor.herd"):
        _archiv(service, eid, _monat(2025, 1))
    monate_2025 = [(2025, m) for m in range(1, 13)]
    monate_2026 = [(2026, m) for m in range(1, 9)]
    daten = {
        "sensor.netz": {k: 100.0 for k in monate_2025 + monate_2026},
        "sensor.waerme": {k: 30.0 for k in monate_2025 + monate_2026} | {(2025, 2): 120.0},
        "sensor.auto": {k: 20.0 for k in monate_2025 + monate_2026},
        "sensor.herd": {k: 5.0 for k in monate_2025 + monate_2026},
    }
    ergebnis = _monate_service(service, daten).compute_consumers(_verbraucher_config(), lz.ConsumersRequest(), NOW)
    assert ergebnis["years"] == [2026, 2025]
    assert [c["key"] for c in ergebnis["consumers"]] == ["sensor.waerme", "sensor.auto"]   # Herd steckt in der Wärmepumpe
    wp = _verbraucher(ergebnis, "sensor.waerme")
    assert wp["name"] == "Wärmepumpe" and wp["entity_id"] == "sensor.waerme"
    assert wp["values"]["2026"] == [30.0] * 8 + [None] * 4                 # Zukunft leer, nicht 0
    assert wp["year_sums"] == {"2026": 240.0, "2025": 450.0}
    assert ergebnis["total"]["year_sums"] == {"2026": 800.0, "2025": 1200.0}
    rest = ergebnis["rest"]
    assert rest["name"] == "Übrige (Grundlast)" and rest["entity_id"] is None
    # Je Monat der Rest dieses Monats (Februar 2025: Verbraucher über der Bilanz, Rest 0 statt negativ) ...
    assert rest["values"]["2025"][:3] == [50.0, 0.0, 50.0]
    # ... je Jahr der Rest der Jahressummen: 1200 − 450 − 240.
    assert rest["year_sums"]["2025"] == 510.0
    assert rest["year_sums"]["2026"] == 800.0 - 240.0 - 160.0


def test_verbraucher_rangliste_zum_stichtag_gegen_dasselbe_stueck_des_vorjahres(service, monkeypatch) -> None:
    from types import SimpleNamespace

    _archiv(service, "sensor.netz", _monat(2025, 1))
    for eid in ("sensor.waerme", "sensor.auto", "sensor.herd"):
        service.index.get_or_create_entity(eid, "sensor", "total_increasing", "kWh")
    werte = {
        ("sensor.netz", 2026): 1100.0, ("sensor.netz", 2025): 300.0,
        ("sensor.waerme", 2026): 300.0, ("sensor.waerme", 2025): 250.0,
        ("sensor.auto", 2026): 100.0, ("sensor.auto", 2025): 150.0,
    }

    def reihen(entity_id, start, end, now_local, read_cache):
        wert = werte.get((entity_id, start.year))
        return [SimpleNamespace(value=wert)] if wert is not None else []

    monkeypatch.setattr(service, "_rows", reihen)
    daten = service.compute_consumers(_verbraucher_config(), lz.ConsumersRequest(at="2026-08-12", months=False), NOW)
    assert "years" not in daten and "values" not in daten["consumers"][0]
    assert daten["at"] == "2026-08-12" and daten["window"]["year"] == 2026
    assert datetime.fromtimestamp(daten["window"]["cur"]["end"], TZ) == datetime(2026, 8, 13, tzinfo=TZ)
    assert datetime.fromtimestamp(daten["window"]["vj"]["end"], TZ) == datetime(2025, 8, 13, tzinfo=TZ)
    wp = _verbraucher(daten, "sensor.waerme")
    assert (wp["cur"], wp["vj"]) == (300.0, 250.0)
    assert daten["window"]["total"] == {"cur": 1100.0, "vj": 300.0}
    assert daten["window"]["measured"] == {"cur": 400.0, "vj": 400.0}
    # Übrige: Rest bis zur Bilanz, im Vorjahr nicht negativ (300 − 400).
    assert (daten["rest"]["cur"], daten["rest"]["vj"]) == (700.0, 0.0)


def _reihen_nach_monat(service, monkeypatch, werte: dict) -> None:
    """Fenstersummen je (Entität, Jahr, Monat des Fensterbeginns); ohne Eintrag keine Zeilen."""
    from types import SimpleNamespace

    def reihen(entity_id, start, end, now_local, read_cache):
        wert = werte.get((entity_id, start.year, start.month))
        return [SimpleNamespace(value=wert)] if wert is not None else []

    monkeypatch.setattr(service, "_rows", reihen)


def test_verbraucher_zeitraeume_monat_und_vormonat_gegen_denselben_zeitraum_des_vorjahres(service, monkeypatch) -> None:
    _archiv(service, "sensor.netz", _monat(2025, 1))
    for eid in ("sensor.waerme", "sensor.auto", "sensor.herd"):
        service.index.get_or_create_entity(eid, "sensor", "total_increasing", "kWh")
    _reihen_nach_monat(service, monkeypatch, {
        ("sensor.netz", 2026, 8): 500.0, ("sensor.netz", 2025, 8): 400.0,
        ("sensor.netz", 2026, 7): 900.0, ("sensor.netz", 2025, 7): 800.0,
        ("sensor.waerme", 2026, 8): 120.0, ("sensor.waerme", 2025, 8): 100.0,
        ("sensor.waerme", 2026, 7): 200.0, ("sensor.waerme", 2025, 7): 190.0,
        ("sensor.auto", 2026, 8): 60.0,                       # im August 2025 noch nicht erfasst
        ("sensor.auto", 2026, 7): 50.0, ("sensor.auto", 2025, 7): 70.0,
    })
    daten = service.compute_consumers(_verbraucher_config(), lz.ConsumersRequest(months=False), NOW)
    assert [p["key"] for p in daten["periods"]] == ["heute", "gestern", "monat", "vormonat", "jahr"]
    perioden = {p["key"]: p for p in daten["periods"]}
    monat, vormonat = perioden["monat"], perioden["vormonat"]
    # Der laufende Monat zählt bis jetzt (20. August 12 Uhr) gegen dasselbe Stück des Vorjahres, der Vormonat ganz.
    assert monat["partial"] is True and vormonat["partial"] is False
    # Heute zählt bis jetzt, Gestern den ganzen Vortag; beide gegen denselben Tag des Vorjahres.
    assert perioden["heute"]["partial"] is True and perioden["gestern"]["partial"] is False
    assert datetime.fromtimestamp(perioden["heute"]["cur"]["start"], TZ) == datetime(2026, 8, 20, tzinfo=TZ)
    assert datetime.fromtimestamp(perioden["gestern"]["vj"]["start"], TZ) == datetime(2025, 8, 19, tzinfo=TZ)
    assert datetime.fromtimestamp(monat["cur"]["start"], TZ) == datetime(2026, 8, 1, tzinfo=TZ)
    assert datetime.fromtimestamp(monat["cur"]["end"], TZ) == NOW
    assert datetime.fromtimestamp(monat["vj"]["end"], TZ) == datetime(2025, 8, 20, 12, tzinfo=TZ)
    assert datetime.fromtimestamp(vormonat["cur"]["start"], TZ) == datetime(2026, 7, 1, tzinfo=TZ)
    assert datetime.fromtimestamp(vormonat["vj"]["end"], TZ) == datetime(2025, 8, 1, tzinfo=TZ)
    assert monat["total"] == {"cur": 500.0, "vj": 400.0} and vormonat["total"] == {"cur": 900.0, "vj": 800.0}
    assert monat["measured"] == {"cur": 180.0, "vj": 100.0}
    wp, auto = _verbraucher(daten, "sensor.waerme"), _verbraucher(daten, "sensor.auto")
    assert wp["periods"]["monat"] == {"cur": 120.0, "vj": 100.0}
    assert wp["periods"]["vormonat"] == {"cur": 200.0, "vj": 190.0}
    assert auto["periods"]["monat"] == {"cur": 60.0, "vj": None}     # fehlendes Vorjahr ist kein 0
    # Übrige: Rest bis zur Bilanz je Seite, nie negativ.
    assert daten["rest"]["periods"]["monat"] == {"cur": 320.0, "vj": 300.0}
    assert daten["rest"]["periods"]["vormonat"] == {"cur": 650.0, "vj": 540.0}
    # Das Fenster des laufenden Jahres bleibt unverändert.
    assert daten["window"]["year"] == 2026


def test_verbraucher_zeitraum_jahr_und_vorjahr_komplett(service, monkeypatch) -> None:
    _archiv(service, "sensor.netz", _monat(2024, 1))
    for eid in ("sensor.waerme", "sensor.auto", "sensor.herd"):
        service.index.get_or_create_entity(eid, "sensor", "total_increasing", "kWh")
    _reihen_nach_monat(service, monkeypatch, {
        ("sensor.netz", 2026, 1): 5000.0, ("sensor.netz", 2025, 1): 4500.0,
        ("sensor.waerme", 2026, 1): 900.0, ("sensor.waerme", 2025, 1): 800.0,
    })
    daten = service.compute_consumers(_verbraucher_config(), lz.ConsumersRequest(months=False), NOW)
    assert [p["key"] for p in daten["periods"]] == list(lz.CONSUMER_PERIODS) and daten["mode"] == "bis_jetzt"
    jahr = {p["key"]: p for p in daten["periods"]}["jahr"]
    # „Dieses Jahr“ ist das Fenster der Rangliste: bis jetzt gegen dasselbe Stück des Vorjahres, dieselben Werte.
    assert jahr["partial"] is True and jahr["total"] == {"cur": 5000.0, "vj": 4500.0}
    waerme = _verbraucher(daten, "sensor.waerme")
    assert waerme["periods"]["jahr"] == {"cur": 900.0, "vj": 800.0}
    assert (waerme["cur"], waerme["vj"]) == (900.0, 800.0)
    assert datetime.fromtimestamp(jahr["vj"]["end"], TZ) == datetime(2025, 8, 20, 12, tzinfo=TZ)

    # „Komplett“ stellt laufende Zeiträume dem ganzen Vorjahreszeitraum gegenüber, abgeschlossene bleiben gleich.
    komplett = service.compute_consumers(_verbraucher_config(), lz.ConsumersRequest(months=False, mode="komplett"), NOW)
    assert komplett["mode"] == "komplett"
    perioden = {p["key"]: p for p in komplett["periods"]}
    assert datetime.fromtimestamp(perioden["jahr"]["vj"]["end"], TZ) == datetime(2026, 1, 1, tzinfo=TZ)
    assert datetime.fromtimestamp(perioden["monat"]["vj"]["end"], TZ) == datetime(2025, 9, 1, tzinfo=TZ)
    assert datetime.fromtimestamp(perioden["vormonat"]["vj"]["end"], TZ) == datetime(2025, 8, 1, tzinfo=TZ)
    assert datetime.fromtimestamp(perioden["jahr"]["cur"]["end"], TZ) == NOW
    # Die Rangliste „Verbraucher im Jahr“ bleibt bei „bis jetzt“.
    assert datetime.fromtimestamp(komplett["window"]["vj"]["end"], TZ) == datetime(2025, 8, 20, 12, tzinfo=TZ)
    # Ein unbekannter Modus gilt als „bis jetzt“.
    assert service.compute_consumers(_verbraucher_config(), lz.ConsumersRequest(months=False, mode="x"), NOW)["mode"] == "bis_jetzt"


def test_verbraucher_zeitraeume_am_monatsanfang_und_abwaehlbar(service, monkeypatch) -> None:
    _archiv(service, "sensor.netz", _monat(2025, 1))
    for eid in ("sensor.waerme", "sensor.auto", "sensor.herd"):
        service.index.get_or_create_entity(eid, "sensor", "total_increasing", "kWh")
    _reihen_nach_monat(service, monkeypatch, {("sensor.netz", 2026, 9): 10.0, ("sensor.netz", 2026, 8): 700.0})
    erster = datetime(2026, 9, 1, 0, 30, tzinfo=TZ)
    daten = service.compute_consumers(_verbraucher_config(), lz.ConsumersRequest(months=False), erster)
    perioden = {p["key"]: p for p in daten["periods"]}
    monat, vormonat = perioden["monat"], perioden["vormonat"]
    # Eine halbe Stunde nach Monatsbeginn: der laufende Monat ist ein kurzes Stück, der Vormonat der ganze August.
    assert datetime.fromtimestamp(monat["cur"]["end"], TZ) == erster and monat["partial"] is True
    assert datetime.fromtimestamp(vormonat["cur"]["start"], TZ) == datetime(2026, 8, 1, tzinfo=TZ)
    assert datetime.fromtimestamp(vormonat["cur"]["end"], TZ) == datetime(2026, 9, 1, tzinfo=TZ)
    assert vormonat["total"]["cur"] == 700.0 and monat["total"]["cur"] == 10.0
    # Ohne Zeiträume keine Zusatzdaten.
    ohne = service.compute_consumers(_verbraucher_config(), lz.ConsumersRequest(months=False, periods=[]), NOW)
    assert "periods" not in ohne and "periods" not in ohne["rest"]


def test_verbraucher_zeitraeume_spaeter_begonnener_verbraucher_hat_kein_vorjahr(service, monkeypatch) -> None:
    _archiv(service, "sensor.netz", _monat(2025, 1))
    _archiv(service, "sensor.waerme", _monat(2025, 1))
    _archiv(service, "sensor.auto", _monat(2026, 7))                # erst seit Juli 2026 erfasst
    service.index.get_or_create_entity("sensor.herd", "sensor", "total_increasing", "kWh")
    _reihen_nach_monat(service, monkeypatch, {
        ("sensor.netz", 2026, 7): 300.0, ("sensor.netz", 2025, 7): 300.0,
        ("sensor.waerme", 2026, 7): 100.0, ("sensor.waerme", 2025, 7): 100.0,
        ("sensor.auto", 2026, 7): 40.0,
    })
    daten = service.compute_consumers(_verbraucher_config(), lz.ConsumersRequest(months=False), NOW)
    auto = _verbraucher(daten, "sensor.auto")
    assert auto["late"] is True and auto["first_ts"] == _monat(2026, 7)
    assert auto["periods"]["vormonat"] == {"cur": 40.0, "vj": None}
    # Sein Verbrauch steckte im Vorjahr in „Übrige“: dort ist der Vergleich nur wegen des späten Beginns verzerrt.
    assert daten["rest"]["periods"]["vormonat"] == {"cur": 160.0, "vj": 200.0}


def test_spaeter_begonnener_verbraucher_ist_markiert_und_hat_einen_teilmonat(service) -> None:
    _archiv(service, "sensor.netz", _monat(2025, 1))
    _archiv(service, "sensor.waerme", _monat(2025, 1) + 3600)                 # am selben Tag wie die Bilanz
    _archiv(service, "sensor.auto", _monat(2025, 6) + 14 * 86400)             # erst ab dem 15. Juni
    daten = _monate_service(service, {}).compute_consumers(_verbraucher_config(), lz.ConsumersRequest(), NOW)
    wp, auto = _verbraucher(daten, "sensor.waerme"), _verbraucher(daten, "sensor.auto")
    assert wp["late"] is False and wp["partial_start"] is None
    assert auto["late"] is True and auto["partial_start"] == [2025, 6]
    assert auto["first_ts"] == _monat(2025, 6) + 14 * 86400
    assert auto["values"]["2025"][:5] == [None] * 5                             # vor dem Archivbeginn leer, nicht 0
    assert daten["rest"]["first_ts"] == _monat(2025, 1)


def test_verbraucher_tragen_luecken_und_zaehlerrueckgaenge_je_monat(service) -> None:
    for eid in ("sensor.netz", "sensor.waerme", "sensor.auto"):
        _archiv(service, eid, _monat(2025, 1))
    _tageszaehler(service, "sensor.waerme", {"2025-03-20": {"counter_decreases": 1}, "2025-04-02": {"gaps": 4}})
    daten = {"sensor.waerme": {(2025, m): 30.0 for m in range(1, 13)}, "sensor.netz": {(2025, m): 90.0 for m in range(1, 13)}}
    ergebnis = _monate_service(service, daten).compute_consumers(_verbraucher_config(), lz.ConsumersRequest(), NOW)
    wp = _verbraucher(ergebnis, "sensor.waerme")
    assert wp["flags"] == {"2025-03": {"counter_decreases": 1}} and wp["unchecked"] == 0   # 4 Lücken sind unauffällig
    auto = _verbraucher(ergebnis, "sensor.auto")
    assert auto["flags"] == {} and auto["unchecked"] == 1                                   # nicht geprüft, nicht „ohne Befund“
    assert ergebnis["rest"]["unchecked"] == 1                                               # der Netzbezug


def test_consumers_route(client) -> None:
    from app.main import index

    index.set_setting(ed.SETTING_CONFIG, "")
    assert client.post("/energiedashboard/langzeit/consumers", json={}).status_code == 409
    ed._save_config(index, _config(netzbezug="sensor.langzeit_test_netz"))
    try:
        antwort = client.post("/energiedashboard/langzeit/consumers", json={"months": True})
        assert antwort.status_code == 200
        daten = antwort.json()
        assert daten["consumers"] == [] and daten["rest"]["key"] == "rest"
        assert daten["years"][0] == daten["current"]["year"]
        assert client.post("/energiedashboard/langzeit/consumers", json={"at": "quatsch"}).status_code == 400
    finally:
        index.set_setting(ed.SETTING_CONFIG, "")
