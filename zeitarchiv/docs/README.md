# Zeitarchiv — Dokumentation

Für einen kurzen Überblick (Installation, Funktionsliste) siehe die
[App-README](../README.md); für Entwicklungs-Setup siehe
[DEVELOPMENT.md](../DEVELOPMENT.md) (lokal, nicht Teil des öffentlichen Repos).

**Zwei getrennte Produkte, ein Datenfluss.** Diese Dokumente beschreiben
ausschließlich die **App** (`addon/`, hier im Repo als `app/`). Den
Schreibpfad auf der Home-Assistant-Seite übernimmt die separat
veröffentlichte **[Zeitarchiv-Integration](https://github.com/bertel2020/HA-Zeitarchiv)**
(lokal `custom_components/zeitarchiv/`, eigene README, eigener Testlauf,
eigenes Änderungsprotokoll) — sie sendet an `/api/write`
([api-reference.md](api-reference.md)) und wird aus App-Sicht in
[ingestion.md](ingestion.md) und [architecture.md](architecture.md)
behandelt. Beide Repos zusammen zu pflegen (Versionierung, Sync,
Release-Reihenfolge): [operations.md](operations.md).

## Für Nutzer

| Dokument | Inhalt |
| --- | --- |
| [user-guide.md](user-guide.md) | Ausführliches, aufgabenorientiertes Benutzerhandbuch — Einrichtung, jede Seite im Detail, Einstellungen-Referenz, typische Aufgaben |

## Für Entwickler und Maintainer

| Dokument | Inhalt |
| --- | --- |
| [architecture.md](architecture.md) | Prozessmodell, Request-Fluss, nginx-Gateway, Nebenläufigkeit |
| [data-model.md](data-model.md) | Speicherformate (Hot Buffer/Archiv/Rollup), SQLite-Schema, Lösch-/Aufbewahrungslebenszyklus |
| [ingestion.md](ingestion.md) | Schreibpfad: Idempotenz, Filterregeln, Zähler-Semantik, Rotation |
| [api-reference.md](api-reference.md) | REST-API (`/api/write`, `/api/health`, `/api/query[-multi]`) |
| [frontend.md](frontend.md) | Template-/JS-Architektur (Jinja, Alpine.js, htmx, ECharts) |
| [operations.md](operations.md) | Backup/Restore, Retention, Wartungsplaner, Versionierung/Release |
| [security.md](security.md) | Auth-Modell, Netzwerktrennung, Pfad-/Zip-Validierung, Ressourcenlimits |
| [testing.md](testing.md) | Testsuite-Überblick, Ausführung |

## Grober Aufbau

```text
app/
  main.py              FastAPI-App, alle Ingress-Routen (~5000 Zeilen)
  api_routes.py         Öffentliche REST-API (/api/write, /api/health, /api/query*)
  security.py            Token-Erzeugung/-Prüfung
  formatting.py           Zahlen-/Datums-/Label-Formatierung (Jinja-Filter)
  storage/
    paths.py               Pfadvalidierung (Entity-ID, Symlink-Schutz)
    coordinator.py          Entitäts-/Exklusiv-Sperren
    hotbuffer.py            Laufender Monat (CSV, append-only)
    rotate.py               Hot Buffer → Archiv-Übergang
    rollup.py               Vorberechnete Aggregate (Stunde…Jahr)
    ingestion.py             Crash-fester, idempotenter Schreibpfad
    query.py                 Zeitraum-/Fenster-Logik für Charts/Tabellen
    index.py                 SQLite-Index (Metadaten, gespeicherte Objekte)
    cleanup.py                Ausreißer/Lücken/Duplikate, Soft-Delete, Purge
    retention.py               Endgültige Aufbewahrungs-Durchsetzung
    backup.py                   ZIP-Export/-Import/-Restore
    symcon_import.py, csv_import.py   Datenübernahme
  templates/               Jinja2-Seiten (Server-Side-Rendering + htmx-Fragmente)
  static/js/                Alpine.js-Komponenten, ECharts-Wrapper
```

Die App ist ein einzelner FastAPI-Prozess (siehe [architecture.md](architecture.md));
nginx davor trennt Ingress- und öffentlichen Port auf Netzwerkebene, nicht die
Anwendung selbst.
