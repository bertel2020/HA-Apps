"""Regressionstests für den gebündelten Datenpfad großer Vergleichstabellen."""

from __future__ import annotations

import dataclasses
from collections import Counter
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo



import pytest
import pyarrow as pa
import pyarrow.parquet as pq

from app.storage import hotbuffer, query, rollup
from app.api_routes import (
    ApiDependencies,
    ApiState,
    TableQueryRequest,
    _table_comparison_aggregates,
    create_api_router,
)
from app.storage.coordinator import StorageCoordinator
from app.storage.index import Index

from _paths import APP, TEMPLATES, page_text


TZ = ZoneInfo("Europe/Berlin")
COMPUTE = (APP / "static/js/table-compute.js").read_text(encoding="utf-8")
DASHBOARD = (APP / "static/js/dashboard-tiles.js").read_text(encoding="utf-8")
FIXED_TOOLTIP = (APP / "static/js/fixed-tooltip.js").read_text(encoding="utf-8")
TABLE_EDITOR = page_text("table_editor.html")


def test_query_read_cache_parses_current_hot_file_once(monkeypatch, tmp_path: Path) -> None:
    entity_id = "sensor.temp"
    index = Index(tmp_path / "index.sqlite")
    index.get_or_create_entity(entity_id, "sensor", "measurement", "°C")
    now = datetime(2026, 8, 20, 12, tzinfo=TZ)
    ts = datetime(2026, 8, 20, 10, tzinfo=TZ).timestamp()
    hotbuffer.append(tmp_path, entity_id, ts, 21.5, TZ)
    index.record_write(entity_id, ts)

    paths: list[Path] = []
    original = query.read_rows

    def recording_read_rows(path: Path):
        paths.append(path)
        return original(path)

    monkeypatch.setattr(query, "read_rows", recording_read_rows)
    cache = query.QueryReadCache()
    query.query_series(tmp_path, index, entity_id, "day", TZ, now, read_cache=cache)
    query.query_series(tmp_path, index, entity_id, "month", TZ, now, read_cache=cache)
    query.query_series(tmp_path, index, entity_id, "year", TZ, now, read_cache=cache)

    # Ein Jahresfenster kann zusätzlich einen (nicht existierenden) Hot-Pfad
    # am Jahresanfang als Randwert prüfen. Entscheidend ist: kein Pfad wird
    # innerhalb desselben Tabellen-Requests ein zweites Mal geparst.
    assert max(Counter(paths).values()) == 1
    assert hotbuffer.hot_path(tmp_path, entity_id, ts, TZ) in paths
    index.close()


def test_table_frontend_uses_one_batch_request() -> None:
    assert "fetch(`${base}/api/query-table`" in COMPUTE
    assert "Promise.all(columns.map" not in COMPUTE


def test_table_batch_endpoint_returns_columns_in_request_order(tmp_path: Path) -> None:
    entity_id = "sensor.temp"
    index = Index(tmp_path / "index.sqlite")
    index.get_or_create_entity(entity_id, "sensor", "measurement", "°C")
    ts = datetime.now(TZ).replace(minute=0, second=0, microsecond=0).timestamp()
    hotbuffer.append(tmp_path, entity_id, ts, 21.5, TZ)
    index.record_write(entity_id, ts)

    router = create_api_router(ApiDependencies(
        data_dir=tmp_path,
        index=index,
        tz=TZ,
        coordinator=StorageCoordinator(),
        ingestion=None,  # Für diesen reinen Lese-Endpunkt nicht benötigt.
        api_token=lambda: "test",
        app_version="test",
        collect_notices=lambda: [],
    ), ApiState())
    endpoint = next(route.endpoint for route in router.routes if route.path == "/api/query-table")
    result = endpoint(TableQueryRequest(
        entity_ids=[entity_id],
        columns=[
            {"range_key": "day", "offset": 0, "year_over_year": False},
            {"range_key": "month", "offset": -1, "year_over_year": False},
        ],
    ))

    columns = result["columns"]
    assert len(columns) == 2
    assert columns[0]["series"][0]["entity_id"] == entity_id
    assert columns[0]["series"][0]["aggregates"]["avg"] == 21.5
    assert "points" not in columns[0]["series"][0]
    assert columns[0]["window_start"] > columns[1]["window_start"]
    index.close()


def test_table_comparison_aggregates_uses_elapsed_cutoff_not_full_period() -> None:
    """_table_comparison_aggregates() liefert den fairen, auf elapsed_seconds
    gekappten Vergleichswert — unabhängig davon, dass "aggregates"
    (_table_aggregates()) für eine same_elapsed-Spalte jetzt immer den
    vollen Zeitraum zeigt (Regressionsschutz für den "Vortag zeigt nur
    einen Teiltag"-Bug)."""
    result = {
        "aggregation_type": "standard",
        "window_start": 1000.0,
        "elapsed_seconds": 500.0,  # Cutoff bei ts < 1500
        "points": [
            {"ts": 1100.0, "value": 4.0, "min": None, "max": None},
            {"ts": 1800.0, "value": 6.0, "min": None, "max": None},
        ],
    }
    comparison = _table_comparison_aggregates(result)
    assert comparison["avg"] == 4.0  # nur der Punkt vor dem Cutoff zählt

    assert _table_comparison_aggregates({**result, "elapsed_seconds": None}) is None


def test_frontend_deviation_uses_fair_comparison_value_not_full_period() -> None:
    """Regressionsschutz fürs Frontend-Gegenstück zum "Vortag zeigt nur einen
    Teiltag"-Bugfix: die Prozentzahl muss comparisonValue (elapsed-gekappt)
    nutzen, nicht den jetzt immer vollen Zellwert — sonst wäre die
    Backend-Korrektur (comparison_aggregates) client-seitig wirkungslos."""
    assert "comparison_aggregates" in COMPUTE
    assert "comparisonCell.comparisonValue" in COMPUTE
    assert "comparisonValue" in DASHBOARD


def test_deviation_tooltip_explains_partial_comparison_window() -> None:
    """Ohne diesen Hinweis wirkt es wie ein Widerspruch, dass die Zelle den
    vollen Zeitraum zeigt, die daneben stehende Prozentzahl aber auf einem
    kürzeren, bisher vergangenen Fenster beruht."""
    assert "vollständigen Zeitraum" in DASHBOARD
    assert "deviationTitle(row, col)" in TABLE_EDITOR
    assert "vollständigen Zeitraum" in TABLE_EDITOR


def test_deviation_tooltip_shows_actual_reference_time_not_vague_phrase() -> None:
    """Der Tooltip muss die tatsächliche Uhrzeit des Vergleichs-Cutoffs zeigen
    (z. B. "Vortag bis 12:16 Uhr"), nicht die vage Phrase "bis zur aktuellen
    Uhrzeit" — Klarstellung nach Nutzer-Feedback: gemeint ist der konkrete
    Referenzzeitpunkt, nicht der komplette Zeitraum."""
    assert "comparisonElapsedTimeText" in COMPUTE
    assert "bis zur aktuellen Uhrzeit" not in DASHBOARD
    assert "bis zur aktuellen Uhrzeit" not in TABLE_EDITOR
    assert "comparisonElapsedTimeText" in DASHBOARD
    assert "comparisonElapsedTimeText" in TABLE_EDITOR


def test_dashboard_tile_deviation_tooltip_escapes_tile_clipping() -> None:
    """Kachel- und Editor-Vorschau clippen überlaufenden Inhalt
    (.dtile-table-preview/.dtile-body: overflow, .tbl-preview: overflow-x:auto)
    — ein normaler CSS-::after-Tooltip (data-tooltip) würde dort abgeschnitten
    (siehe Bugreport: Tooltip lief in den überlaufenen Bereich). data-tooltip-fixed
    plus ein JS-Popup mit position:fixed (fixed-tooltip.js) hängt stattdessen
    an document.body, außerhalb jeder Beschneidung."""
    assert 'data-tooltip-fixed="${escapeHtml(deviationTitle)}"' in DASHBOARD
    assert "function wire" in FIXED_TOOLTIP
    assert ":data-tooltip-fixed=\"deviationTitle(row, col)\"" in TABLE_EDITOR


def test_fixed_tooltip_script_loaded_wherever_data_tooltip_fixed_is_used() -> None:
    """table_editor.html lädt dashboard-tiles.js NICHT (eigenständige Seite
    ohne Kacheln) — der Abweichungs-Tooltip braucht deshalb sein eigenes,
    von beiden Seiten geladenes Skript (fixed-tooltip.js), sonst bleibt
    data-tooltip-fixed dort ohne jede Wirkung (Regressionsschutz für genau
    diesen Bug: Tooltip verschwand komplett auf /tables/<id>)."""
    for name in ("entities.html", "dashboard_detail.html", "table_editor.html"):
        html = (TEMPLATES / name).read_text(encoding="utf-8")
        assert "js/fixed-tooltip.js" in html, name


def test_dashboard_only_computes_visible_table_slice() -> None:
    assert "computeValues(base, visibleCols, visibleRows)" in DASHBOARD


# --- Rollup-Cache für grobe Stufen (PERFORMANCE.md, ZP-008) -----------------

DEKADE_SPALTEN = (
    {"offset": 0},
    {"offset": -1},
    {"offset": 0, "year_over_year": True},
    # "Rollierend" ist hier keine Zugabe, sondern der einzige Fall, der die
    # Beschneidung angeschnittener Jahre auslöst (clip_start > year_start in
    # _query_year_level). Ohne ihn liefe der Test nur über volle Kalenderjahre
    # und übersähe genau die Zeilen, die der Cache spaltenübergreifend teilt.
    {"offset": 0, "continuous": True},
)


def _zaehler_mit_jahren(tmp_path: Path, entity_id: str, von: int, bis: int) -> Index:
    """Legt eine Zähler-Entität mit Monats- und Jahres-Rollups an, im
    Legacy-Format (je Stufe genau eine Datei) — so sieht eine Installation aus,
    die seit dem Update noch keinen Monat rotiert hat."""
    index = Index(tmp_path / "index.sqlite")
    index.get_or_create_entity(entity_id, "sensor", "total_increasing", "kWh")
    monate, jahre = [], []
    for jahr in range(von, bis + 1):
        for monat in range(1, 13):
            monate.append((datetime(jahr, monat, 1, tzinfo=TZ).timestamp(), float(jahr * 12 + monat)))
        jahre.append((datetime(jahr, 1, 1, tzinfo=TZ).timestamp(), float(jahr)))
    ziel = rollup.rollup_dir(tmp_path, entity_id)
    ziel.mkdir(parents=True, exist_ok=True)
    for stufe, zeilen in (("monat", monate), ("jahr", jahre)):
        pq.write_table(
            pa.table({"bucket_start": [t for t, _ in zeilen], "value": [v for _, v in zeilen]}),
            ziel / f"{stufe}.parquet",
        )
    return index


def _serien(tmp_path: Path, index: Index, entity_id: str, bereich: str, *, mit_cache: bool) -> list:
    cache = query.QueryReadCache() if mit_cache else None
    now = datetime(2026, 9, 8, 12, tzinfo=TZ)
    return [
        query.query_series(tmp_path, index, entity_id, bereich, TZ, now, read_cache=cache, **spalte)["points"]
        for spalte in DEKADE_SPALTEN
    ]


def test_the_decade_view_opens_each_coarse_rollup_level_only_once(monkeypatch, tmp_path: Path) -> None:
    """Vor ZP-008 las _query_year_level() jahr.parquet einmal je Kalenderjahr —
    bei drei Spalten also bis zu 33-mal dieselbe Datei. Der Request-Cache macht
    daraus einen Zugriff je (Entität, Stufe)."""
    entity_id = "sensor.zaehler"
    index = _zaehler_mit_jahren(tmp_path, entity_id, 2014, 2025)

    gelesen: list[str] = []
    original = pq.read_table

    def aufzeichnend(path, *args, **kwargs):
        gelesen.append(Path(path).name)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(query.pq, "read_table", aufzeichnend)
    _serien(tmp_path, index, entity_id, "decade", mit_cache=True)
    index.close()

    grob = Counter(name for name in gelesen if name in ("monat.parquet", "jahr.parquet"))
    assert grob["jahr.parquet"] == 1, grob
    assert grob["monat.parquet"] <= 1, grob


def test_the_cache_never_changes_what_a_decade_query_returns(tmp_path: Path) -> None:
    """Der Cache ist eine reine Beschleunigung: Er liest vollständig statt
    gefiltert und filtert danach in Python — heraus kommen dieselben Punkte."""
    entity_id = "sensor.zaehler"
    index = _zaehler_mit_jahren(tmp_path, entity_id, 2014, 2025)
    for bereich in ("year", "decade"):
        ohne = _serien(tmp_path, index, entity_id, bereich, mit_cache=False)
        mit = _serien(tmp_path, index, entity_id, bereich, mit_cache=True)
        assert mit == ohne, bereich
    index.close()


def test_the_cache_also_holds_for_segmented_rollups(tmp_path: Path) -> None:
    """Bestehende Installationen, die seit dem Update einen Monat rotiert
    haben, haben Dataset-VERZEICHNISSE statt Einzeldateien (rollup._segment_dir).
    Beide Formate müssen dasselbe liefern — sonst hinge das Ergebnis davon ab,
    wann jemand aktualisiert hat."""
    entity_id = "sensor.temp"
    index = Index(tmp_path / "index.sqlite")
    index.get_or_create_entity(entity_id, "sensor", "measurement", "°C")
    for jahr in (2024, 2025):
        for monat in (6, 7):
            start = datetime(jahr, monat, 1, tzinfo=TZ)
            ts = [(start.timestamp() + i * 3600) for i in range(24 * 20)]
            rollup.append_completed_month(
                tmp_path, entity_id, "standard",
                pa.table({"ts": ts, "value": [float(i % 17) for i in range(len(ts))]}),
                jahr, monat, TZ,
            )
    assert rollup.rollup_path(tmp_path, entity_id, "monat").is_dir()

    ohne = _serien(tmp_path, index, entity_id, "decade", mit_cache=False)
    mit = _serien(tmp_path, index, entity_id, "decade", mit_cache=True)
    assert mit == ohne
    index.close()


def test_the_fine_rollup_levels_stay_out_of_the_cache(monkeypatch, tmp_path: Path) -> None:
    """Die Begrenzung auf monat/jahr ist kein Detail, sondern der Grund, warum
    der Cache harmlos ist: monat+jahr wiegen über 86 Entitäten zusammen rund
    627 KiB, eine einzelne stunde.parquet dagegen 5,7 MiB. Eine spätere
    Erweiterung auf die feinen Stufen soll hier auflaufen."""
    assert query.CACHEABLE_ROLLUP_LEVELS == ("monat", "jahr")

    entity_id = "sensor.temp"
    index = Index(tmp_path / "index.sqlite")
    index.get_or_create_entity(entity_id, "sensor", "measurement", "°C")
    ziel = rollup.rollup_dir(tmp_path, entity_id)
    ziel.mkdir(parents=True, exist_ok=True)
    start = datetime(2026, 7, 1, tzinfo=TZ).timestamp()
    pq.write_table(
        pa.table({"bucket_start": [start + i * 3600 for i in range(200)],
                  "value": [float(i) for i in range(200)]}),
        ziel / "stunde.parquet",
    )

    cache = query.QueryReadCache()
    zuerst = query._read_rollup_rows(tmp_path, entity_id, "stunde", start, start + 200 * 3600, cache)
    assert zuerst

    gelesen: list[str] = []
    original = pq.read_table

    def aufzeichnend(path, *args, **kwargs):
        gelesen.append(Path(path).name)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(query.pq, "read_table", aufzeichnend)
    query._read_rollup_rows(tmp_path, entity_id, "stunde", start, start + 200 * 3600, cache)
    index.close()
    assert gelesen == ["stunde.parquet"], "feine Stufe darf nicht aus dem Cache kommen"


def test_rollup_rows_cannot_be_mutated_at_all(tmp_path: Path) -> None:
    """Der Cache reicht DIESELBEN Zeilen-Objekte an mehrere Spalten weiter.
    Würde eine Spalte eine Zeile verändern, sähe die nächste die Änderung —
    ein Fehler, der von der Spaltenreihenfolge abhinge und deshalb kaum
    reproduzierbar wäre. rollup.FineRow ist darum frozen: Der Versuch scheitert
    laut, statt still falsche Zahlen zu erzeugen. Ein Test, der stattdessen nur
    zwei Durchläufe vergleicht, fängt genau die kumulativen Fälle und lässt die
    übrigen durch — gemessen, nicht vermutet."""
    zeile = rollup.FineRow(bucket_start=1.0, value=2.0)
    with pytest.raises(dataclasses.FrozenInstanceError):
        zeile.value = 99.0


def test_cached_rollup_rows_survive_a_second_pass_unchanged(tmp_path: Path) -> None:
    """Zweite Sicherung neben der Unveränderlichkeit oben: Derselbe Cache,
    zweimal dieselben Spalten — das Ergebnis darf sich nicht verschieben."""
    entity_id = "sensor.zaehler"
    index = _zaehler_mit_jahren(tmp_path, entity_id, 2014, 2025)
    cache = query.QueryReadCache()
    now = datetime(2026, 9, 8, 12, tzinfo=TZ)

    def durchlauf():
        return [
            query.query_series(tmp_path, index, entity_id, "decade", TZ, now, read_cache=cache, **spalte)["points"]
            for spalte in DEKADE_SPALTEN
        ]

    assert durchlauf() == durchlauf()
    index.close()
