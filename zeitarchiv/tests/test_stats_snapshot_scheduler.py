"""Regressionstests für die automatische Wachstumsaufzeichnung."""

from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAIN_SOURCE = (ROOT  / "app" / "main.py").read_text(encoding="utf-8")


def _function(name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    tree = ast.parse(MAIN_SOURCE)
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"Funktion {name} fehlt")


def _calls_snapshot(function: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    return any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "record_stats_snapshot_if_stale"
        for node in ast.walk(function)
    )


def test_maintenance_scheduler_records_growth_snapshots() -> None:
    assert _calls_snapshot(_function("_maintenance_scheduler_loop"))


def test_maintenance_scheduler_refreshes_retention_overview() -> None:
    function = _function("_maintenance_scheduler_loop")
    assert any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_refresh_retention_overview_if_stale"
        for node in ast.walk(function)
    )


def test_home_page_no_longer_controls_growth_snapshot_timing() -> None:
    assert not _calls_snapshot(_function("entities_view"))


def test_scheduler_records_before_its_first_wait() -> None:
    function = _function("_maintenance_scheduler_loop")
    snapshot_line = min(
        node.lineno
        for node in ast.walk(function)
        if isinstance(node, ast.Attribute) and node.attr == "record_stats_snapshot_if_stale"
    )
    wait_line = min(
        node.lineno
        for node in ast.walk(function)
        if isinstance(node, ast.Attribute) and node.attr == "wait"
    )
    assert snapshot_line < wait_line


def test_maintenance_scheduler_refreshes_duplicate_snapshot() -> None:
    """ZP-002 (PERFORMANCE.md): die Duplikat-Zählung für /statistik läuft im
    Wartungsplaner statt bei jedem Seitenaufruf synchron neu zu rechnen."""
    function = _function("_maintenance_scheduler_loop")
    assert any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_refresh_duplicate_snapshot_if_stale"
        for node in ast.walk(function)
    )


def test_statistik_view_reads_duplicate_snapshot_not_live_scan() -> None:
    """Die Duplikate-Anzeige (seit 0.75.0 in Housekeeping statt Statistik,
    siehe _duplicate_rows_for_display) darf die teure Rohdaten-Prüfung
    (cleanup.count_duplicate_rows_by_entity) nicht selbst aufrufen, sondern
    nur noch den vom Wartungsplaner zwischengespeicherten Stand lesen
    (index.get_duplicate_snapshot)."""
    helper = _function("_duplicate_rows_for_display")
    calls = {
        node.func.attr
        for node in ast.walk(helper)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert "count_duplicate_rows_by_entity" not in calls
    assert "get_duplicate_snapshot" in calls

    housekeeping = _function("housekeeping_view")
    housekeeping_calls = {
        node.func.id
        for node in ast.walk(housekeeping)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "_duplicate_rows_for_display" in housekeeping_calls



def _name_calls(function: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    return {
        node.func.id
        for node in ast.walk(function)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }


def test_request_path_reads_cached_stale_count_instead_of_locking_all_entities() -> None:
    """_count_stale_entities() nimmt über storage_coordinator.entities() die
    Sperren ALLER Entitäten (ohne Timeout, blockiert von/blockierend für
    Ingestion und Exklusiv-Wartung). Das gehört in den 30s-Wartungsplaner,
    nicht in einen context_processor, der bei jeder Template-Antwort und
    damit bei jedem htmx-Such-Fragment läuft."""
    for name in ("_notices_context", "mute_notice_route"):
        assert "_count_stale_entities" not in _name_calls(_function(name)), name
    assert "_refresh_stale_entity_count" in _name_calls(_function("_maintenance_scheduler_loop"))
    assert "_refresh_stale_entity_count" in _name_calls(_function("_start_maintenance_scheduler"))


def _run_all() -> None:
    tests = [obj for name, obj in globals().items() if name.startswith("test_")]
    for test in tests:
        test()
        print(f"ok  {test.__name__}")
    print(f"\n{len(tests)} Tests bestanden.")


if __name__ == "__main__":
    _run_all()
