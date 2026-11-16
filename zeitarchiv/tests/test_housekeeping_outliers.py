"""Housekeeping → Ausreißer: die Liste, die Meldung und woher die Zahlen kommen.

Der Abschnitt zeigt Entitäten, deren Ausreißer-Schwelle einen auffälligen
Anteil ihrer Werte markiert. Die entscheidende Zusage ist nicht die Liste
selbst, sondern dass sie DIESELBEN Zahlen zeigt wie das Schwellenfeld der
jeweiligen Entität — zwei Stellen mit zwei verschieden gemessenen
„Markierungsquoten" wären genau die Verwirrung, die eine Übersicht auflösen
soll. Deshalb rechnet hier nichts nach; gefüllt wird der Cache vom
Wartungsplaner, je Takt im Zeitbudget.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import time
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from _paths import DOCS
from app import cleanup_stats
from app.notices import build_notices
from app.storage.cleanup import OUTLIER_RULE_VERSION
from app.storage.index import Index

TZ = ZoneInfo("Europe/Berlin")


@pytest.fixture()
def index():
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-hk-outlier-"))
    idx = Index(tmp / "index.sqlite")
    try:
        yield idx
    finally:
        idx.close()
        shutil.rmtree(tmp, ignore_errors=True)


def _entity(index, entity_id, *, schwelle="50", markiert=None, gesamt=1000,
            gezaehlt_mit=None, alter=0.0, name=None):
    """Legt eine Entität an und schiebt ihr optional einen Cache-Eintrag unter —
    so, wie ihn ein Lauf über die komplette Historie hinterlassen würde."""
    index.get_or_create_entity(entity_id, "sensor", "measurement", "°C")
    index.set_config(entity_id, outlier_threshold=schwelle, custom_name=name)
    if markiert is not None:
        index.set_cleanup_alltime_stats(
            entity_id, {"all": gesamt, "outliers": markiert},
            schwelle if gezaehlt_mit is None else gezaehlt_mit,
            OUTLIER_RULE_VERSION, gap_threshold="r15", daily={},
            outlier_profile={"rule": OUTLIER_RULE_VERSION, "days": 60.0, "judged": gesamt, "blind": 0, "tiers": {}},
        )
        if alter:
            eintrag = index.get_cleanup_alltime_stats(entity_id)
            eintrag["computed_at"] = time.time() - alter
            index.set_setting("cleanup_alltime_stats:" + entity_id, json.dumps(eintrag))


def _notices(index, db_path):
    return build_notices(
        index, db_path, TZ,
        purge_totals={"removable_rows": 0, "entities_affected": 0},
        storage_reconcile=None, stale_entity_count=0,
        scheduler_last_tick=time.time(), reconcile_last_tick=time.time(),
        reconcile_in_progress=False,
    )


# --- Die Liste ---------------------------------------------------------------


def test_only_conspicuous_entities_are_listed(index) -> None:
    """Die Marke steht bei 1 %: Ausreißer sind per Definition selten, wer jeden
    hundertsten Wert markiert, sucht keine Fehler mehr."""
    _entity(index, "sensor.viel", markiert=50, gesamt=1000)     # 5 %
    _entity(index, "sensor.knapp", markiert=9, gesamt=1000)     # 0,9 %
    _entity(index, "sensor.ruhig", markiert=0, gesamt=1000)

    uebersicht = cleanup_stats.outlier_rate_overview(index)
    assert [r["entity_id"] for r in uebersicht["rows"]] == ["sensor.viel"]
    assert uebersicht["measured"] == 3
    assert uebersicht["pending"] == 0


def test_the_list_is_sorted_by_rate(index) -> None:
    _entity(index, "sensor.a", markiert=20, gesamt=1000)
    _entity(index, "sensor.b", markiert=90, gesamt=1000)
    _entity(index, "sensor.c", markiert=40, gesamt=1000)
    zeilen = cleanup_stats.outlier_rate_overview(index)["rows"]
    assert [r["entity_id"] for r in zeilen] == ["sensor.b", "sensor.c", "sensor.a"]


def test_a_rate_counted_with_another_threshold_does_not_appear(index) -> None:
    """Dieselbe Zusage wie am Schwellenfeld: eine Zahl neben einer Schwelle,
    zu der sie nicht gehört, wäre schlimmer als keine Zahl. Sie zählt hier als
    „noch nicht gemessen", nicht als 0 % — sonst behauptete die Übersicht
    Unauffälligkeit, wo nichts gemessen wurde."""
    _entity(index, "sensor.alt", schwelle="50", markiert=500, gesamt=1000,
            gezaehlt_mit="10")
    uebersicht = cleanup_stats.outlier_rate_overview(index)
    assert uebersicht["rows"] == []
    assert (uebersicht["measured"], uebersicht["pending"]) == (0, 1)


def test_entities_with_detection_off_are_not_counted_as_pending(index) -> None:
    """Sonst stünde dauerhaft „3 von 40 gemessen", obwohl für 37 nichts zu
    messen ist."""
    _entity(index, "sensor.aus", schwelle="off")
    index.get_or_create_entity("binary_sensor.schalter", "binary_sensor", "measurement", None)
    _entity(index, "sensor.an", markiert=5, gesamt=1000)

    uebersicht = cleanup_stats.outlier_rate_overview(index)
    assert (uebersicht["measured"], uebersicht["pending"]) == (1, 0)


def test_an_entity_without_values_is_not_a_zero_percent_entry(index) -> None:
    """0 von 0 ist keine Quote, sondern eine Division."""
    _entity(index, "sensor.leer", markiert=0, gesamt=0)
    uebersicht = cleanup_stats.outlier_rate_overview(index)
    assert uebersicht["highest"] is None
    assert uebersicht["pending"] == 1


def test_the_highest_value_is_reported_even_when_nothing_is_conspicuous(index) -> None:
    """Ein leerer Abschnitt, der den höchsten gemessenen Wert nennt, belegt,
    dass gemessen wurde — „nichts gefunden" allein sieht genauso aus wie
    „noch nichts gemessen"."""
    _entity(index, "sensor.a", markiert=2, gesamt=1000)
    _entity(index, "sensor.b", markiert=7, gesamt=1000)
    uebersicht = cleanup_stats.outlier_rate_overview(index)
    assert uebersicht["rows"] == []
    assert uebersicht["highest"]["entity_id"] == "sensor.b"
    assert uebersicht["highest"]["percent_label"] == "0,70"


def test_the_labels_are_german_and_shared_with_the_form_field(index) -> None:
    """Dieselbe Aufbereitung wie unter dem Schwellenfeld — sonst stünde an der
    einen Stelle „1.5" und an der anderen „1,50"."""
    _entity(index, "sensor.viel", schwelle="20", markiert=1500, gesamt=100000)
    zeile = cleanup_stats.outlier_rate_overview(index)["rows"][0]
    assert zeile["percent_label"] == "1,50"
    assert zeile["marked_label"] == "1.500"
    assert zeile["total_label"] == "100.000"
    assert zeile["threshold_label"] == "20×"

    vom_feld = cleanup_stats.outlier_rate(index, index.get_entity("sensor.viel"))
    assert cleanup_stats.rate_labels(vom_feld)["percent_label"] == zeile["percent_label"]
    assert vom_feld["marked"] == zeile["marked"]


# --- Die Meldung -------------------------------------------------------------


def _judged(index, entity_id, tiers, *, schwelle="20", days=60.0):
    """Entität mit Stufen-Profil im Cache, so wie es der Gesamt-Durchlauf ablegt (Treffer je Stufe über `days` Tage)."""
    index.get_or_create_entity(entity_id, "sensor", "measurement", "°C")
    index.set_config(entity_id, outlier_threshold=schwelle)
    index.record_write(entity_id, time.time())
    index.set_cleanup_alltime_stats(
        entity_id, {"all": 5000, "outliers": 0}, schwelle, OUTLIER_RULE_VERSION, gap_threshold="r15", daily={},
        outlier_profile={"rule": OUTLIER_RULE_VERSION, "days": days, "judged": 5000, "blind": 0, "tiers": tiers},
    )


NOISY = {"10": 900, "20": 900, "50": 900, "100": 0}      # Grenzfälle auf allen feinen Stufen, bei 100× keine
UNSUITABLE = {"10": 900, "20": 800, "50": 700, "100": 600}  # auch bei 100× laut


def test_the_notice_appears_once_for_the_whole_installation(index) -> None:
    """Eine Meldung, egal ob eine oder hundert Entitäten betroffen sind: das
    Meldungs-Center trägt Titel, zwei Sätze und einen Link, und hundert
    einzelne Meldungen müsste man hundertmal stummschalten."""
    for i in range(100):
        _judged(index, f"sensor.v{i:03d}", NOISY)

    treffer = [n for n in _notices(index, Path("x")) if n["id"] == "entities.outlier_rate_high"]
    assert len(treffer) == 1
    assert treffer[0]["link"] == "/housekeeping#ausreisser"
    assert "100 mit zu empfindlicher Schwelle" in treffer[0]["detail"]


def test_the_notice_counts_both_kinds_and_stays_short(index) -> None:
    _judged(index, "sensor.zu_eng", NOISY)
    _judged(index, "sensor.zu_eng2", NOISY)
    _judged(index, "sensor.laut", UNSUITABLE, schwelle="50")

    meldung = next(n for n in _notices(index, Path("x")) if n["id"] == "entities.outlier_rate_high")
    assert "2 mit zu empfindlicher Schwelle" in meldung["detail"]
    assert "1 mit vielen Treffern auch bei 100×" in meldung["detail"]
    assert "sensor." not in meldung["detail"]
    assert len(meldung["detail"]) < 250


def test_the_notice_names_only_the_kind_that_exists(index) -> None:
    _judged(index, "sensor.laut", UNSUITABLE, schwelle="50")
    meldung = next(n for n in _notices(index, Path("x")) if n["id"] == "entities.outlier_rate_high")
    assert "zu empfindlicher" not in meldung["detail"]
    assert "vielen Treffern auch bei 100×" in meldung["detail"]


def test_no_notice_without_conspicuous_entities(index) -> None:
    _judged(index, "sensor.ruhig", {"10": 0, "20": 0, "50": 0, "100": 0})
    _entity(index, "sensor.ungemessen")
    ids = [n["id"] for n in _notices(index, Path("x"))]
    assert "entities.outlier_rate_high" not in ids


def test_a_rate_counted_with_an_older_rule_does_not_appear(index) -> None:
    """Der Fehler, der diese Kennung nötig gemacht hat, an einer echten
    Installation beobachtet: Die Umstellung von Prozent auf Vielfache ließ die
    Schwelle "50" ein gültiger Wert bleiben — aus "50 %" wurde "50×". Ein vor
    der Umstellung gezähltes Ergebnis passte damit weiterhin zur eingestellten
    Schwelle, und Housekeeping zeigte es als aktuelle Quote an: 2.406 von
    52.194 markierten Werten an einer Entität, an der die neue Regel 0 findet.

    Die Schwelle allein kann eine Zählung also nicht ausweisen. Ein Eintrag der
    Vorgängerregel zählt als "noch nicht gemessen", nicht als 0 %.
    """
    _entity(index, "sensor.altregel", schwelle="50", markiert=2406, gesamt=52194)
    eintrag = index.get_cleanup_alltime_stats("sensor.altregel")
    eintrag["outlier_rule"] = OUTLIER_RULE_VERSION - 1
    index.set_setting("cleanup_alltime_stats:sensor.altregel", json.dumps(eintrag))

    uebersicht = cleanup_stats.outlier_rate_overview(index)
    assert uebersicht["rows"] == []
    assert (uebersicht["measured"], uebersicht["pending"]) == (0, 1)
    assert cleanup_stats.outlier_rate(index, index.get_entity("sensor.altregel")) is None


def test_an_entry_from_before_the_rule_was_versioned_counts_as_unknown(index) -> None:
    """Einträge aus der Zeit vor diesem Feld tragen es gar nicht — sie stammen
    per Definition aus einer anderen Regel."""
    _entity(index, "sensor.uralt", schwelle="50", markiert=500, gesamt=1000)
    eintrag = index.get_cleanup_alltime_stats("sensor.uralt")
    del eintrag["outlier_rule"]
    index.set_setting("cleanup_alltime_stats:sensor.uralt", json.dumps(eintrag))

    assert cleanup_stats.outlier_rate_overview(index)["rows"] == []


def test_the_stored_rule_version_matches_the_detector() -> None:
    """Wer OutlierDetector ändert, ohne die Kennung hochzuzählen, bekommt genau
    den Fehler oben zurück — deshalb steht die Zahl an einer Stelle."""
    from app.storage import cleanup as cleanup_mod

    assert cleanup_mod.OUTLIER_RULE_VERSION == 4


# --- Woher die Zahlen kommen -------------------------------------------------


def test_never_measured_entities_come_first(index) -> None:
    """Ihre Quote zeigt die Oberfläche gar nicht an — sie sind der Grund, warum
    der Hintergrundlauf existiert."""
    _entity(index, "sensor.alt", markiert=5, gesamt=1000,
            alter=cleanup_stats.OUTLIER_RATE_REFRESH_AGE_SECONDS + 60)
    _entity(index, "sensor.nie")
    index.record_write("sensor.nie", time.time())
    index.record_write("sensor.alt", time.time())

    naechste = cleanup_stats.next_entity_for_outlier_rate(index, time.time())
    assert naechste["entity_id"] == "sensor.nie"


def test_the_oldest_entry_is_refreshed_next(index) -> None:
    _entity(index, "sensor.aelter", markiert=5, gesamt=1000,
            alter=cleanup_stats.OUTLIER_RATE_REFRESH_AGE_SECONDS + 3600)
    _entity(index, "sensor.juenger", markiert=5, gesamt=1000,
            alter=cleanup_stats.OUTLIER_RATE_REFRESH_AGE_SECONDS + 60)
    for eid in ("sensor.aelter", "sensor.juenger"):
        index.record_write(eid, time.time())

    naechste = cleanup_stats.next_entity_for_outlier_rate(index, time.time())
    assert naechste["entity_id"] == "sensor.aelter"


def test_nothing_is_refreshed_while_everything_is_fresh(index) -> None:
    """Sonst liefe der Wartungsplaner alle 30 Sekunden über einen Vollscan —
    das ist der Grund für die Altersschwelle."""
    _entity(index, "sensor.frisch", markiert=5, gesamt=1000)
    index.record_write("sensor.frisch", time.time())
    assert cleanup_stats.next_entity_for_outlier_rate(index, time.time()) is None


def test_an_entity_without_data_is_never_scheduled(index) -> None:
    """Ein Vollscan über eine Entität ohne einen einzigen Rohwert kostet nur
    Zeit und hinterlässt keine Quote."""
    _entity(index, "sensor.ohne_werte")
    assert cleanup_stats.next_entity_for_outlier_rate(index, time.time()) is None


def test_the_scheduler_actually_runs_this_step() -> None:
    """Ohne den Aufruf im Takt bliebe die Liste leer, und niemand merkte es."""
    from pathlib import Path as _Path

    import app.background as background

    quelle = _Path(background.__file__).read_text(encoding="utf-8")
    assert "self._refresh_due_outlier_rates()" in quelle
    assert "due_entities_for_outlier_rate" in quelle


# --- Die Seite selbst --------------------------------------------------------


def test_the_section_sits_between_duplicates_and_storage(client) -> None:
    """Die Reihenfolge ist Teil der Aussage: erst was in den Daten steckt
    (Duplikate, Ausreißer), dann der Speicher."""
    html = client.get("/housekeeping").text
    for marke in ('id="duplikate"', 'id="ausreisser"', 'id="speicherplatz"'):
        assert marke in html, marke
    assert html.index('id="duplikate"') < html.index('id="ausreisser"') < html.index('id="speicherplatz"')
    # Nur INNERHALB der Seitennavigation vergleichen: dieselben Anker stehen
    # auch in Meldungs-Links, die je nach Zustand der Testinstanz vor der
    # Navigation stehen können.
    nav = html[html.index('class="settings-nav"'):]
    nav = nav[:nav.index("</nav>")]
    assert nav.index("housekeeping#duplikate") < nav.index("housekeeping#ausreisser") < nav.index("housekeeping#speicherplatz")


def test_the_page_shows_the_assessment_filters(client) -> None:
    html = client.get("/housekeeping").text
    abschnitt = html[html.index('id="ausreisser"'):html.index('id="luecken-erkennung"')]
    assert 'id="outlier-overview"' in abschnitt and "data-gc-root" in abschnitt
    for text in ("Zu empfindlich", "Nicht geeignet", "Greift kaum", "Passt", "Ausgeschaltet"):
        assert text in abschnitt or "Keine Entität hat die Ausreißer-Erkennung eingeschaltet." in abschnitt


def test_the_manual_names_the_same_mark_and_interval_as_the_code() -> None:
    """Die Marke und der Auffrisch-Abstand stehen als Zahl im Handbuch. Genau
    solche Zahlen veralten still, wenn jemand die Konstante ändert."""
    kapitel = (DOCS / "user-guide.md").read_text(encoding="utf-8")
    abschnitt = kapitel[kapitel.index("## Housekeeping"):kapitel.index("### Tipps")]
    flach = " ".join(abschnitt.split())

    marke = int(cleanup_stats.OUTLIER_RATE_NOTABLE_PERCENT)
    assert cleanup_stats.OUTLIER_RATE_NOTABLE_PERCENT == marke, "Nachkommastelle nicht beschrieben"
    assert f"mehr als {marke} % ihrer Werte markiert" in flach
    assert f"mehr als {marke} % aller Werte" in flach

    # Im Handbuch ausgeschrieben ("sechs Stunden"), deshalb hier über die
    # Zahl geprüft statt über den Wortlaut.
    assert cleanup_stats.OUTLIER_RATE_REFRESH_AGE_SECONDS // 3600 == 6
    assert "etwa alle sechs Stunden auf" in flach


def test_der_planer_misst_auch_entitaeten_ohne_ausreisser_erkennung(index):
    """Ihre Gesamt-Zählung liefert Duplikate und Zählerrückgänge für die
    Datenqualität; ohne Messung fehlten sie dort dauerhaft."""
    _entity(index, "sensor.pytest_ohne_erkennung", schwelle="off")
    index.set_first_ts_and_add_rows("sensor.pytest_ohne_erkennung", 1_700_000_000.0, 0)
    assert cleanup_stats.next_entity_for_outlier_rate(index, time.time())["entity_id"] == "sensor.pytest_ohne_erkennung"
    index.set_cleanup_alltime_stats("sensor.pytest_ohne_erkennung", {"all": 10}, "off", OUTLIER_RULE_VERSION, gap_threshold="r15", daily={})
    assert cleanup_stats.next_entity_for_outlier_rate(index, time.time()) is None


def test_der_planer_ueberspringt_entitaeten_ohne_daten(index):
    _entity(index, "sensor.pytest_leer", schwelle="50")
    assert cleanup_stats.next_entity_for_outlier_rate(index, time.time()) is None


def test_eintraege_ohne_tageszahlen_werden_neu_gemessen(index):
    _entity(index, "sensor.pytest_ohne_tage", schwelle="off")
    index.set_first_ts_and_add_rows("sensor.pytest_ohne_tage", 1_700_000_000.0, 0)
    index.set_cleanup_alltime_stats("sensor.pytest_ohne_tage", {"all": 10}, "off", OUTLIER_RULE_VERSION, gap_threshold="r15")
    assert cleanup_stats.next_entity_for_outlier_rate(index, time.time())["entity_id"] == "sensor.pytest_ohne_tage"


# --- Nur bei Bedarf messen, im Zeitbudget ------------------------------------


def test_eine_stille_entitaet_wird_nicht_neu_gemessen(index):
    """Ohne neue Werte ergäbe ein Vollscan dieselben Zahlen — bei vielen
    Konfigurationswerten wäre das die meiste Arbeit des Planers."""
    _entity(index, "sensor.still", markiert=5, gesamt=1000,
            alter=cleanup_stats.OUTLIER_RATE_REFRESH_AGE_SECONDS + 3600)
    index.set_first_ts_and_add_rows("sensor.still", 1_700_000_000.0, 0)
    assert cleanup_stats.next_entity_for_outlier_rate(index, time.time()) is None


def test_neue_werte_machen_eine_alte_messung_faellig(index):
    _entity(index, "sensor.aktiv", markiert=5, gesamt=1000,
            alter=cleanup_stats.OUTLIER_RATE_REFRESH_AGE_SECONDS + 3600)
    index.record_write("sensor.aktiv", time.time())
    assert cleanup_stats.next_entity_for_outlier_rate(index, time.time())["entity_id"] == "sensor.aktiv"


def test_neue_werte_allein_genuegen_nicht_vor_ablauf_der_altersgrenze(index):
    _entity(index, "sensor.frisch_aktiv", markiert=5, gesamt=1000, alter=60)
    index.record_write("sensor.frisch_aktiv", time.time())
    assert cleanup_stats.next_entity_for_outlier_rate(index, time.time()) is None


def test_ein_zurueckgesetzter_eintrag_ist_auch_ohne_neue_werte_faellig(index):
    """Duplikate entfernen und „Rückgängig“ setzen computed_at auf 0 und
    verlassen sich darauf, dass der Planer neu misst."""
    _entity(index, "sensor.zurueckgesetzt", markiert=5, gesamt=1000)
    index.set_first_ts_and_add_rows("sensor.zurueckgesetzt", 1_700_000_000.0, 0)
    eintrag = index.get_cleanup_alltime_stats("sensor.zurueckgesetzt")
    eintrag["computed_at"] = 0
    index.set_setting("cleanup_alltime_stats:sensor.zurueckgesetzt", json.dumps(eintrag))
    assert cleanup_stats.next_entity_for_outlier_rate(index, time.time())["entity_id"] == "sensor.zurueckgesetzt"


def test_faellige_entitaeten_kommen_nie_gemessene_zuerst_dann_die_aeltesten(index):
    alt = cleanup_stats.OUTLIER_RATE_REFRESH_AGE_SECONDS
    _entity(index, "sensor.juenger", markiert=5, gesamt=1000, alter=alt + 60)
    _entity(index, "sensor.aelter", markiert=5, gesamt=1000, alter=alt + 3600)
    _entity(index, "sensor.nie")
    for eid in ("sensor.juenger", "sensor.aelter", "sensor.nie"):
        index.record_write(eid, time.time())
    reihenfolge = [e["entity_id"] for e in cleanup_stats.due_entities_for_outlier_rate(index, time.time())]
    assert reihenfolge == ["sensor.nie", "sensor.aelter", "sensor.juenger"]


def _planer_mit(index, faellige, monkeypatch, *, budget, dauer):
    """BackgroundService-Rumpf, der nur _refresh_due_outlier_rates braucht;
    jede „Messung“ kostet `dauer` Sekunden auf der (gefälschten) Uhr."""
    import threading

    import app.background as background

    uhr = {"jetzt": 1000.0}
    gemessen: list[str] = []
    monkeypatch.setattr(background.time, "monotonic", lambda: uhr["jetzt"])
    monkeypatch.setattr(cleanup_stats, "OUTLIER_RATE_TICK_BUDGET_SECONDS", budget)
    monkeypatch.setattr(cleanup_stats, "due_entities_for_outlier_rate", lambda _i, _n: faellige)

    def messen(_d, _i, _tz, entity, _now, force=False):
        gemessen.append(entity["entity_id"])
        uhr["jetzt"] += dauer

    monkeypatch.setattr(cleanup_stats, "alltime_counts", messen)
    dienst = background.BackgroundService.__new__(background.BackgroundService)
    dienst.index, dienst.data_dir, dienst.tz = index, Path("."), TZ
    dienst._maintenance_scheduler_stop = threading.Event()
    return dienst, gemessen


def test_der_takt_misst_bis_das_zeitbudget_verbraucht_ist(index, monkeypatch):
    faellige = [{"entity_id": f"sensor.e{i}"} for i in range(10)]
    dienst, gemessen = _planer_mit(index, faellige, monkeypatch, budget=10.0, dauer=3.0)
    dienst._refresh_due_outlier_rates()
    assert gemessen == ["sensor.e0", "sensor.e1", "sensor.e2", "sensor.e3"]  # nach 12 s Schluss


def test_der_takt_misst_mindestens_eine_entitaet_auch_wenn_sie_das_budget_sprengt(index, monkeypatch):
    faellige = [{"entity_id": "sensor.gross"}, {"entity_id": "sensor.zweite"}]
    dienst, gemessen = _planer_mit(index, faellige, monkeypatch, budget=10.0, dauer=60.0)
    dienst._refresh_due_outlier_rates()
    assert gemessen == ["sensor.gross"]


# --- Stufen-Profil der Empfehlung --------------------------------------------


def test_entry_without_profile_is_due_again(index) -> None:
    """Einträge aus der Zeit vor dem Stufen-Profil werden einmal neu gemessen."""
    _entity(index, "sensor.profil", markiert=0, gesamt=1000)
    index.record_write("sensor.profil", time.time())
    assert cleanup_stats.next_entity_for_outlier_rate(index, time.time()) is None

    eintrag = index.get_cleanup_alltime_stats("sensor.profil")
    eintrag.pop("outlier_profile")
    index.set_setting("cleanup_alltime_stats:sensor.profil", json.dumps(eintrag))
    assert cleanup_stats.next_entity_for_outlier_rate(index, time.time())["entity_id"] == "sensor.profil"


def test_profile_of_an_older_rule_is_due_again(index) -> None:
    _entity(index, "sensor.regel", markiert=0, gesamt=1000)
    index.record_write("sensor.regel", time.time())
    eintrag = index.get_cleanup_alltime_stats("sensor.regel")
    eintrag["outlier_profile"]["rule"] = OUTLIER_RULE_VERSION - 1
    index.set_setting("cleanup_alltime_stats:sensor.regel", json.dumps(eintrag))
    assert cleanup_stats.next_entity_for_outlier_rate(index, time.time())["entity_id"] == "sensor.regel"


def test_switched_off_entity_needs_no_profile(index) -> None:
    """Aus-Entitäten bekommen bewusst kein Profil (Kosten, bewusste Wahl)."""
    index.get_or_create_entity("sensor.aus", "sensor", "measurement", "°C")
    index.set_config("sensor.aus", outlier_threshold="off")
    index.set_cleanup_alltime_stats("sensor.aus", {"all": 10}, "off", OUTLIER_RULE_VERSION, gap_threshold="r15", daily={})
    index.record_write("sensor.aus", time.time())
    assert cleanup_stats.next_entity_for_outlier_rate(index, time.time()) is None


def test_analyzer_returns_profile_only_when_asked() -> None:
    from zoneinfo import ZoneInfo

    from app.storage import cleanup as cleanup_mod

    tz = ZoneInfo("Europe/Berlin")
    rows = [(i * 60.0, 20.0 + (i % 3) * 0.5) for i in range(200)]
    rows[150] = (150 * 60.0, 5000.0)  # echter Fehler: auf jeder Stufe
    rows[160] = (160 * 60.0, 20.0 + 14 * 0.5 * 1.0)  # Grenzfall: nur auf feinen Stufen

    def lauf(**kw):
        return cleanup_mod.analyze_raw_rows_page(
            lambda: iter(rows), filter_="all", page=1, page_size=1, gap_threshold_minutes=None,
            outlier_factor=50.0, tz=tz, **kw,
        )

    assert "outlier_profile" not in lauf()
    profil = lauf(outlier_tiers_since=0.0)["outlier_profile"]
    assert profil["rule"] == OUTLIER_RULE_VERSION
    assert profil["tiers"]["100"] == 1 and profil["tiers"]["10"] >= profil["tiers"]["100"]
    assert profil["judged"] > 150 and profil["blind"] == 0
    assert lauf()["counts"] == lauf(outlier_tiers_since=0.0)["counts"]


def test_profile_counts_one_hit_per_timestamp_and_only_inside_the_window() -> None:
    from zoneinfo import ZoneInfo

    from app.storage import cleanup as cleanup_mod

    tz = ZoneInfo("Europe/Berlin")
    rows = [(i * 60.0, 10.0 + (i % 2)) for i in range(100)]
    rows += [(6000.0, 9000.0), (6000.0, 9500.0)]  # derselbe Zeitstempel: eine Gruppe
    rows.append((50.0 * 60, 9999.0))  # unsortiert vermeiden: Fehler vor dem Fenster separat prüfen
    rows.sort()
    ergebnis = cleanup_mod.analyze_raw_rows_page(
        lambda: iter(rows), filter_="all", page=1, page_size=1, gap_threshold_minutes=None,
        outlier_factor=50.0, tz=tz, outlier_tiers_since=5000.0,
    )["outlier_profile"]
    assert ergebnis["tiers"]["100"] == 1  # Fehler bei 6000 s zählt einmal, der bei 3000 s liegt vor dem Fenster
