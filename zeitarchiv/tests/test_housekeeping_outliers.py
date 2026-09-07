"""Housekeeping → Ausreißer: die Liste, die Meldung und woher die Zahlen kommen.

Der Abschnitt zeigt Entitäten, deren Ausreißer-Schwelle einen auffälligen
Anteil ihrer Werte markiert. Die entscheidende Zusage ist nicht die Liste
selbst, sondern dass sie DIESELBEN Zahlen zeigt wie das Schwellenfeld der
jeweiligen Entität — zwei Stellen mit zwei verschieden gemessenen
„Markierungsquoten" wären genau die Verwirrung, die eine Übersicht auflösen
soll. Deshalb rechnet hier nichts nach; gefüllt wird der Cache vom
Wartungsplaner, eine Entität je Takt.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import time
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

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
            OUTLIER_RULE_VERSION,
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


def test_the_notice_appears_once_for_the_whole_installation(index) -> None:
    """Eine Meldung, egal ob eine oder hundert Entitäten betroffen sind: das
    Meldungs-Center trägt Titel, zwei Sätze und einen Link, und hundert
    einzelne Meldungen müsste man hundertmal stummschalten."""
    for i in range(100):
        _entity(index, f"sensor.v{i:03d}", markiert=50 + i, gesamt=1000)

    treffer = [n for n in _notices(index, Path("x")) if n["id"] == "entities.outlier_rate_high"]
    assert len(treffer) == 1
    assert treffer[0]["link"] == "/housekeeping#ausreisser"
    assert "100 Entitäten" in treffer[0]["detail"]


def test_the_notice_names_the_worst_case_and_stays_one_sentence(index) -> None:
    _entity(index, "sensor.schlimm", markiert=90, gesamt=1000, name="PV-Leistung")
    _entity(index, "sensor.mittel", markiert=20, gesamt=1000)

    meldung = next(n for n in _notices(index, Path("x")) if n["id"] == "entities.outlier_rate_high")
    assert "PV-Leistung mit 9,00 %" in meldung["detail"]
    assert "sensor.mittel" not in meldung["detail"]
    # Nicht länger werdend: die Zahl ändert sich, der Satzbau nicht.
    assert len(meldung["detail"]) < 250


def test_the_notice_is_grammatical_for_a_single_entity(index) -> None:
    _entity(index, "sensor.eine", markiert=90, gesamt=1000, name="PV-Leistung")
    meldung = next(n for n in _notices(index, Path("x")) if n["id"] == "entities.outlier_rate_high")
    assert "Bei 1 Entität markiert" in meldung["detail"]
    assert "Entitäten" not in meldung["detail"]
    assert meldung["detail"].endswith(
        "Eine zu enge Schwelle markiert normales Verhalten als verdächtig."
    )


def test_no_notice_without_conspicuous_entities(index) -> None:
    _entity(index, "sensor.ruhig", markiert=2, gesamt=1000)
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

    assert cleanup_mod.OUTLIER_RULE_VERSION == 2


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
    assert "self._refresh_one_outlier_rate()" in quelle
    assert "next_entity_for_outlier_rate" in quelle


# --- Die Seite selbst --------------------------------------------------------


def test_the_section_sits_between_duplicates_and_configuration(client) -> None:
    """Die Reihenfolge ist Teil der Aussage: erst was in den Daten steckt
    (Duplikate, Ausreißer), dann was an den Einstellungen liegt."""
    html = client.get("/housekeeping").text
    for marke in ('id="duplikate"', 'id="ausreisser"', 'id="konfiguration"'):
        assert marke in html, marke
    assert html.index('id="duplikate"') < html.index('id="ausreisser"') < html.index('id="konfiguration"')
    # Nur INNERHALB der Seitennavigation vergleichen: dieselben Anker stehen
    # auch in Meldungs-Links, die je nach Zustand der Testinstanz vor der
    # Navigation stehen können.
    nav = html[html.index('class="settings-nav"'):]
    nav = nav[:nav.index("</nav>")]
    assert nav.index("housekeeping#duplikate") < nav.index("housekeeping#ausreisser") < nav.index("housekeeping#konfiguration")


def test_the_page_shows_how_far_the_measurement_got(client) -> None:
    """Ohne diese Zahl sähe „nichts gefunden" genauso aus wie „noch nichts
    gemessen" — der Unterschied ist für den Leser wesentlich."""
    html = client.get("/housekeeping").text
    abschnitt = html[html.index('id="ausreisser"'):html.index('id="konfiguration"')]
    assert "Entitäten mit Ausreißer-Erkennung sind gemessen" in abschnitt


def test_the_manual_names_the_same_mark_and_interval_as_the_code() -> None:
    """Die Marke und der Auffrisch-Abstand stehen als Zahl im Handbuch. Genau
    solche Zahlen veralten still, wenn jemand die Konstante ändert."""
    kapitel = (
        Path(__file__).resolve().parents[1] / "docs" / "user-guide.md"
    ).read_text(encoding="utf-8")
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
