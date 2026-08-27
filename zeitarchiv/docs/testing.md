# Tests

Eine gemeinsame Testsuite für App **und** Integration liegt unter `tests/`
im Repository-Stamm (nicht innerhalb von `addon/`) — beide Produkte werden
zusammen getestet, da die Integration echte Requests gegen die App-typischen
Antwortformen simuliert.

```bash
python3 -m pytest -q
```

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
| `test_index.py` | SQLite-Schema, Migrationen, Aggregationen |
| `test_query.py`, `test_period_navigation.py` | Zeitfenster-/Range-Logik |
| `test_rollup.py`, `test_rotate.py` | Bucket-Berechnung, Hot-→-Archiv-Übergang |
| `test_retention.py`, `test_cleanup.py` | Aufbewahrung, Soft-Delete/Purge |
| `test_backup.py`, `test_backup_scheduler.py` | Backup-Format, Restore-Validierung, Zeitplan |
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

## Bekannter, offener Befund: `main.py`-Zeilenbudget

`test_route_modules.py::test_main_keeps_external_api_and_report_routes_out_of_the_monolith`
erzwingt `len(main.py.splitlines()) < 4_800` als Architektur-Wächter gegen
unkontrolliertes Wachstum des Monolithen. Stand 0.40.0: **5.140 Zeilen** —
der Test schlägt bewusst weiterhin fehl, statt das Limit stillschweigend
hochzusetzen (das würde den Zweck der Prüfung aushebeln). `api_routes.py`
und `report_routes.py` (die vom selben Test geprüften Auslagerungen) sind
weiterhin sauber getrennt; es fehlt eine weitere, noch nicht vorgenommene
Extraktion (Kandidaten: Bereinigungs- oder Einstellungen-Routen) aus
`main.py`, um wieder unter das Budget zu kommen. Bis dahin bleibt dieser eine
Test rot — kein Grund zur Beunruhigung bei neuen `pytest`-Läufen, aber auch
keiner, ihn zu ignorieren.
