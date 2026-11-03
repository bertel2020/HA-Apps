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
    jahr = service.compute_records(config, NOW)["kenntage"][0]
    assert jahr["year"] == 2025 and jahr["days"] == 10
    assert jahr["autarkie"] == 2 and jahr["kaum_pv"] == 1
    assert jahr["einspeisung_ueber_verbrauch"] == 0        # Verbrauch negativ: unplausibler Tag zählt nicht als „mehr eingespeist“


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
