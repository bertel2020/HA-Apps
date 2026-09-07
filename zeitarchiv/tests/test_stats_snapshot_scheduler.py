"""Regressionstests für die automatische Wachstumsaufzeichnung."""

from __future__ import annotations

import ast

from _paths import APP


MAIN_SOURCE = (APP / "main.py").read_text(encoding="utf-8")
HOUSEKEEPING_SOURCE = (APP / "housekeeping_routes.py").read_text(encoding="utf-8")
#: Der Wartungsplaner und die von ihm gepflegten Zwischenspeicher liegen seit
#: 0.85.0 in background.py — main.py hängt sie nur noch ein (Zeilenbudget,
#: siehe test_route_modules.py). Die Zusicherungen unten sind unverändert, nur
#: die Fundstelle ist eine andere; aus Funktionen wurden dabei Methoden, aus
#: `_refresh_x()` also `self.refresh_x()`.
BACKGROUND_SOURCE = (APP / "background.py").read_text(encoding="utf-8")


def _function(name: str, source: str = MAIN_SOURCE) -> ast.FunctionDef | ast.AsyncFunctionDef:
    """Sucht die Funktion im ganzen Baum, nicht nur auf Modulebene — die
    Housekeeping-Routen liegen als verschachtelte Definitionen in
    create_housekeeping_router() (Muster von create_api_router())."""
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"Funktion {name} fehlt")


def _self_calls(function: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    """Namen der über self.… aufgerufenen Methoden — das Gegenstück zu
    _name_calls() weiter unten, seit der Wartungsplaner eine Methode ist."""
    return {
        node.func.attr
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "self"
    }


def _calls_snapshot(function: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    return any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "record_stats_snapshot_if_stale"
        for node in ast.walk(function)
    )


def test_maintenance_scheduler_records_growth_snapshots() -> None:
    assert _calls_snapshot(_function("_maintenance_scheduler_loop", BACKGROUND_SOURCE))


def test_maintenance_scheduler_refreshes_retention_overview() -> None:
    function = _function("_maintenance_scheduler_loop", BACKGROUND_SOURCE)
    assert "refresh_retention_overview_if_stale" in _self_calls(function)


def test_home_page_no_longer_controls_growth_snapshot_timing() -> None:
    assert not _calls_snapshot(_function("entities_view"))


def test_scheduler_records_before_its_first_wait() -> None:
    function = _function("_maintenance_scheduler_loop", BACKGROUND_SOURCE)
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
    function = _function("_maintenance_scheduler_loop", BACKGROUND_SOURCE)
    assert "_refresh_duplicate_snapshot_if_stale" in _self_calls(function)


def test_statistik_view_reads_duplicate_snapshot_not_live_scan() -> None:
    """Die Duplikate-Anzeige (seit 0.75.0 in Housekeeping statt Statistik,
    siehe _duplicate_rows_for_display) darf die teure Rohdaten-Prüfung
    (cleanup.count_duplicate_rows_by_entity) nicht selbst aufrufen, sondern
    nur noch den vom Wartungsplaner zwischengespeicherten Stand lesen
    (index.get_duplicate_snapshot)."""
    helper = _function("_duplicate_rows_for_display", HOUSEKEEPING_SOURCE)
    calls = {
        node.func.attr
        for node in ast.walk(helper)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert "count_duplicate_rows_by_entity" not in calls
    assert "get_duplicate_snapshot" in calls

    housekeeping = _function("housekeeping_view", HOUSEKEEPING_SOURCE)
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
    # In background.py heißt der Wartungsplaner-Start schlicht start().
    assert "_refresh_stale_entity_count" in _self_calls(
        _function("_maintenance_scheduler_loop", BACKGROUND_SOURCE)
    )
    assert "_refresh_stale_entity_count" in _self_calls(
        _function("start", BACKGROUND_SOURCE)
    )


def _run_all() -> None:
    tests = [obj for name, obj in globals().items() if name.startswith("test_")]
    for test in tests:
        test()
        print(f"ok  {test.__name__}")
    print(f"\n{len(tests)} Tests bestanden.")


if __name__ == "__main__":
    _run_all()


# --------------------------------------------------------------------------
# Der Abgleich zwischen Wartungsplaner und Diagnose-Liste
#
# Einstellungen → Diagnose → Hintergrundprozesse ist von Hand gepflegt: sieben
# feste Zeilen plus zwei bedingte. Nichts hielt sie bisher gegen das, was
# _maintenance_scheduler_loop() tatsächlich aufruft — und genau deshalb fehlte
# der Ausreißer-Lauf dort monatelang, obwohl er in jedem Takt läuft. Ein
# fehlender Eintrag fällt niemandem auf: Die Liste sieht vollständig aus.
# --------------------------------------------------------------------------

#: Jeder Schritt des Wartungsplaners → die Zeile, die er in der Diagnose
#: bekommt. None heißt "bewusst keine Zeile", mit dem Grund daneben. Neue
#: Schritte müssen hier eingetragen werden; genau das erzwingt die Entscheidung,
#: die beim Ausreißer-Lauf nie jemand getroffen hat.
WARTUNGSPLANER_ZEILEN: dict[str, str | None] = {
    "record_stats_snapshot_if_stale": "Statistik-Snapshot",
    "maybe_record_memory_snapshot": "Arbeitsspeicher-Snapshot",
    "refresh_retention_overview_if_stale": "Aufbewahrung-Übersicht",
    "refresh_purge_preview_if_stale": "Löschvorschau",
    "_refresh_duplicate_snapshot_if_stale": "Duplikat-Erkennung",
    "_refresh_one_outlier_rate": "Ausreißer-Quoten",
    "refresh_if_stale": "Versionsprüfung",
    "process_pending_hourly_backfill": "Energiedashboard · Stunden-Rollup-Backfill",
    "refresh_heatmap_weekday_cache_if_stale": "Energiedashboard · Tageslastprofil-Cache",
    # Ohne eigene Zeile, jeweils weil ihr Ergebnis anderswo vollständiger steht:
    "_refresh_stale_entity_count": None,       # → Meldung housekeeping.rotation_pending
    "_refresh_host_disk_usage": None,          # → Speicherplatz-Balken auf /housekeeping
    "refresh_import_leftovers_if_stale": None,  # → Meldung über liegengebliebene Quelldaten
    "refresh_integration_version_check_if_stale": None,  # → Meldung "Integration veraltet"
    "_run_backup_schedule_if_due": None,       # → eigene Backup-Seite mit Verlauf
    "_run_retention_enforcement_if_due": None,  # → eigene Aufbewahrung-Seite mit Verlauf
}

#: Läuft NICHT im 30-Sekunden-Takt, sondern einmalig beim Start in einem
#: eigenen Thread (siehe BackgroundService.start) — hat aber eine Zeile, weil
#: sein Ergebnis genau so lange gilt wie der Prozess.
ZEILE_AUSSERHALB_DES_TAKTS = "Speicherindex-Abgleich"

#: Kein Arbeitsschritt, sondern Gerüst der Schleife selbst.
KEIN_SCHRITT = {"debug", "exception", "wait", "is_set", "now", "time"}


def _wartungsplaner_schritte() -> set[str]:
    schleife = _function("_maintenance_scheduler_loop", BACKGROUND_SOURCE)
    namen = set()
    for knoten in ast.walk(schleife):
        if not isinstance(knoten, ast.Call):
            continue
        if isinstance(knoten.func, ast.Attribute):
            namen.add(knoten.func.attr)
        elif isinstance(knoten.func, ast.Name):
            namen.add(knoten.func.id)
    return namen - KEIN_SCHRITT


def _diagnose_zeilennamen() -> set[str]:
    """Die Namen aus _settings_background_processes_context() — aus dem Baum
    gelesen statt aus dem gerenderten HTML: Die beiden
    Energiedashboard-Zeilen entstehen nur bei konfiguriertem Dashboard und
    fehlten in einer Testumgebung sonst immer."""
    funktion = _function("_settings_background_processes_context")
    namen = set()
    for knoten in ast.walk(funktion):
        # row("Name", …) — die festen Zeilen
        if (isinstance(knoten, ast.Call) and isinstance(knoten.func, ast.Name)
                and knoten.func.id == "row" and knoten.args
                and isinstance(knoten.args[0], ast.Constant)):
            namen.add(knoten.args[0].value)
        # {"name": "…", …} — die Zeilen mit eigener Pille
        if isinstance(knoten, ast.Dict):
            for schluessel, wert in zip(knoten.keys, knoten.values):
                if (isinstance(schluessel, ast.Constant) and schluessel.value == "name"
                        and isinstance(wert, ast.Constant)):
                    namen.add(wert.value)
    return namen


def test_every_scheduler_step_has_been_decided_about() -> None:
    """Die Richtung, die den Ausreißer-Lauf durchrutschen ließ: Ein neuer
    Schritt im Wartungsplaner bekommt entweder eine Zeile in der Diagnose oder
    einen ausdrücklichen Vermerk, warum nicht. Stillschweigend gar nichts geht
    nicht mehr."""
    schritte = _wartungsplaner_schritte()
    unbekannt = schritte - set(WARTUNGSPLANER_ZEILEN)
    assert not unbekannt, (
        f"Neue Wartungsplaner-Schritte ohne Entscheidung: {sorted(unbekannt)} — "
        "entweder eine Zeile in _settings_background_processes_context() ergänzen "
        "oder hier mit None und Begründung eintragen"
    )
    verschwunden = set(WARTUNGSPLANER_ZEILEN) - schritte
    assert not verschwunden, (
        f"Hier eingetragen, aber im Wartungsplaner nicht mehr aufgerufen: "
        f"{sorted(verschwunden)} — die zugehörige Diagnose-Zeile zeigt dann einen "
        "Prozess an, den es nicht mehr gibt"
    )


def test_the_diagnostics_list_matches_the_scheduler() -> None:
    """Und die Gegenrichtung: keine Zeile für einen Prozess, den niemand mehr
    aufruft. Eine Diagnose, die einen abgeschafften Lauf als "OK" meldet, wäre
    schlimmer als gar keine."""
    erwartet = {zeile for zeile in WARTUNGSPLANER_ZEILEN.values() if zeile}
    erwartet.add(ZEILE_AUSSERHALB_DES_TAKTS)
    vorhanden = _diagnose_zeilennamen()
    assert not erwartet - vorhanden, (
        f"Hier einer Zeile zugeordnet, aber in der Diagnose gibt es keine dieses "
        f"Namens: {sorted(erwartet - vorhanden)} — umbenannt oder entfernt?"
    )
    assert not vorhanden - erwartet, (
        f"Zeilen ohne zugehörigen Lauf: {sorted(vorhanden - erwartet)} — eine "
        "Diagnose, die einen abgeschafften Prozess als \"OK\" meldet, ist "
        "schlimmer als gar keine"
    )
