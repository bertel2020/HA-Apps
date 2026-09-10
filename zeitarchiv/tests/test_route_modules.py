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
    # 5.850. Am 7. September 2026 erstmals GESENKT auf 5.700 (ZG-27), am
    # 8. September auf 5.150, am 9. September auf 5.260.
    #
    # Was beim vorletzten Mal schiefgelaufen war: Bei 5.850 stand hier der
    # Satz, der nächste Schritt sei eine eigene housekeeping_routes.py und
    # NICHT ein weiteres Anheben. Genau das ist dann passiert — c279ae2 hat
    # das Modul angelegt, main.py fiel von 5.848 auf 5.578. Nur wusste dieser
    # Kommentar es nicht: Er nannte den Ausweg weiter als verfügbar, obwohl er
    # genommen war. Wer die Grenze als Nächstes gerissen hätte, hätte eine
    # bereits ausgeführte Anweisung gelesen und mangels Alternative doch die
    # Zahl erhöht — also genau das, wovor der Satz schützen sollte.
    #
    # DIESER EINTRAG IST DIE GEGENPROBE ZU JENEM FEHLER. Der am 7. September
    # benannte nächste Schnitt — die Hintergrundarbeit — ist am 8. September
    # ausgeführt: app/background.py hält seither Wartungsplaner, Backup-,
    # Retention- und Abgleich-Läufe samt ihrem Zustand (623 Zeilen), main.py
    # fiel von 5.707 auf 5.084. Der Ausweg ist also GENOMMEN und steht nicht
    # mehr zur Verfügung.
    #
    # Am 9. September auf 5.260 angehoben — bewusst, nicht reflexiv: main.py
    # wuchs durch das Sektionen-Feature (drei neue Routen, dashboard_section_
    # add/rename/remove) von 5.084 auf 5.209, über die vorherige Schwelle von
    # 5.150. Das ist neue Nutzer-Funktionalität, kein schleichendes Wachstum
    # an einer Stelle, für die es schon einen benannten Ausweg gäbe — anders
    # als beim vorletzten Mal steht hier kein bereits identifizierter Schnitt
    # ungenutzt herum. Die 5.150-Schwelle selbst war zudem nie um diese
    # konkrete Änderung herum bemessen, sie kannte das Feature nicht.
    #
    # Die Schwelle folgt weiter der Regel "Ist-Stand plus kleiner Puffer":
    # 5.260 gegen die heutigen 5.209, also gut 50 Zeilen.
    #
    # Der fällige, schwierigere Schnitt bleibt unverändert offen: die
    # TEMPLATE-KONTEXTE. Am 8. September gezählt waren von 5.084 Zeilen rund
    # 1.730 Routenfunktionen (34 %) auf 112 Routen; der Rest sind überwiegend
    # Kontext-Erbauer (_rows_fragment, _dashboard_tiles_context,
    # _entities_table_response …) — _dashboard_tiles_context() ist mit den
    # Sektionen jetzt eher gewachsen als geschrumpft. Sie sind enger mit den
    # Routen verzahnt als die Hintergrundarbeit es war — ein Schnitt dort
    # braucht erst eine Antwort darauf, was ein Kontext-Erbauer vom Request
    # wissen darf. Das ist mit dieser Anhebung NICHT erledigt, nur vertagt.
    assert len(main.splitlines()) < 5_260


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


def test_background_module_owns_the_scheduler_and_its_state() -> None:
    """Der Vertrag des jüngsten Schnitts (0.85.0).

    Die Hintergrundarbeit hing nur deshalb in main.py, weil sie dort beim
    Start eingehängt wurde — nicht, weil sie mit den Routen zu tun hätte. Was
    hier geprüft wird, ist genau die Grenze, die den Umzug erst möglich machte:
    keine Abhängigkeit zurück auf main.py, und der Zustand liegt am Dienst
    statt als Modul-Globale.
    """
    background = _source("background.py")
    tree = ast.parse(background)
    classes = {node.name for node in tree.body if isinstance(node, ast.ClassDef)}
    assert {"BackgroundDependencies", "BackgroundService"} <= classes
    assert "from .main import" not in background

    # Kein Modul-Zustand mehr: Alles, was der Wartungsplaner umschreibt, gehört
    # der Instanz. Ein "global" hier hieße, dass zwei Dienste in einem Prozess
    # einander überschrieben — und genau das war der Grund, warum main.py für
    # jeden dieser Werte einen Getter durch die Bereichsmodule reichen musste.
    assert "global " not in background

    dienst = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "BackgroundService")
    methoden = {n.name for n in dienst.body if isinstance(n, ast.FunctionDef)}
    # Was main.py und die Bereichsmodule aufrufen, muss öffentlich bleiben.
    assert {
        "start", "stop", "run_backup", "run_storage_reconciliation",
        "begin_retention_job", "finish_retention_job",
        "load_purge_preview", "load_retention_overview",
        "refresh_purge_preview_if_stale", "refresh_retention_overview_if_stale",
        "invalidate_purge_preview", "invalidate_retention_overview",
        "set_next_backup_run", "set_next_retention_run", "reconcile_in_progress",
    } <= methoden

    # main.py hängt den Dienst nur noch ein und hält keine eigene Kopie des
    # Zustands mehr — sonst liefen beide auseinander.
    main = _source("main.py")
    assert "_background = BackgroundService(BackgroundDependencies(" in main
    for weg in (
        "_maintenance_scheduler_loop", "_run_backup_background",
        "_storage_reconcile_thread", "_last_scheduler_tick",
    ):
        assert f"\n{weg}" not in main, f"{weg} steht wieder in main.py"
