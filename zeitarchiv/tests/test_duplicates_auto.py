"""„Duplikate automatisch entfernen“ (duplicates_auto.py): Schalter, Lauf, Sicherungen, Rückgängig, Meldungen.

Wie test_data_quality.py gegen echte Rohdaten im Hot Buffer; „jetzt“ ist fest, damit das 30-Tage-Fenster stimmt.
"""

from __future__ import annotations

import shutil
import tempfile
from contextlib import nullcontext
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app import duplicates_auto, notices
from app.storage import cleanup, hotbuffer
from app.storage.coordinator import CoordinatorBusy
from app.storage.index import Index

_TZ = ZoneInfo("Europe/Berlin")
_JETZT = datetime(2024, 8, 15, 12, tzinfo=_TZ)
_TAG = datetime(2024, 8, 10, 8, tzinfo=_TZ).timestamp()


def _lock(_entity_id):
    return nullcontext()


@pytest.fixture()
def daten():
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-dup-auto-"))
    idx = Index(tmp / "index.sqlite")
    try:
        yield tmp, idx
    finally:
        idx.close()
        shutil.rmtree(tmp, ignore_errors=True)


def _schreibe(tmp, idx, entity_id, werte_je_zeitstempel, *, start=_TAG, typ="measurement", **config):
    """Je Eintrag in ``werte_je_zeitstempel`` ein Zeitstempel (stündlich) mit den angegebenen Rohwerten."""
    idx.get_or_create_entity(entity_id, "sensor", "measurement", "°C", state=(typ == "state"))
    for offset, werte in enumerate(werte_je_zeitstempel):
        ts = start + offset * 3600
        for wert in werte:
            hotbuffer.append(tmp, entity_id, ts, wert, _TZ)
            idx.record_write(entity_id, ts)
    return start


def _schnappschuss(tmp, idx):
    rows = cleanup.count_duplicate_rows_by_entity(tmp, idx, _TZ, now=_JETZT)
    idx.set_duplicate_snapshot(rows)
    return rows


def _an(idx, mode="identical", now=1.0):
    duplicates_auto.enable(idx, mode, now)


def _lauf(tmp, idx, lock=_lock):
    _schnappschuss(tmp, idx)
    return duplicates_auto.run(tmp, idx, _TZ, lock, _JETZT)


def _sichtbar(tmp, idx, entity_id):
    """Rohzeilen, die nach den Markierungen noch zählen."""
    start = (_JETZT - timedelta(days=60)).timestamp()
    return list(cleanup.iter_raw_rows(tmp, idx, entity_id, start, _JETZT.timestamp(), _TZ, now=_JETZT))


def test_standard_ist_aus_und_ein_lauf_tut_nichts(daten):
    tmp, idx = daten
    _schreibe(tmp, idx, "sensor.a", [[1.0, 1.0, 1.0]])
    _schnappschuss(tmp, idx)
    assert duplicates_auto.settings(idx)["enabled"] is False
    assert duplicates_auto.run_if_due(tmp, idx, _TZ, _lock, _JETZT) is False
    assert idx.get_entity("sensor.a")["deleted_count"] == 0
    assert idx.list_entity_actions_by_action(duplicates_auto.RUN_ACTION) == []


def test_modus_identical_entfernt_nur_wertgleiche(daten):
    tmp, idx = daten
    # Zeitstempel 1: dreimal 20.0 (zwei wertgleiche Überzählige); Zeitstempel 2: 20.0 und 25.0 (abweichend)
    _schreibe(tmp, idx, "sensor.a", [[20.0, 20.0, 20.0], [20.0, 25.0]])
    _an(idx, "identical")
    lauf = _lauf(tmp, idx)
    assert lauf["entities"] == {"sensor.a": 2}
    rows = _sichtbar(tmp, idx, "sensor.a")
    assert [v for _, v in rows] == [20.0, 20.0, 25.0]  # je eine Zeile des ersten Zeitstempels, beide des zweiten
    assert idx.get_entity("sensor.a")["deleted_count"] == 2


def test_modus_all_laesst_den_groessten_wert_stehen(daten):
    tmp, idx = daten
    _schreibe(tmp, idx, "sensor.a", [[20.0, 25.0, 22.0]])
    _an(idx, "all")
    lauf = _lauf(tmp, idx)
    assert lauf["entities"] == {"sensor.a": 2}
    assert [v for _, v in _sichtbar(tmp, idx, "sensor.a")] == [25.0]


def test_ausnahme_und_zustands_entitaet_werden_uebersprungen(daten):
    tmp, idx = daten
    _schreibe(tmp, idx, "sensor.frei", [[1.0, 1.0]])
    _schreibe(tmp, idx, "sensor.ausnahme", [[1.0, 1.0]])
    _schreibe(tmp, idx, "select.zustand", [[1.0, 1.0]], typ="state")
    idx.set_duplicates_auto("sensor.ausnahme", "off")
    _an(idx)
    lauf = _lauf(tmp, idx)
    assert lauf["entities"] == {"sensor.frei": 1}
    assert idx.get_entity("sensor.ausnahme")["deleted_count"] == 0
    assert idx.get_entity("select.zustand")["deleted_count"] == 0


def test_zaehler_mit_erlaubten_rueckgaengen_nur_wertgleich_auch_im_modus_all(daten):
    tmp, idx = daten
    _schreibe(tmp, idx, "sensor.zaehler", [[0.0, 5.0], [7.0, 7.0]])
    idx.set_counter_decreases("sensor.zaehler", "allow")
    _an(idx, "all")
    lauf = _lauf(tmp, idx)
    # der abweichende Zeitstempel (0.0 / 5.0) bleibt, nur das wertgleiche Paar geht
    assert lauf["entities"] == {"sensor.zaehler": 1}
    assert [v for _, v in _sichtbar(tmp, idx, "sensor.zaehler")] == [0.0, 5.0, 7.0]


def test_nur_die_letzten_30_tage(daten):
    tmp, idx = daten
    alt = (_JETZT - timedelta(days=45)).timestamp()
    _schreibe(tmp, idx, "sensor.alt", [[3.0, 3.0]], start=alt)
    _an(idx)
    assert _lauf(tmp, idx) is None
    assert idx.get_entity("sensor.alt")["deleted_count"] == 0


def test_erster_lauf_ohne_sicherung_folgelauf_haelt_an(daten):
    tmp, idx = daten
    viele = duplicates_auto.MAX_PER_ENTITY + 100
    _schreibe(tmp, idx, "sensor.sturm", [[1.0] * (viele + 1)])
    _an(idx)
    erster = _lauf(tmp, idx)
    assert erster["entities"] == {"sensor.sturm": viele}  # der Bestand des ersten Laufs wird nicht gekappt
    assert idx.get_entity("sensor.sturm")["duplicates_auto_halted_at"] is None
    # später: ein neuer Sturm an einem anderen Zeitstempel
    _schreibe(tmp, idx, "sensor.sturm", [[1.0] * (viele + 1)], start=_TAG + 86400)
    zweiter = _lauf(tmp, idx)
    assert zweiter["halted"] == ["sensor.sturm"]
    assert zweiter["entities"] == {}
    assert idx.get_entity("sensor.sturm")["duplicates_auto_halted_at"] is not None
    titel = [n["title"] for n in duplicates_auto.notices(idx, _TZ)]
    assert "Duplikate-Automatik angehalten" in titel


def test_gesamtgrenze_je_lauf_der_rest_kommt_im_naechsten(daten, monkeypatch):
    tmp, idx = daten
    monkeypatch.setattr(duplicates_auto, "MAX_PER_RUN", 3)
    _schreibe(tmp, idx, "sensor.a", [[1.0] * 6])
    _an(idx)
    assert _lauf(tmp, idx)["entities"] == {"sensor.a": 3}
    assert duplicates_auto.settings(idx)["backlog"] is True  # erster Lauf noch nicht fertig
    assert _lauf(tmp, idx)["entities"] == {"sensor.a": 2}
    assert duplicates_auto.settings(idx)["backlog"] is False


def test_ein_lauf_teilt_deleted_at_und_laesst_sich_je_entitaet_zuruecknehmen(daten):
    tmp, idx = daten
    _schreibe(tmp, idx, "sensor.a", [[1.0, 1.0]])
    _schreibe(tmp, idx, "sensor.b", [[2.0, 2.0, 2.0]])
    _an(idx)
    lauf = _lauf(tmp, idx)
    run_at = lauf["run_at"]
    assert idx.count_deleted_by_batch("sensor.a", [run_at]) == {run_at: 1}
    assert idx.count_deleted_by_batch("sensor.b", [run_at]) == {run_at: 2}
    assert duplicates_auto.undo_run(idx, run_at, _lock, "sensor.a") == 1
    assert idx.get_entity("sensor.a")["deleted_count"] == 0
    assert idx.get_entity("sensor.b")["deleted_count"] == 2
    assert duplicates_auto.undo_run(idx, run_at, _lock) == 2  # der Rest des Laufs; sensor.a ist schon zurück
    assert duplicates_auto.undo_run(idx, run_at, _lock) == 0  # wiederholt: harmlos


def test_zurueckgenommene_werte_kommen_nicht_wieder(daten):
    tmp, idx = daten
    _schreibe(tmp, idx, "sensor.a", [[1.0, 1.0]])
    _an(idx)
    run_at = _lauf(tmp, idx)["run_at"]
    duplicates_auto.undo_run(idx, run_at, _lock)
    assert _lauf(tmp, idx) is None
    assert idx.get_entity("sensor.a")["deleted_count"] == 0


def test_manuelles_zuruecknehmen_im_reiter_markiert_zaehlt_auch(daten):
    tmp, idx = daten
    _schreibe(tmp, idx, "sensor.a", [[1.0, 1.0]])
    _an(idx)
    _lauf(tmp, idx)
    cleanup.undo_last_delete(idx, "sensor.a")
    assert _lauf(tmp, idx) is None


def test_belegte_entitaet_wird_uebersprungen_die_uebrigen_laufen(daten):
    tmp, idx = daten
    _schreibe(tmp, idx, "sensor.belegt", [[1.0, 1.0]])
    _schreibe(tmp, idx, "sensor.frei", [[1.0, 1.0]])
    _an(idx)

    def sperre(entity_id):
        if entity_id == "sensor.belegt":
            raise CoordinatorBusy("belegt")
        return nullcontext()

    lauf = _lauf(tmp, idx, sperre)
    assert lauf["entities"] == {"sensor.frei": 1}
    assert lauf["skipped_locked"] == ["sensor.belegt"]
    assert duplicates_auto.settings(idx)["backlog"] is True  # der erste Lauf ist erst mit allen fertig


def test_takt_zweiter_aufruf_in_der_stunde_tut_nichts(daten):
    tmp, idx = daten
    _schreibe(tmp, idx, "sensor.a", [[1.0, 1.0]])
    _an(idx)
    _schnappschuss(tmp, idx)
    assert duplicates_auto.run_if_due(tmp, idx, _TZ, _lock, _JETZT) is True
    _schreibe(tmp, idx, "sensor.a", [[1.0, 1.0]], start=_TAG + 86400)
    _schnappschuss(tmp, idx)
    assert duplicates_auto.run_if_due(tmp, idx, _TZ, _lock, _JETZT + timedelta(minutes=30)) is False
    assert duplicates_auto.run_if_due(tmp, idx, _TZ, _lock, _JETZT + timedelta(minutes=61)) is True


def test_nach_dem_lauf_gilt_der_schnappschuss_als_veraltet(daten):
    tmp, idx = daten
    _schreibe(tmp, idx, "sensor.a", [[1.0, 1.0]])
    _an(idx)
    _lauf(tmp, idx)
    # Markieren leert den Schnappschuss (Index.invalidate_finding_caches): der nächste Wartungstakt rechnet neu,
    # statt dass die Meldung „Duplikate gefunden“ eine Stunde lang auf Erledigtes zeigt.
    assert idx.is_duplicate_snapshot_stale()
    assert idx.get_duplicate_snapshot()["rows"] == []


def test_meldung_nach_lauf_und_gesehen(daten):
    tmp, idx = daten
    _schreibe(tmp, idx, "sensor.a", [[1.0, 1.0]])
    _an(idx, now=1.0)
    _lauf(tmp, idx)
    gefunden = [n for n in duplicates_auto.notices(idx, _TZ) if n["id"] == "housekeeping.duplicates_auto"]
    assert len(gefunden) == 1 and gefunden[0]["severity"] == "info"
    duplicates_auto.mark_seen(idx, now=9_999_999_999.0)
    assert [n for n in duplicates_auto.notices(idx, _TZ) if n["id"] == "housekeeping.duplicates_auto"] == []


def test_ausgeschaltet_keine_meldungen(daten):
    tmp, idx = daten
    _schreibe(tmp, idx, "sensor.a", [[1.0, 1.0]])
    _an(idx)
    _lauf(tmp, idx)
    duplicates_auto.disable(idx)
    assert duplicates_auto.notices(idx, _TZ) == []


def test_vorschau_aus_dem_schnappschuss(daten):
    tmp, idx = daten
    _schreibe(tmp, idx, "sensor.a", [[1.0, 1.0, 1.0]])
    _schreibe(tmp, idx, "sensor.gemischt", [[1.0, 1.0], [2.0, 3.0]])
    _schreibe(tmp, idx, "select.zustand", [[1.0, 1.0]], typ="state")
    _schreibe(tmp, idx, "sensor.ausnahme", [[1.0, 1.0]])
    idx.set_duplicates_auto("sensor.ausnahme", "off")
    _schnappschuss(tmp, idx)
    identisch = duplicates_auto.preview(idx, "identical")
    zahlen = {r["entity_id"]: r["count"] for r in identisch["rows"]}
    assert zahlen == {"sensor.a": 2, "sensor.gemischt": 1}
    assert {r["entity_id"]: r["left"] for r in identisch["rows"]}["sensor.gemischt"] == 1
    assert {s["name"] for s in identisch["skipped"]} >= {"select.zustand", "sensor.ausnahme"}
    alle = duplicates_auto.preview(idx, "all")
    assert {r["entity_id"]: r["count"] for r in alle["rows"]} == {"sensor.a": 2, "sensor.gemischt": 2}


def test_einschalten_setzt_stand_und_ausschalten_laesst_markierungen(daten):
    tmp, idx = daten
    _schreibe(tmp, idx, "sensor.a", [[1.0, 1.0]])
    _an(idx, "all", now=42.0)
    state = duplicates_auto.settings(idx)
    assert (state["enabled"], state["mode"], state["since"], state["backlog"]) == (True, "all", 42.0, True)
    _lauf(tmp, idx)
    duplicates_auto.disable(idx)
    assert idx.get_entity("sensor.a")["deleted_count"] == 1
    with pytest.raises(ValueError):
        duplicates_auto.set_mode(idx, "kaputt")


def test_fortsetzen_raeumt_angehaltene_entitaet_ohne_sicherung(daten):
    tmp, idx = daten
    _schreibe(tmp, idx, "sensor.a", [[1.0, 1.0, 1.0]])
    _an(idx)
    idx.set_duplicates_auto_halted("sensor.a", 1.0)
    assert duplicates_auto.entity_status(idx.get_entity("sensor.a"), "identical")[0] == "halted"
    _schnappschuss(tmp, idx)
    assert duplicates_auto.resume_entity(tmp, idx, _TZ, "sensor.a", _lock, _JETZT) == 2
    assert idx.get_entity("sensor.a")["duplicates_auto_halted_at"] is None
    eintraege = duplicates_auto.runs(idx, now=_JETZT.timestamp() + 3600 * 24 * 365 * 100, days=36500)
    assert eintraege and eintraege[0]["manual"] is True


def test_meldung_duplikate_gefunden_zaehlt_nur_den_rest(daten):
    tmp, idx = daten
    _schreibe(tmp, idx, "sensor.a", [[1.0, 1.0]])
    _schreibe(tmp, idx, "sensor.ausnahme", [[1.0, 1.0]])
    idx.set_duplicates_auto("sensor.ausnahme", "off")
    _an(idx)
    _schnappschuss(tmp, idx)
    gefunden = [n for n in _duplikat_meldungen(idx) if n["id"] == "housekeeping.duplicates_found"]
    assert len(gefunden) == 1
    assert "1 doppelte Zeitstempel über 1 Entität" in gefunden[0]["detail"]


def _duplikat_meldungen(idx):
    """Nur der Duplikat-Teil von build_notices(): der Rest braucht Index-Pfad, Scheduler-Stand usw."""
    kwargs = dict(
        index_path=Path("."), tz=_TZ, purge_totals={}, storage_reconcile=None, stale_entity_count=0,
        scheduler_last_tick=0.0, reconcile_last_tick=0.0, reconcile_in_progress=False,
    )
    import inspect

    assert "duplicate_ratio_events" in inspect.signature(notices.build_notices).parameters
    return notices.build_notices(idx, **kwargs)
