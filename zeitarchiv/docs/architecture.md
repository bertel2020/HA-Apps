# Architektur

## Prozessmodell

Ein Container, zwei Prozesse, ein Anwendungscode:

```text
┌─────────────────────────────────────────────────────────────┐
│ Container                                                    │
│                                                               │
│  nginx (Gateway)                    uvicorn (app.main:app)   │
│  ├─ :8099  Ingress-Server    ──►    :8128 (127.0.0.1, intern)│
│  │  IP-Allowlist: Supervisor,                                │
│  │  localhost. Alles / → App.                                │
│  │                                                            │
│  └─ :8127  Öffentlicher Server ──►  :8128                    │
│     Nur /api/health, /api/write,                             │
│     /api/notices proxied. Alles                              │
│     andere → 404 direkt am Gateway,                          │
│     erreicht die App nie.                                    │
└─────────────────────────────────────────────────────────────┘
```

`run.sh` startet beide Prozesse und beendet den Container, sobald einer der
beiden unerwartet endet (kein halb-gesunder Zustand). Siehe
`nginx.conf`/`run.sh` im Repo-Root für die exakte Konfiguration.

**Wichtig:** Die Trennung zwischen Ingress- und öffentlichem Zugriff ist eine
reine Gateway-Entscheidung. `app/main.py` registriert alle Routen (UI +
`/api/*`) auf **einer** FastAPI-Instanz; es gibt keine zweite App-Instanz und
keine routenseitige Netzwerk-Prüfung. Wer nginx umgeht und direkt
`127.0.0.1:8128` erreicht, sieht die volle App ohne IP-Filter (siehe
[security.md](security.md) für die Verteidigungslinien, die dann noch
greifen: Bearer-Token auf `/api/*`, sonst keine).

## Request-Fluss (Schreibpfad)

```text
Zeitarchiv-Integration (Home Assistant)
   │ POST /api/write  { events: [...] }
   │ Authorization: Bearer <token>
   ▼
nginx :8127  (Allowlist: nur /api/write, /api/health)
   ▼
app.api_routes.write()
   │ Token-Prüfung (secrets.compare_digest)
   │ pro Event:
   ▼
storage.ingestion.IngestionService.ingest()
   │ StorageCoordinator.entity(entity_id)  ← serialisiert je Entität
   │ Idempotenz-Claim (SQLite ingested_events)
   │ Dedup (Event-ID, dann Zeitstempel)
   │ Auflösungs-/Wertänderungsfilter
   │ Zähler-Rückgang-Erkennung (nur protokolliert, nicht blockiert)
   ▼
storage.rotate.rotate_if_needed()   ← Monatswechsel? Hot → Archiv + Rollup
   ▼
storage.hotbuffer.append()          ← CSV-Append, laufender Monat
```

Details zu jedem Schritt: [ingestion.md](ingestion.md).

## Request-Fluss (Lesepfad, Chart/Tabelle)

```text
Browser (Alpine.js-Komponente)
   │ GET /api/query-multi?... (Charts)
   │ POST /api/query-table {...} (Vergleichstabellen)
   ▼
app.api_routes.api_query_multi() / api_query_table()
   │ @locked(...)  ← Entitäts-Lesesperre (verhindert Lesen während Rewrite)
   ▼
storage.query.query_series()  je Entität
   │ Zeitfenster aus range_key + offset (query._window())
   │ Innerhalb des laufenden Zeitraums: live aus Hot Buffer
   │ Abgeschlossene Perioden: aus vorberechnetem Rollup (rollup.py)
   ▼
JSON {series: [{points: [...]}]}  → ECharts im Browser
JSON {columns: [{series: [{aggregates: {...}}]}]} → Tabellen-Renderer
```

Vergleichstabellen (`table-compute.js`) senden alle benötigten Entitäten und
Zeiträume gemeinsam an `/api/query-table`. Ein request-lokaler Lese-Cache
verhindert, dass dieselbe Hot-Buffer-Datei für mehrere Spalten erneut geparst
wird. Der Server gibt pro Entität und Zeitraum nur `auto`/`avg`/`min`/`max`/
`sum` zurück; Gruppen- und Formelzeilen bleiben Darstellungslogik im Browser.
Gespeichert wird weiterhin nur die Spalten-/Zeilen-Struktur (`saved_tables`,
siehe [data-model.md](data-model.md)).

## Nebenläufigkeit

`storage/coordinator.py` (`StorageCoordinator`) ist die einzige
Synchronisationsprimitive für Dateizugriffe:

- **`entity(id)` / `entities(ids)`** — pro-Entität-Sperren (RLock), beliebig
  viele verschiedene Entitäten laufen parallel. Mehrfach-Sperren (`entities`)
  sortieren die IDs vor dem Acquire, um Deadlocks bei überlappenden
  Operationen auszuschließen.
- **`exclusive()`** — globale Wartungssperre für Backup, Restore, Retention-
  Durchsetzung, Rotation-Batch und Purge. Wartet, bis alle laufenden
  Entitäts-Operationen abgeschlossen sind, UND blockiert währenddessen neue
  Entitäts-Operationen (kein Verhungern durch Dauerschreibverkehr).

Das Modell ist bewusst kein globaler Lock: Home-Assistant-Schreibverkehr auf
Entität A darf weiterlaufen, während Entität B im Bereinigungs-Werkzeug
bearbeitet wird. Nur echte Wartungsvorgänge (die den GESAMTEN Datenbestand
anfassen können) pausieren alles andere.

Diese Sperren sind **prozessintern** (In-Memory, `threading`). Es gibt genau
einen App-Prozess pro Container — ein zweiter Prozess auf denselben Daten
(z. B. zwei Container gegen dasselbe `/data`-Volume) würde die Coordinator-
Garantien umgehen. Das ist kein unterstütztes Deployment.

## Ereignisgesteuerter Hintergrundplaner

`main.py:_maintenance_scheduler_loop()` läuft als Daemon-Thread, geprüft alle
30 Sekunden, unabhängig von Seitenaufrufen:

- stündlicher Statistik-Schnappschuss (`stats_snapshots`)
- stündlicher RAM-Schnappschuss (Supervisor-API, falls verfügbar)
- Retention-Übersicht/Duplikat-Übersicht/Bereinigungsvorschau: höchstens
  einmal pro Stunde neu berechnet, aus SQLite `settings` gecacht (siehe
  [data-model.md](data-model.md) → "Gecachte Vorschauen")
- geplante Backups, geplante Retention-Durchsetzung (beide: verpasster Lauf
  nach Downtime wird nachgeholt, nie mehrfach parallel)
- Host-Speicherplatz (`shutil.disk_usage(DATA_DIR)`) und die verfügbare
  Integrations-Version (GitHub-Raw-Abruf, höchstens einmal täglich,
  `ha_integration.py`) — beide rein informativ, ihr Ausfall (z. B. kein
  Internet) blockiert nie den Schreibpfad

Ein Fehler in einem Planer-Durchlauf wird geloggt, bricht die Schleife aber
nicht ab (`except Exception: logger.exception(...)`).

Ein zweiter, unabhängiger Daemon-Thread
(`main.py:_background_storage_reconciliation()`) gleicht beim Start
einmalig (danach beendet er sich) den Speicherindex entitätsweise mit
Archiv/Hot Buffer ab (siehe `storage/reconcile.py`) — läuft nur bei sauber
beendetem letzten Shutdown im Hintergrund, sonst (Restore/Crash) synchron
vor dem ersten Request.

Beide Threads schreiben bei jedem Durchlauf bzw. jeder geprüften Entität
einen Zeitstempel (`_last_scheduler_tick` / `_last_reconcile_tick`). Bleibt
einer davon länger als 5 Minuten ohne Fortschritt stehen (Deadlock am
Index- oder Storage-Lock), erscheint eine Meldung im Meldungs-Center
(`system.scheduler_stalled` / `system.storage_reconcile_stalled`,
`notices.py`) — die App bleibt für alle anderen Requests weiter
erreichbar, nur der jeweilige Thread hängt.

## Frontend-Rendering

Server-Side-Rendering (Jinja2) für den initialen Seitenaufbau, htmx für
partielle Neuladungen (Formulare, Polling), Alpine.js für clientseitigen
Zustand (Tabellen-/Chart-Editoren, Dropdown-Picker), ECharts für Diagramme.
Kein Build-Schritt, keine Bundler — alle JS-Dateien werden unverändert unter
`static/js/` ausgeliefert. Details: [frontend.md](frontend.md).
