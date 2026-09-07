"""Architekturverträge der aus main.py ausgelagerten Routenmodule."""

from __future__ import annotations

import ast

from _paths import APP





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
    # Schwellenhistorie: 4.800, dann 5.700 (Housekeeping-Bereich, 0.75.0),
    # dann 5.800 (CoordinatorBusy-Handler + Backup-Worker-Heartbeat), dann
    # 5.850. Seit dem 7. September 2026 wieder 5.700 — die Zahl wurde erstmals
    # GESENKT, und das ist der Punkt (ZG-27).
    #
    # Was schiefgelaufen war: Bei 5.850 stand hier der Satz, der nächste
    # Schritt sei eine eigene housekeeping_routes.py und NICHT ein weiteres
    # Anheben. Genau das ist dann passiert — c279ae2 hat das Modul angelegt
    # (632 Zeilen), main.py fiel von 5.848 auf 5.578. Nur wusste dieser
    # Kommentar es nicht: Er nannte den Ausweg weiter als verfügbar, obwohl er
    # genommen war. Wer die Grenze als Nächstes gerissen hätte, hätte eine
    # bereits ausgeführte Anweisung gelesen und mangels Alternative doch die
    # Zahl erhöht — also genau das, wovor der Satz schützen sollte.
    #
    # Zweiter Schaden derselben Sache: Die Schwelle war als "Ist-Stand plus
    # kleiner Puffer" (~40 Zeilen) gedacht. Nach dem Schnitt wurde daraus
    # stillschweigend "Ist-Stand plus 272". Eine Grenze mit so viel Luft
    # stellt ihre Frage nicht mehr im richtigen Moment.
    #
    # Deshalb 5.700 gegen die heutigen 5.635: 65 Zeilen. Bei gemessenen rund
    # fünf Zeilen Zuwachs je Commit sind das ein gutes Dutzend Commits — kurz
    # genug, dass die Frage wieder gestellt wird, lang genug, dass nicht jede
    # Kleinigkeit sie auslöst.
    #
    # Der nächste Schnitt ist KEIN weiteres Routenmodul mehr. Am 7. September
    # gezählt: von 5.635 Zeilen sind nur 1.697 (30 %) Routenfunktionen, verteilt
    # auf 111 Routen; die größte verbliebene Gruppe ist /entities mit 476 Zeilen,
    # also 8 % der Datei. Die Masse sind 2.185 Zeilen Hilfsfunktionen.
    #
    # Der nächste Schnitt ist deshalb die HINTERGRUNDARBEIT: Sie ist klar
    # abgegrenzt, hängt nicht am Request und ist heute nur deshalb hier, weil
    # sie beim Start eingehängt wird — _run_backup_background (123),
    # _finish_retention_job (58), _background_storage_reconciliation (43) und
    # die zugehörigen Heartbeat-/Tick-Zustände. Erst danach käme die zweite,
    # größere Gruppe: die Template-Kontexte (_rows_fragment 176,
    # _dashboard_tiles_context 157, _entities_table_response 123 …), die enger
    # mit den Routen verzahnt sind.
    assert len(main.splitlines()) < 5_700


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
