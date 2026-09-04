"""Regressionstests für den gebündelten Datenpfad großer Vergleichstabellen."""

from __future__ import annotations

import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.storage import hotbuffer, query
from app.api_routes import (
    ApiDependencies,
    ApiState,
    TableQueryRequest,
    _table_comparison_aggregates,
    create_api_router,
)
from app.storage.coordinator import StorageCoordinator
from app.storage.index import Index


TZ = ZoneInfo("Europe/Berlin")
COMPUTE = (ROOT / "app/static/js/table-compute.js").read_text(encoding="utf-8")
DASHBOARD = (ROOT / "app/static/js/dashboard-tiles.js").read_text(encoding="utf-8")
TABLE_EDITOR = (ROOT / "app/templates/table_editor.html").read_text(encoding="utf-8")


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
    assert "vollständige Zeitraum" in DASHBOARD
    assert "deviationTitle(row, col)" in TABLE_EDITOR
    assert "vollständige Zeitraum" in TABLE_EDITOR


def test_dashboard_only_computes_visible_table_slice() -> None:
    assert "computeValues(base, visibleCols, visibleRows)" in DASHBOARD
