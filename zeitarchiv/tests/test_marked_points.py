"""Zur Löschung markierte Bereiche sind bis zum Purge sichtbar.

„Löschen" auf der Bereinigungsseite ist ein Soft-Delete: die Zeile bleibt
liegen, bis der Purge sie physisch entfernt. Gleichzeitig filtert jeder
Lesepfad sie sofort heraus, damit ein Chart nach dem Markieren ohne sie
aussieht. Damit war sie aber auch aus jeder Ansicht verschwunden — nachsehen
ließ sich nur eine Zeitstempel-Tabelle unter Housekeeping → Speicherplatz,
losgelöst von der Kurve, an der die Entscheidung getroffen wurde.

Der Rückweg ist bewusst schmal: nur die betroffenen ZEITABSCHNITTE, gelesen
allein aus dem Index. Die Werte selbst bräuchte ein Band nicht — und genau
das macht die Auskunft billig genug, um sie bei jeder Abfrage mitzuliefern.
"""

from __future__ import annotations

import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from _paths import APP, page_text


try:
    from app.storage import cleanup, hotbuffer, query
    from app.storage.index import Index

    _PYARROW_AVAILABLE = True
except ImportError:
    _PYARROW_AVAILABLE = False

TZ = ZoneInfo("Europe/Berlin")
ENTITY = page_text("entity_detail.html")
PURGE_FORM = (APP / "templates/_settings_purge_form.html").read_text(encoding="utf-8")

pytestmark = pytest.mark.skipif(not _PYARROW_AVAILABLE, reason="pyarrow fehlt")


def _ts(hour: int, minute: int = 0) -> float:
    return datetime(2024, 7, 1, hour, minute, tzinfo=TZ).timestamp()


FENSTER = (datetime(2024, 7, 1, tzinfo=TZ).timestamp(), datetime(2024, 7, 2, tzinfo=TZ).timestamp())
NOW = datetime(2024, 7, 1, 23, tzinfo=TZ)
# 24 h / 100 = 864 s (14,4 min): enger beieinander liegende Markierungen
# bilden EIN Band. Der Wert ist an der Anzeige hergeleitet (rund neun Pixel
# auf einem 900 px breiten Chart), siehe marked_ranges_in_window().
LUECKE = (FENSTER[1] - FENSTER[0]) / 100


def _aufbau(tmp: Path, rows: list[tuple[float, float]]) -> tuple[Index, str]:
    index = Index(tmp / "index.sqlite")
    entity_id = "sensor.temp"
    index.get_or_create_entity(entity_id, "sensor", "measurement", "°C")
    for ts, value in rows:
        hotbuffer.append(tmp, entity_id, ts, value, TZ)
        index.record_write(entity_id, ts)
    return index, entity_id


def test_adjacent_markings_become_one_band() -> None:
    """Vier aufeinanderfolgende Messungen sind EIN Vorgang, kein Vierfaches.

    Als vier getrennte Bänder gezeichnet lägen sie ohnehin übereinander — die
    Zusammenfassung ist deshalb keine Vereinfachung, sondern die ehrlichere
    Darstellung.
    """
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-marked-"))
    try:
        rows = [(_ts(9, m), 90.0) for m in (0, 1, 2, 3)]
        index, entity_id = _aufbau(tmp, [(_ts(8), 21.0), *rows, (_ts(10), 21.2)])
        cleanup.soft_delete(index, entity_id, [ts for ts, _ in rows])

        bloecke, gesamt = query.marked_ranges_in_window(index, entity_id, *FENSTER, limit=200)
        assert gesamt == 4
        assert bloecke == [{"start": _ts(9, 0), "end": _ts(9, 3), "count": 4}]
        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_markings_far_apart_stay_separate_bands() -> None:
    """Zwei Vorgänge an verschiedenen Tageszeiten sind zwei Aussagen."""
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-marked-"))
    try:
        index, entity_id = _aufbau(tmp, [(_ts(8), 21.0), (_ts(9), 90.0), (_ts(17), 91.0)])
        cleanup.soft_delete(index, entity_id, [_ts(9), _ts(17)])

        bloecke, gesamt = query.marked_ranges_in_window(index, entity_id, *FENSTER, limit=200)
        assert gesamt == 2
        assert [b["start"] for b in bloecke] == [_ts(9), _ts(17)]
        assert all(b["count"] == 1 for b in bloecke)
        assert _ts(17) - _ts(9) > LUECKE
        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_a_five_minute_sensor_marked_in_one_go_stays_one_band() -> None:
    """Der Fall, an dem die erste Fassung gemessen gescheitert ist.

    Mit einer Schwelle von einem Dreihundertstel des Fensters (4,8 Minuten bei
    einer Tagesansicht) lag ein 5-Minuten-Takt um zwölf Sekunden darüber — aus
    einem zusammenhängenden Vorgang wurden 147 getrennte Bänder. Deshalb steht
    die Schwelle bei einem Hundertstel.
    """
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-marked-"))
    try:
        rows = [(_ts(3) + i * 300, 200.0) for i in range(40)]
        index, entity_id = _aufbau(tmp, rows)
        cleanup.soft_delete(index, entity_id, [ts for ts, _ in rows])

        bloecke, gesamt = query.marked_ranges_in_window(index, entity_id, *FENSTER, limit=200)
        assert gesamt == 40
        assert len(bloecke) == 1, [len(bloecke), "5-Minuten-Takt darf nicht zerfallen"]
        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_two_lone_markings_never_merge_just_because_they_are_the_only_two() -> None:
    """Gegenprobe zur verworfenen Median-Schwelle: bei genau zwei Markierungen
    ist der Median ihr eigener Abstand — sie wären dann immer verschmolzen,
    egal wie weit sie auseinanderliegen."""
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-marked-"))
    try:
        index, entity_id = _aufbau(tmp, [(_ts(2), 1.0), (_ts(20), 2.0)])
        cleanup.soft_delete(index, entity_id, [_ts(2), _ts(20)])
        bloecke, _ = query.marked_ranges_in_window(index, entity_id, *FENSTER, limit=200)
        assert len(bloecke) == 2
        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_the_reported_number_counts_values_not_bands() -> None:
    """Der Chip sagt „163 markiert", nicht „2 Bereiche". Wer aufräumen will,
    denkt in Werten; die Bänder sind nur ihre Darstellung."""
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-marked-"))
    try:
        rows = [(_ts(9, m), 90.0) for m in range(10)]
        index, entity_id = _aufbau(tmp, rows)
        cleanup.soft_delete(index, entity_id, [ts for ts, _ in rows])

        bloecke, gesamt = query.marked_ranges_in_window(index, entity_id, *FENSTER, limit=200)
        assert gesamt == 10
        assert len(bloecke) == 1 and bloecke[0]["count"] == 10
        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_duplicates_are_counted_per_occurrence_not_per_timestamp() -> None:
    """Zwei Rohwerte können exakt denselben Zeitstempel tragen, und beide
    lassen sich einzeln markieren. Die Zahl im Chip zählt WERTE — wer statt
    dessen die Zeitstempel zählt (``len(counts)`` statt ``sum(...)``), meldet
    für zwei markierte Duplikate nur eines.
    """
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-marked-"))
    try:
        dup = _ts(8)
        index, entity_id = _aufbau(tmp, [(dup, 151.0), (dup, 151.0), (_ts(9), 20.0)])
        cleanup.soft_delete(index, entity_id, [dup, dup])   # BEIDE Vorkommen

        bloecke, gesamt = query.marked_ranges_in_window(index, entity_id, *FENSTER, limit=200)
        assert gesamt == 2, "zwei markierte Werte auf einem Zeitstempel sind zwei"
        assert len(bloecke) == 1
        assert bloecke[0]["count"] == 2
        assert cleanup.list_raw_rows(tmp, index, entity_id, *FENSTER, TZ, now=NOW) == [(_ts(9), 20.0)]
        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_a_partially_marked_duplicate_leaves_the_other_occurrence_alone() -> None:
    """Gegenstück: nur eines der beiden Vorkommen markiert."""
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-marked-"))
    try:
        dup = _ts(8)
        index, entity_id = _aufbau(tmp, [(dup, 151.0), (dup, 151.0), (_ts(9), 20.0)])
        cleanup.soft_delete(index, entity_id, [dup])

        bloecke, gesamt = query.marked_ranges_in_window(index, entity_id, *FENSTER, limit=200)
        assert gesamt == 1
        assert bloecke[0]["count"] == 1
        assert cleanup.list_raw_rows(tmp, index, entity_id, *FENSTER, TZ, now=NOW) == [
            (dup, 151.0), (_ts(9), 20.0),
        ]
        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_an_entity_without_markings_answers_from_the_index_alone() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-marked-"))
    try:
        index, entity_id = _aufbau(tmp, [(_ts(8), 21.0), (_ts(9), 21.1)])
        assert query.marked_ranges_in_window(index, entity_id, *FENSTER, limit=200) == ([], 0)
        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_the_band_list_is_capped_but_the_reported_number_is_not() -> None:
    """Eine Anzeige „3 markiert", die in Wahrheit 40 meint, wäre schlimmer als
    gar keine Anzeige. Gekappt werden nur die gezeichneten Bänder."""
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-marked-"))
    try:
        # Stündlich verteilt — jede Markierung ein eigener Block.
        rows = [(_ts(h), 90.0) for h in range(20)]
        index, entity_id = _aufbau(tmp, rows)
        cleanup.soft_delete(index, entity_id, [ts for ts, _ in rows])

        bloecke, gesamt = query.marked_ranges_in_window(index, entity_id, *FENSTER, limit=5)
        assert gesamt == 20
        assert len(bloecke) == 5
        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_undone_markings_disappear_from_the_chart_again() -> None:
    """Rückgängig ist der Weg zurück — danach darf kein Band übrigbleiben,
    sonst zeigte der Chart eine Löschung an, die es nicht mehr gibt."""
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-marked-"))
    try:
        index, entity_id = _aufbau(tmp, [(_ts(8), 21.0), (_ts(9), 184.7)])
        cleanup.soft_delete(index, entity_id, [_ts(9)])
        assert query.marked_ranges_in_window(index, entity_id, *FENSTER, limit=200)[1] == 1

        assert cleanup.undo_last_delete(index, entity_id) == 1
        assert query.marked_ranges_in_window(index, entity_id, *FENSTER, limit=200) == ([], 0)
        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --- Oberfläche -----------------------------------------------------------


def test_bands_work_for_every_entity_type() -> None:
    """Ein senkrechtes Band braucht nur die Zeitachse und trifft keine Aussage
    über den Wert. Genau deshalb gibt es hier — anders als bei einem Marker auf
    dem entfernten Wert — keine Einschränkung auf „standard": bei einem Zähler
    sind Bucket-Werte Zuwächse und Rohwerte absolute Stände, ein markierter
    Stand von 45.213 kWh in einem Chart mit Tageszuwächsen um 12 kWh zerrisse
    die Achse."""
    assert "MARKED_SUPPORTED" not in ENTITY
    assert "markedAvailable" not in ENTITY
    schalter = ENTITY.split('<span class="menu-row-label">Markierte Werte</span>')[1][:400]
    assert ":disabled" not in schalter


def test_the_markings_are_only_fetched_when_they_are_shown() -> None:
    assert "if (this.showMarked) params.set('marked', 'true');" in ENTITY


def test_they_are_drawn_as_markarea_not_as_a_second_series() -> None:
    """Eine zweite Serie bestimmte die Achsenskalierung mit, verfälschte die
    Punkt-Schwelle des Zooms und stünde in der Legende."""
    assert "series[0].markArea = {" in ENTITY
    assert "if (this.showMarked && this.markedRanges.length) {" in ENTITY
    # silent: sonst fängt das Band die Mauszeiger-Ereignisse ab und das
    # Achsen-Tooltip der Kurve bleibt an den interessanten Stellen aus.
    assert "silent: true," in ENTITY.split("series[0].markArea = {")[1][:400]


def test_a_single_marking_still_gets_a_visible_band() -> None:
    """start == end wäre ein Band von null Pixeln."""
    bereich = ENTITY.split("series[0].markArea = {")[1][:1400]
    assert "Math.max(b.end - b.start, mindest)" in bereich


def test_the_chip_offers_both_ways_out_and_both_are_links() -> None:
    """Ein Zähler allein ließe den Nutzer stehen: die eine Hälfte will einzelne
    Markierungen zurücknehmen, die andere die ganze letzte Charge.

    Beides sind LINKS, und das ist der Kern. Die letzte Charge kann sechsstellig
    sein — in einer echten Installation gemessen 196.263 Werte —, während der
    Chip darüber nur die paar Markierungen des gezeigten Zeitraums nennt. Aus
    diesem Menü heraus eine Aktion dieser Größenordnung auszulösen, mit einer
    Rückfrage als einziger Zwischenstufe, wäre eine Falle: die Zahl im Knopf
    legt eine ganz andere Größenordnung nahe als die, die tatsächlich
    passiert.
    """
    menue = ENTITY.split('class="chip menu-btn chip-marked"')[1][:1200]
    assert menue.count("<a class=\"menu-row\"") == 2, "beide Einträge müssen Links sein"
    assert "/cleanup\"" in menue
    assert "/cleanup?undo=1" in menue
    assert "<button" not in menue.split("menu-popover")[1][:600]
    assert 'x-text="markedLabel"' in ENTITY


def test_the_undo_shows_the_rows_before_anything_happens() -> None:
    """Der Link landet nicht irgendwo auf der Bereinigungsseite, sondern klappt
    dort die Vorschau auf — sie zeigt die betroffenen Zeilen selbst, während
    eine Rückfrage nur eine Zahl nennen könnte.

    Der Warteschritt ist gemessen nötig, nicht vorsichtshalber da: die
    Zeilentabelle wird beim Seitenaufbau ZWEIMAL geholt (hx-trigger="load" auf
    #controls, und gleich darauf ein "change", weil das eingesetzte Fragment
    das Seitengrößen-Feld schreibt — nur die zweite Anfrage trägt page_size).
    Wer nach dem ersten Austausch öffnet, sieht die Vorschau vom zweiten sofort
    wieder überschrieben, und es sieht aus, als hätte der Klick nie
    stattgefunden.
    """
    cleanup_seite = page_text("cleanup.html")
    assert "new URLSearchParams(location.search).get('undo') === '1'" in cleanup_seite
    assert "clearTimeout(warte);" in cleanup_seite
    assert "warte = setTimeout(versuche, 300);" in cleanup_seite
    # Der Kern: versuche() wird IMMER nur eingeplant, nie direkt gerufen.
    # Genau dieser Unterschied war der Fehler — ein direkter Aufruf im
    # Swap-Handler öffnet die Vorschau nach dem ersten Austausch, und der
    # zweite überschreibt sie sofort wieder.
    assert "versuche();" not in cleanup_seite
    # Auch der Fall, dass die Tabelle schon steht, bevor die Datei läuft.
    assert "if (document.getElementById('undo-preview-btn')) warte = setTimeout" in cleanup_seite
    assert 'id="undo-preview-btn"' in (APP / "templates/_rows_table.html").read_text(encoding="utf-8")


def test_the_chart_no_longer_carries_its_own_undo() -> None:
    """Ein zweiter Weg, dieselbe große Aktion auszulösen, wäre genau der, den
    der Link ersetzen soll."""
    assert "undoLastBatch" not in ENTITY
    assert "undo-batch" not in ENTITY
    assert "undo-batch" not in (APP / "main.py").read_text(encoding="utf-8")


def test_show_marked_is_off_by_default_and_survives_as_an_entity_option() -> None:
    main_py = (APP / "main.py").read_text(encoding="utf-8")
    assert '"show_marked": False,' in main_py
    assert "show_marked: this.showMarked," in ENTITY
    assert "this.showMarked !== CHART_DEFAULTS.show_marked" in ENTITY


def test_the_deep_link_switches_the_display_on_without_saving_it() -> None:
    """Eine Seite über einen Link zu öffnen ist keine Einstellung: ?marked=1
    gilt für den Besuch, der nächste Aufruf zeigt wieder, was im Optionen-Menü
    steht."""
    assert 'href="{{ app_root }}/entities/{{ row.entity_id }}?marked=1"' in PURGE_FORM
    assert "const INITIAL_MARKED = {{ initial_marked | tojson }};" in ENTITY
    assert "showMarked: INITIAL_MARKED || CHART_OPTIONS.show_marked," in ENTITY
    # Der Link darf NICHT auf die Bereinigungsseite zeigen: die Tabelle
    # beantwortet „was würde der Purge wegräumen", und ansehen statt zählen
    # lässt sich das nur im Chart.
    assert "/cleanup" not in PURGE_FORM
