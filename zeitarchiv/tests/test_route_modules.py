"""Architekturverträge der aus main.py ausgelagerten Routenmodule."""

from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"


def _source(name: str) -> str:
    return (APP / name).read_text(encoding="utf-8")


def test_main_keeps_external_api_and_report_routes_out_of_the_monolith() -> None:
    main = _source("main.py")
    assert '@app.get("/api/health")' not in main
    assert '@app.post("/api/write")' not in main
    assert '@app.get("/api/query")' not in main
    assert '@app.get("/reports")' not in main
    assert "create_api_router" in main
    assert "ReportService" in main
    # War 4.800, dann 5.700 (Housekeeping-Bereich, 0.75.0), jetzt 5.800 nach
    # CoordinatorBusy-Handler + Backup-Worker-Heartbeat (Roadmap "Neu seit
    # 0.76.1", Punkt 1: coordinator.entity()/entities() ohne Timeout) —
    # bewusst mit Puffer statt exakt auf den aktuellen Stand (~5.720), damit
    # nicht jede Kleinigkeit die Grenze reißt. Wächst main.py nochmal
    # spürbar, ist eine eigene housekeeping_routes.py (analog zu
    # api_routes.py/report_routes.py/import_routes.py) der nächste Schritt,
    # nicht ein weiteres Anheben dieser Zahl.
    assert len(main.splitlines()) < 5_800


def test_api_router_has_explicit_runtime_dependencies_and_all_api_routes() -> None:
    source = _source("api_routes.py")
    tree = ast.parse(source)
    classes = {node.name for node in tree.body if isinstance(node, ast.ClassDef)}
    assert {"ApiDependencies", "ApiState", "EventIn", "WriteRequest"} <= classes
    for path in ("/api/health", "/api/write", "/api/query", "/api/query-multi", "/api/query-table"):
        assert f'"{path}"' in source
    assert "from .main import" not in source


def test_report_router_is_independent_and_route_locking_is_shared() -> None:
    reports = _source("report_routes.py")
    support = _source("route_support.py")
    ast.parse(reports)
    ast.parse(support)
    assert "class ReportService" in reports
    assert "class ReportDependencies" in reports
    assert "from .main import" not in reports
    assert "def storage_locked" in support
    assert "with coordinator.entities(entity_ids, timeout=timeout):" in support
