# Tests

Eine gemeinsame Testsuite für App **und** Integration liegt unter `tests/`
im Repository-Stamm (nicht innerhalb von `addon/`) — beide Produkte werden
zusammen getestet, da die Integration echte Requests gegen die App-typischen
Antwortformen simuliert.

```bash
python3 -m pytest -q
```

## Sync in die Produkt-Repos

`tests/` wird pro Datei nach `HA-Apps/zeitarchiv/tests/` (App) bzw.
`HA-Zeitarchiv/tests/` (Integration) übertragen, automatisch klassifiziert
anhand ihrer tatsächlichen Importe (`scripts/sync_tests.py`, analog zu
`scripts/sync_versions.py`):

```bash
python3 scripts/sync_tests.py          # überträgt, listet Änderungen
python3 scripts/sync_tests.py --check  # nur prüfen, Exit-Code 1 bei Drift
```

Eine Datei, die sich nicht eindeutig zuordnen lässt (Signale für beide
Zielrepos oder für keins), bricht den Lauf ab statt still übersprungen zu
werden — Details und die beiden dokumentierten Ausnahmen
(`test_routes.py`, `test_metadata_and_versions.py`) im Docstring des
Skripts.

Kein `pytest.ini`/`pyproject.toml` — Standard-Discovery über `tests/test_*.py`.
`tests/_pkg.py` registriert `custom_components.zeitarchiv` als Namespace-Paket
**ohne** dessen `__init__.py` auszuführen, damit Integrationsmodule
(`const.py`, `events.py`, `filtering.py`, `queue_writer.py`, …) isoliert
importierbar sind, ohne ein echtes `homeassistant`-Paket zu benötigen (das in
der Testumgebung nicht installiert ist).

## Abdeckungsschwerpunkte (Auswahl)

| Datei | Prüft |
| --- | --- |
| `test_ingestion.py` | Idempotenz, Dedup, Crash-Recovery (siehe [ingestion.md](ingestion.md)) |
| `test_logging.py`, `test_api_observability.py`, `test_logs_template.py` | Secret-Redaction, ISO-Zeitstempel, Rate-Limits, Request-Korrelation, Capture-TTL, Entity-Trace und Logquellen |
| `test_index.py` | SQLite-Schema, Migrationen, Aggregationen |
| `test_query.py`, `test_period_navigation.py` | Zeitfenster-/Range-Logik |
| `test_rollup.py`, `test_rotate.py` | Bucket-Berechnung, Hot-→-Archiv-Übergang |
| `test_retention.py`, `test_cleanup.py` | Aufbewahrung, Soft-Delete/Purge |
| `test_backup.py`, `test_backup_scheduler.py` | Backup-Format, Restore-Validierung, Zeitplan |
| `test_storage_coordinator.py` | `StorageCoordinator`-Sperren (Nebenläufigkeit, Timeout/`CoordinatorBusy`) |
| `test_http_middleware.py` | Security-Header, `X-Request-ID`, Access-Log-Korrelation (globale ASGI-Middleware in `main.py`) |
| `test_energiedashboard_config_schema.py` | `energiedashboard_config`-`schema_version` (Downgrade-Schutz) |
| `test_security.py` | Token-Erzeugung/-Vergleich |
| `test_paths.py` | Entity-ID-Validierung, Symlink-/Traversal-Schutz |
| `test_csv_import.py`, `test_import_reports.py` | Import-Pipeline |
| `test_route_modules.py`, `test_metadata_and_versions.py` | Routen-Registrierung, Versions-Konsistenz (`sync_versions.py`) |
| `test_requirements_lock.py` | `requirements.txt` vollständig gepinnt und mit Dockerfile konsistent |
| `test_*_template.py`, `test_*_breadcrumbs.py` | Template-Rendering/-Struktur ohne laufenden Server (Jinja direkt gerendert und auf erwartete Fragmente geprüft) |
| `test_config_flow_sortable_entities.py`, `test_options_transfer.py` | Integrations-seitige Config-Flow-Logik |

## Was hier bewusst fehlt

Kein Browser-/E2E-Test (kein Playwright/Selenium) — Alpine.js-/htmx-
Interaktionen werden manuell im Browser verifiziert. Template-Tests prüfen
gerenderten HTML-Output, nicht clientseitiges Verhalten.

## `main.py`-Zeilenbudget

`test_route_modules.py::test_main_keeps_external_api_and_report_routes_out_of_the_monolith`
erzwingt `len(main.py.splitlines()) < 5_800` als Architektur-Wächter gegen
unkontrolliertes Wachstum des Monolithen (angehoben von ursprünglich 4.800
über 5.700 auf zuletzt 5.800, siehe Git-Historie des Tests). `/api/*`
(`api_routes.py`), Import-Reports (`report_routes.py`) und seit 0.51.0 auch
der komplette Symcon-/CSV-/Home-Assistant-Import (`import_routes.py`) sind
dafür ausgelagert — jeweils ein `*Dependencies`-Frozen-Dataclass plus ein
`*Service` mit `.router()`, der die Routen als verschachtelte Closures
registriert (siehe `ReportService`/`ImportService` als Vorlage für weitere
Extraktionen). Stand 0.80.2: **rund 5.720 Zeilen**, Test grün. Wächst
`main.py` nochmal spürbar über das Budget, ist eine eigene
`housekeeping_routes.py` (analog zu den bereits ausgelagerten Modulen) der
nächste Schritt, nicht ein weiteres stillschweigendes Anheben der Zahl.
