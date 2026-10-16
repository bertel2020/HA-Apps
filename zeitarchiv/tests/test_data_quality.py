"""Housekeeping → Datenqualität: die Matrix aus den vorhandenen Messungen.

Die Matrix rechnet nichts nach, sie führt Duplikat-Schnappschuss,
Zählerrückgang-Schnappschuss und Ausreißer-Quoten über die entity_id zusammen.
Geprüft wird deshalb vor allem, dass nichts verloren geht, nichts doppelt zählt
und dass "noch nicht gemessen" von "nichts gefunden" unterscheidbar bleibt.
"""

from __future__ import annotations

import shutil
import tempfile
from contextlib import nullcontext
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app import data_quality
from app.storage import hotbuffer
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


def _entity(index, entity_id, *, name=None, schwelle="50"):
    index.get_or_create_entity(entity_id, "sensor", "measurement", "kWh")
    index.set_config(entity_id, outlier_threshold=schwelle, custom_name=name)


def _gesamt(index, entity_id, *, duplicates=0, counter_decreases=0, outliers=0):
    """Legt der Entität die Gesamt-Zählung unter, wie sie der Wartungsplaner nach einem
    Lauf über die komplette Historie hinterlässt (dieselbe Quelle wie die
    Trefferzahlen der Bereinigungsseite)."""
    index.set_config(entity_id, outlier_threshold="50")
    index.set_cleanup_alltime_stats(
        entity_id,
        {"all": 1000, "outliers": outliers, "gaps": 0, "duplicates": duplicates,
         "repetitions": 0, "counter_decreases": counter_decreases},
        "50", OUTLIER_RULE_VERSION, gap_threshold="15",
    )


def _mit_daten(index, entity_id, **kwargs):
    """Eine Entität, die schon Werte hat (first_ts gesetzt) und damit gemessen werden soll."""
    _entity(index, entity_id, **kwargs)
    index.set_first_ts_and_add_rows(entity_id, 1_700_000_000.0, 0)


def test_ohne_messung_ist_leer_und_ausstehend(index):
    _mit_daten(index, "sensor.a")
    matrix = data_quality.build_matrix(index)
    assert matrix["rows"] == []
    assert matrix["affected"] == 0
    # Keine Zählung ist nicht "keine Befunde".
    assert matrix["pending"] == 1


def test_entitaet_ohne_daten_zaehlt_nicht_als_ausstehend(index):
    _entity(index, "sensor.a")
    assert data_quality.build_matrix(index)["pending"] == 0


def test_gemessen_ohne_befund_ist_nicht_ausstehend(index):
    _mit_daten(index, "sensor.a")
    _gesamt(index, "sensor.a")
    matrix = data_quality.build_matrix(index)
    assert matrix["pending"] == 0
    assert matrix["rows"] == []


def test_entitaet_mit_allen_drei_befunden_eine_zeile(index):
    _mit_daten(index, "sensor.a", name="Gesamtenergie")
    _gesamt(index, "sensor.a", duplicates=12, counter_decreases=3, outliers=7)
    matrix = data_quality.build_matrix(index)
    assert len(matrix["rows"]) == 1
    zeile = matrix["rows"][0]
    assert zeile["friendly_name"] == "Gesamtenergie"
    assert zeile["total"] == 22
    assert zeile["cells"]["duplicates"]["count"] == 12
    assert zeile["cells"]["counter_decreases"]["count"] == 3
    assert zeile["cells"]["outliers"]["count"] == 7
    assert matrix["totals"]["duplicates"]["count"] == 12


def test_entitaet_nur_mit_einem_befund_hat_leere_zellen(index):
    _mit_daten(index, "sensor.a")
    _gesamt(index, "sensor.a", duplicates=5)
    zeile = data_quality.build_matrix(index)["rows"][0]
    assert zeile["cells"]["duplicates"]["count"] == 5
    assert zeile["cells"]["counter_decreases"] is None
    assert zeile["cells"]["outliers"] is None


def test_ohne_ausreisser_erkennung_zaehlen_duplikate_trotzdem(index):
    # Der Wartungsplaner misst auch Entitäten, deren Ausreißer-Erkennung aus ist,
    # sonst fehlten ihre Duplikate und Zählerrückgänge in der Matrix.
    _mit_daten(index, "sensor.a", schwelle="off")
    index.set_cleanup_alltime_stats(
        "sensor.a", {"all": 10, "duplicates": 4, "counter_decreases": 0}, "off", OUTLIER_RULE_VERSION,
        gap_threshold="15"
    )
    matrix = data_quality.build_matrix(index)
    assert matrix["pending"] == 0
    assert matrix["rows"][0]["cells"]["duplicates"]["count"] == 4


def test_sortiert_nach_summe_absteigend(index):
    for eid in ("sensor.a", "sensor.b", "sensor.c"):
        _mit_daten(index, eid)
    _gesamt(index, "sensor.a", duplicates=2, counter_decreases=1)
    _gesamt(index, "sensor.b", duplicates=9)
    _gesamt(index, "sensor.c", duplicates=5)
    reihenfolge = [r["entity_id"] for r in data_quality.build_matrix(index)["rows"]]
    assert reihenfolge == ["sensor.b", "sensor.c", "sensor.a"]


def test_ausreisser_unter_der_auffaelligkeitsmarke_zaehlen_mit(index):
    # 5 von 1000 = 0,5 %: für Housekeeping → Ausreißer unauffällig, als Anzahl
    # in der Matrix aber ein Befund.
    _mit_daten(index, "sensor.a")
    _gesamt(index, "sensor.a", outliers=5)
    zeile = data_quality.build_matrix(index)["rows"][0]
    assert zeile["cells"]["outliers"]["count"] == 5


def test_ausreisser_mit_anderer_schwelle_gezaehlt_gilt_als_ausstehend(index):
    _mit_daten(index, "sensor.a", schwelle="50")
    index.set_cleanup_alltime_stats(
        "sensor.a", {"all": 1000, "outliers": 9, "duplicates": 0, "counter_decreases": 0}, "100", OUTLIER_RULE_VERSION
    )
    matrix = data_quality.build_matrix(index)
    assert matrix["pending"] == 1
    assert matrix["rows"] == []


def test_links_tragen_filter_und_zeitraum(index):
    _mit_daten(index, "sensor.a")
    _gesamt(index, "sensor.a", duplicates=1, counter_decreases=1, outliers=2)
    zellen = data_quality.build_matrix(index)["rows"][0]["cells"]
    assert zellen["duplicates"]["href"] == "entities/sensor.a/cleanup?filter=duplicates&range=all"
    assert zellen["counter_decreases"]["href"] == (
        "entities/sensor.a/cleanup?filter=counter_decreases&range=all"
    )
    assert zellen["outliers"]["href"] == "entities/sensor.a/cleanup?filter=outliers&range=all"


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


def test_paginate_teilt_in_seiten_zu_50():
    rows = [{"n": i} for i in range(120)]
    seite, info = data_quality.paginate(rows, 1, data_quality.DEFAULT_PAGE_SIZE)
    assert len(seite) == 50
    assert (info["start"], info["end"], info["total"], info["total_pages"]) == (1, 50, 120, 3)
    seite, info = data_quality.paginate(rows, 3, 50)
    assert [r["n"] for r in seite] == list(range(100, 120))
    assert (info["start"], info["end"]) == (101, 120)


def test_paginate_klemmt_seite_und_verwirft_unbekannte_groessen():
    rows = [{"n": i} for i in range(30)]
    _, info = data_quality.paginate(rows, 99, 50)
    assert info["page"] == 1
    _, info = data_quality.paginate(rows, 1, 7)
    assert info["page_size"] == data_quality.DEFAULT_PAGE_SIZE
    _, info = data_quality.paginate(rows, 4, 10)
    assert info["page"] == 3


def test_paginate_leere_liste():
    seite, info = data_quality.paginate([], 1, 50)
    assert seite == []
    assert (info["start"], info["end"], info["total_pages"]) == (0, 0, 1)


def test_erlaubte_zaehlerrueckgaenge_werden_gekennzeichnet(index):
    """Ein Zähler mit "Zählerrückgänge = Erlauben" zählt nie Rückgänge; die leere
    Zelle soll nicht wie "nichts gefunden" aussehen. Gekennzeichnet wird nur dort,
    wo es zutrifft (Zähler, Modus allow), und nur in Zeilen, die ohnehin einen Befund haben."""
    for eid in ("sensor.zaehler", "sensor.normal"):
        index.get_or_create_entity(eid, "sensor", "total_increasing", "kWh")
        index.set_first_ts_and_add_rows(eid, 1_700_000_000.0, 0)
        _gesamt(index, eid, duplicates=3)
    index.set_counter_decreases("sensor.zaehler", "allow")
    rows = {r["entity_id"]: r for r in data_quality.build_matrix(index)["rows"]}
    assert rows["sensor.zaehler"]["counter_allowed"] is True
    assert rows["sensor.zaehler"]["cells"]["counter_decreases"] is None
    assert rows["sensor.normal"]["counter_allowed"] is False


def test_zaehler_der_klasse_total_werden_als_nicht_anwendbar_gekennzeichnet(index):
    """state_class total darf sinken: Rückgänge werden dort nie geprüft. Die leere Zelle
    soll nicht wie "nichts gefunden" aussehen, bei total_increasing bleibt sie unmarkiert."""
    for eid, klasse in (("sensor.netto", "total"), ("sensor.zaehler", "total_increasing")):
        index.get_or_create_entity(eid, "sensor", klasse, "kWh")
        index.set_first_ts_and_add_rows(eid, 1_700_000_000.0, 0)
        _gesamt(index, eid, duplicates=3)
    rows = {r["entity_id"]: r for r in data_quality.build_matrix(index)["rows"]}
    assert rows["sensor.netto"]["counter_na"] is True
    assert rows["sensor.zaehler"]["counter_na"] is False


def test_luecken_zaehlen_nur_mit_passender_schwelle(index):
    """Eine Lücken-Zahl gehört zu der Schwelle, mit der sie gezählt wurde. Nach einer
    Änderung der Schwelle darf die alte Zahl nicht stehen bleiben; die Entität gilt
    als nicht gemessen, bis der Planer sie neu gerechnet hat."""
    _mit_daten(index, "sensor.a")
    index.set_cleanup_alltime_stats(
        "sensor.a", {"all": 100, "outliers": 0, "gaps": 7, "duplicates": 0, "repetitions": 0,
                     "counter_decreases": 0},
        "50", OUTLIER_RULE_VERSION, gap_threshold="15",
    )
    matrix = data_quality.build_matrix(index)
    assert matrix["rows"][0]["cells"]["gaps"]["count"] == 7
    assert matrix["rows"][0]["cells"]["gaps"]["href"].startswith("entities/sensor.a/cleanup?filter=gaps")
    assert matrix["pending"] == 0

    index.set_config("sensor.a", gap_threshold="60")
    matrix = data_quality.build_matrix(index)
    assert matrix["rows"] == []
    assert matrix["pending"] == 1


def test_luecken_ausgeschaltet_gibt_nichts_zu_messen(index):
    _mit_daten(index, "sensor.a")
    index.set_config("sensor.a", gap_threshold="off")
    index.set_cleanup_alltime_stats(
        "sensor.a", {"all": 100, "outliers": 0, "gaps": 0, "duplicates": 2, "repetitions": 0,
                     "counter_decreases": 0},
        "50", OUTLIER_RULE_VERSION, gap_threshold="off",
    )
    matrix = data_quality.build_matrix(index)
    assert matrix["pending"] == 0
    assert matrix["rows"][0]["cells"]["gaps"] is None


def test_aufgeklappte_duplikate_fassen_ein_paar_zu_einem_eintrag_zusammen(tmp_path):
    """Ein Duplikat-Paar besteht aus zwei Zeilen mit gleichem Zeitstempel; in der
    aufgeklappten Zeile steht es einmal. Gezählt bleiben Zeilen (6 Zeilen = 3 Paare),
    damit die Zahl mit Matrix und Filter der Bereinigungsseite übereinstimmt."""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from app.storage import hotbuffer

    tz = ZoneInfo("Europe/Berlin")
    idx = Index(tmp_path / "index.sqlite")
    try:
        idx.get_or_create_entity("sensor.d", "sensor", "measurement", "kWh")
        idx.set_config("sensor.d", outlier_threshold="off", gap_threshold="off")
        first = datetime(2024, 7, 1, 8, tzinfo=tz).timestamp()
        for hour, value in enumerate((1.0, 2.0, 3.0, 4.0)):
            ts = first + hour * 3600
            hotbuffer.append(tmp_path, "sensor.d", ts, value, tz)
            if hour in (1, 2):  # zwei Paare
                hotbuffer.append(tmp_path, "sensor.d", ts, value, tz)
            idx.record_write("sensor.d", ts)
        idx.set_first_ts_and_add_rows("sensor.d", first, 0)
        now = datetime(2024, 7, 1, 12, tzinfo=tz)
        groups = data_quality.findings_for_entity(tmp_path, idx, tz, idx.get_entity("sensor.d"), now=now)
        duplikate = next(g for g in groups if g["key"] == "duplicates")
        assert duplikate["total"] == 4
        assert duplikate["shown"] == 4
        assert len(duplikate["rows"]) == 2
        assert duplikate["more"] == 0
    finally:
        idx.close()


def test_offset_fuer_einen_tag():
    from datetime import date

    heute = date(2026, 10, 14)
    assert data_quality.offset_for_day("2026-10-14", heute) == 0
    assert data_quality.offset_for_day("2026-10-10", heute) == -4
    assert data_quality.offset_for_day("2026-10-20", heute) == 0  # Zukunft
    assert data_quality.offset_for_day("quatsch", heute) == 0
    assert data_quality.offset_for_day(None, heute) == 0


def _mit_tageszahlen(index, entity_id, daily, **counts):
    _mit_daten(index, entity_id)
    index.set_cleanup_alltime_stats(
        entity_id,
        {"all": 1000, "outliers": 0, "gaps": 0, "duplicates": 0, "repetitions": 0,
         "counter_decreases": 0, **counts},
        "50", OUTLIER_RULE_VERSION, daily=daily, gap_threshold="15",
    )


def test_heatmap_zaehlt_je_tag_und_verlinkt_den_tag(index):
    from datetime import date

    heute = date(2026, 10, 14)
    _mit_tageszahlen(
        index, "sensor.a",
        {"2026-10-14": {"duplicates": 4, "gaps": 1}, "2026-10-12": {"counter_decreases": 2},
         "2020-01-01": {"duplicates": 99}},  # außerhalb des Fensters
        duplicates=103, counter_decreases=2, gaps=1,
    )
    alle = data_quality.build_heatmap(index, days=30, kind="all", today=heute)
    assert alle["day_list"][-1] == "2026-10-14" and len(alle["day_list"]) == 30
    zeile = alle["rows"][0]
    assert zeile["total"] == 7  # 4 + 1 + 2, der Tag von 2020 zählt nicht
    letzter = zeile["cells"][-1]
    assert letzter["count"] == 5 and letzter["level"] == 4
    assert letzter["kind"] == "duplicates"  # Farbton = häufigster Typ des Tages
    assert "Duplikate 4" in letzter["detail"] and "Lücken 1" in letzter["detail"]
    # Stärke je Zeile: der kleinere Tag ist schwächer als der Höchstwert der Entität
    assert zeile["cells"][-3]["level"] < letzter["level"]
    assert letzter["href"] == "entities/sensor.a/cleanup?filter=all&range=day&date=2026-10-14"
    assert zeile["cells"][-2]["href"] is None  # Tag ohne Befund: kein Link

    nur_luecken = data_quality.build_heatmap(index, days=30, kind="gaps", today=heute)
    assert nur_luecken["rows"][0]["total"] == 1
    assert nur_luecken["rows"][0]["cells"][-1]["href"].startswith("entities/sensor.a/cleanup?filter=gaps")


def test_heatmap_ohne_tageszahlen_gilt_als_nicht_gemessen(index):
    from datetime import date

    _mit_tageszahlen(index, "sensor.neu", None, duplicates=5)  # Eintrag ohne "daily"
    heatmap = data_quality.build_heatmap(index, days=30, kind="all", today=date(2026, 10, 14))
    assert heatmap["rows"] == []
    assert heatmap["pending"] == 1


def test_heatmap_ungueltige_angaben_fallen_auf_standard(index):
    from datetime import date

    heatmap = data_quality.build_heatmap(index, days=7, kind="quatsch", today=date(2026, 10, 14))
    assert heatmap["days"] == data_quality.DEFAULT_HEATMAP_DAYS and heatmap["kind"] == "all"


def test_zeitraum_der_matrix_summiert_die_tageszahlen(index):
    from datetime import date

    heute = date(2026, 10, 14)
    _mit_tageszahlen(
        index, "sensor.a",
        {"2026-10-14": {"duplicates": 4}, "2026-10-05": {"duplicates": 2},
         "2026-06-01": {"duplicates": 10}, "2020-01-01": {"duplicates": 100}},
        duplicates=116,
    )
    _mit_tageszahlen(index, "sensor.alt", None, duplicates=5)  # nie mit Tageszahlen gemessen

    def dup(period):
        matrix = data_quality.build_matrix(index, period=period, today=heute)
        return matrix["totals"]["duplicates"]["count"], matrix["pending"]

    assert dup("all") == (121, 0)  # Standard: gesamte Historie aus den Gesamtzahlen
    assert dup("year") == (16, 1)
    assert dup("30d") == (6, 1)
    assert dup("7d") == (4, 1)
    assert data_quality.build_matrix(index, period="quatsch", today=heute)["period"] == "all"


# --- Sammelaktion: Duplikate mehrerer Entitäten entfernen --------------------------------

_TZ = ZoneInfo("Europe/Berlin")
_JETZT = datetime(2024, 8, 15, 12, tzinfo=_TZ)


def _lock(_entity_id):
    return nullcontext()


@pytest.fixture()
def daten():
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-dq-bulk-"))
    idx = Index(tmp / "index.sqlite")
    try:
        yield tmp, idx
    finally:
        idx.close()
        shutil.rmtree(tmp, ignore_errors=True)


def _mit_duplikaten(tmp, idx, entity_id, zusaetzliche):
    """Ein Zeitstempel, dessen Wert (1 + zusaetzliche)-mal geschrieben ist, dazu ein unauffälliger."""
    idx.get_or_create_entity(entity_id, "sensor", "measurement", "°C")
    ts = datetime(2024, 8, 10, 8, tzinfo=_TZ).timestamp()
    for _ in range(1 + zusaetzliche):
        hotbuffer.append(tmp, entity_id, ts, 20.0, _TZ)
        idx.record_write(entity_id, ts)
    ts2 = ts + 3600
    hotbuffer.append(tmp, entity_id, ts2, 21.0, _TZ)
    idx.record_write(entity_id, ts2)
    idx.set_cleanup_alltime_stats(
        entity_id, {"all": 2 + zusaetzliche, "duplicates": zusaetzliche, "gaps": 0, "outliers": 0,
                    "repetitions": 0, "counter_decreases": 0},
        "50", OUTLIER_RULE_VERSION, daily={"2024-08-10": {"duplicates": zusaetzliche}}, gap_threshold="15",
    )


def test_sammel_vorschau_zaehlt_je_entitaet_und_laesst_saubere_weg(daten):
    tmp, idx = daten
    _mit_duplikaten(tmp, idx, "sensor.a", 2)
    _mit_duplikaten(tmp, idx, "sensor.b", 1)
    _mit_duplikaten(tmp, idx, "sensor.sauber", 0)
    vorschau = data_quality.preview_duplicate_removal(
        tmp, idx, _TZ, ["sensor.a", "sensor.b", "sensor.sauber", "sensor.gibt_es_nicht"], _lock, now=_JETZT
    )
    assert {r["entity_id"]: r["count"] for r in vorschau["rows"]} == {"sensor.a": 2, "sensor.b": 1}
    assert vorschau["total"] == 3


def test_sammel_vorschau_schreibt_nichts(daten):
    tmp, idx = daten
    _mit_duplikaten(tmp, idx, "sensor.a", 2)
    data_quality.preview_duplicate_removal(tmp, idx, _TZ, ["sensor.a"], _lock, now=_JETZT)
    assert data_quality.duplicate_timestamps(tmp, idx, _TZ, "sensor.a", idx.get_entity("sensor.a"), _JETZT)


def test_sammel_loeschen_entfernt_nur_ueberzaehlige_und_gleicht_die_zahlen_an(daten):
    tmp, idx = daten
    _mit_duplikaten(tmp, idx, "sensor.a", 2)
    _mit_duplikaten(tmp, idx, "sensor.b", 1)
    ergebnis = data_quality.remove_duplicates(tmp, idx, _TZ, ["sensor.a", "sensor.b"], _lock, now=_JETZT)
    assert ergebnis["total"] == 3
    for entity_id in ("sensor.a", "sensor.b"):
        assert data_quality.duplicate_timestamps(tmp, idx, _TZ, entity_id, idx.get_entity(entity_id), _JETZT) == []
        eintrag = idx.get_cleanup_alltime_stats(entity_id)
        assert eintrag["counts"]["duplicates"] == 0
        assert "duplicates" not in eintrag["daily"]["2024-08-10"]
        # veraltet, damit der Wartungsplaner den Rest neu misst
        assert eintrag["computed_at"] == 0


def test_sammel_loeschen_ueberspringt_entitaeten_ohne_duplikate(daten):
    tmp, idx = daten
    _mit_duplikaten(tmp, idx, "sensor.sauber", 0)
    assert data_quality.remove_duplicates(tmp, idx, _TZ, ["sensor.sauber"], _lock, now=_JETZT)["rows"] == []


def test_sammel_rueckgaengig_stellt_wieder_her_und_verwirft_die_zahlen(daten):
    tmp, idx = daten
    _mit_duplikaten(tmp, idx, "sensor.a", 2)
    data_quality.remove_duplicates(tmp, idx, _TZ, ["sensor.a"], _lock, now=_JETZT)
    assert data_quality.undo_duplicate_removal(idx, ["sensor.a"], _lock) == 2
    assert len(data_quality.duplicate_timestamps(tmp, idx, _TZ, "sensor.a", idx.get_entity("sensor.a"), _JETZT)) == 2
    eintrag = idx.get_cleanup_alltime_stats("sensor.a")
    assert "daily" not in eintrag


def test_suche_filter_und_sortierung_der_matrix(index):
    _entity(index, "sensor.alpha", name="Zebra")
    _mit_tageszahlen(index, "sensor.alpha", {}, duplicates=1, gaps=9)
    _entity(index, "sensor.beta", name="Anton")
    _mit_tageszahlen(index, "sensor.beta", {}, duplicates=7)
    _mit_tageszahlen(index, "sensor.gamma", {}, gaps=4)
    zeilen = data_quality.build_matrix(index)["rows"]

    def ids(**kw):
        return [r["entity_id"] for r in data_quality.filter_and_sort(zeilen, **kw)]

    assert ids() == ["sensor.alpha", "sensor.beta", "sensor.gamma"]  # Summe absteigend: 10, 7, 4
    assert ids(direction="asc") == ["sensor.gamma", "sensor.beta", "sensor.alpha"]
    assert ids(sort="duplicates") == ["sensor.beta", "sensor.alpha", "sensor.gamma"]
    assert ids(sort="name", direction="asc") == ["sensor.beta", "sensor.gamma", "sensor.alpha"]  # Anton, sensor.gamma, Zebra
    assert ids(kind="gaps") == ["sensor.alpha", "sensor.gamma"]
    assert ids(q="ZEB") == ["sensor.alpha"]  # Name, ohne Groß-/Kleinschreibung
    assert ids(q="sensor.gam") == ["sensor.gamma"]  # Entity-ID
    assert ids(sort="quatsch", kind="quatsch") == ids()  # Unbekanntes fällt auf den Standard zurück


def test_hinweis_auf_neue_befunde_seit_dem_letzten_besuch(index):
    def hinweis():
        return data_quality.new_findings(index)

    def neu_lesen():
        data_quality._totals_cache["key"] = None  # Zwischenspeicher der Meldungen leeren

    _mit_tageszahlen(index, "sensor.a", {}, duplicates=5)
    neu_lesen()
    assert hinweis() is None  # nie besucht: der aktuelle Stand ist der Anfang

    _mit_tageszahlen(index, "sensor.a", {}, duplicates=9)
    _mit_tageszahlen(index, "sensor.neu", {}, duplicates=100)  # beim Besuch nicht gemessen
    neu_lesen()
    assert hinweis() == {"new": 4, "entities": 1}  # nur der Zuwachs bei sensor.a

    # Besuch merken: danach nichts mehr, auch nicht für die nachgemessene Entität
    data_quality.mark_seen(index)
    neu_lesen()
    assert hinweis() is None

    # Bereinigung senkt den Stand mit, spätere neue Befunde fallen wieder auf
    _mit_tageszahlen(index, "sensor.a", {}, duplicates=1)
    neu_lesen()
    assert hinweis() is None
    _mit_tageszahlen(index, "sensor.a", {}, duplicates=3)
    neu_lesen()
    assert hinweis() == {"new": 2, "entities": 1}


def test_stand_je_zeile_und_gesamtstatus(index):
    """Jede Zeile trägt den Zeitpunkt ihrer Gesamt-Zählung; der Gesamtstatus zählt gemessene,
    veraltete und noch nicht gemessene Entitäten. Wer älter als die Erneuerungsgrenze des
    Wartungsplaners ist UND seitdem neue Werte bekam, gilt als veraltet; eine stille Entität
    erneuert der Planer nicht und sie bleibt aktuell."""
    import time

    from app import cleanup_stats

    _mit_daten(index, "sensor.a")
    _gesamt(index, "sensor.a", duplicates=2)
    _mit_daten(index, "sensor.b")
    _gesamt(index, "sensor.b", duplicates=1)
    _mit_daten(index, "sensor.offen")  # nie gemessen
    index.record_write("sensor.a", time.time() + 30)  # neue Werte nach der Messung; sensor.b bleibt still

    jetzt = time.time()
    matrix = data_quality.build_matrix(index, now=jetzt + 120)
    zeilen = {r["entity_id"]: r for r in matrix["rows"]}
    assert zeilen["sensor.a"]["measured_label"] == "vor 2 Min."
    assert zeilen["sensor.a"]["measured_old"] is False
    assert matrix["status"]["total"] == 3
    assert matrix["status"]["measured"] == 2
    assert matrix["status"]["stale"] == 0
    assert matrix["status"]["oldest_label"] == "vor 2 Min."

    spaeter = jetzt + cleanup_stats.OUTLIER_RATE_REFRESH_AGE_SECONDS + 60
    matrix = data_quality.build_matrix(index, now=spaeter)
    zeilen = {r["entity_id"]: r for r in matrix["rows"]}
    assert zeilen["sensor.a"]["measured_old"] is True
    assert zeilen["sensor.b"]["measured_old"] is False  # still: nichts zu erneuern
    assert matrix["status"]["stale"] == 1
    assert matrix["status"]["stale_hours"] == 6


def test_entitaet_ohne_daten_zaehlt_nicht_im_gesamtstatus(index):
    _entity(index, "sensor.leer")
    assert data_quality.build_matrix(index)["status"]["total"] == 0


def test_sortierung_nach_stand_aelteste_zuerst(index):
    import time

    for name, duplikate in (("sensor.alt", 1), ("sensor.neu", 5)):
        _mit_daten(index, name)
        _gesamt(index, name, duplicates=duplikate)
    # Die ältere Messung nachträglich zurückdatieren.
    eintrag = index.get_cleanup_alltime_stats("sensor.alt")
    eintrag["computed_at"] = time.time() - 3 * 3600
    import json

    index.set_setting(index._CLEANUP_ALLTIME_STATS_PREFIX + "sensor.alt", json.dumps(eintrag))

    rows = data_quality.build_matrix(index)["rows"]
    aufsteigend = data_quality.filter_and_sort(rows, sort="measured", direction="asc")
    assert [r["entity_id"] for r in aufsteigend] == ["sensor.alt", "sensor.neu"]
    absteigend = data_quality.filter_and_sort(rows, sort="measured", direction="desc")
    assert [r["entity_id"] for r in absteigend] == ["sensor.neu", "sensor.alt"]
