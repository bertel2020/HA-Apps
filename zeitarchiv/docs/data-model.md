# Datenmodell

Zeitreihen-Rohdaten leben in Dateien (CSV/Parquet); alles Strukturelle
(Metadaten, gespeicherte Charts/Tabellen/Dashboards, Einstellungen, Job-
Historie) liegt in einer einzigen SQLite-Datei, `index.sqlite`. Diese Trennung
ist zentral: Rohdaten wachsen unbegrenzt und werden nie in SQLite gehalten,
der Index bleibt dadurch klein und schnell abfragbar.

## Verzeichnislayout

```text
$ZEITARCHIV_DATA_DIR/
  index.sqlite            SQLite-Index (siehe unten)
  hot/
    <entity_id>-<YYYY-MM>.csv       laufender Monat, unkomprimiert, append-only
  archive/
    <entity_id>/
      <YYYY-MM>.parquet             abgeschlossene Monate, zstd-komprimiert
  rollup/
    <entity_id>/
      stunde.parquet | tag.parquet   feine Rollup-Stufe (siehe unten)
      monat.parquet
      jahr.parquet
  backups/                          erzeugte, herunterladbare ZIP-Backups
  symcon_import/, csv_import/       temporäre Upload-Zwischenablage (nicht Teil des Backups)
```

Alle Pfade werden über `storage/paths.py` aufgelöst, das jede Entity-ID gegen
`^[a-z][a-z0-9_]*\.[a-z0-9_]+$` validiert und jeden konstruierten Pfad per
`Path.resolve()` gegen Verlassen des jeweiligen Storage-Bereichs prüft (auch
über bereits vorhandene Symlinks). Kein Code-Pfad baut Dateinamen aus
Nutzereingaben ohne diese Validierung.

## Hot Buffer (laufender Monat)

- Format: CSV, eine Zeile `ts,value[,event_id]` pro Messpunkt.
- Bewusst **unkomprimiert**: Parquet lässt sich nicht beliebig fortlaufend
  anhängen; ein Absturz mitten im Schreiben macht ein CSV nicht unlesbar,
  eine Parquet-Datei ohne Footer schon.
- Ein neuer Wert landet immer hier, nie direkt im Archiv.
- Live-Abfragen des laufenden Zeitraums lesen und aggregieren diese Datei
  direkt (kein Rollup existiert für unabgeschlossene Perioden).

## Rotation (Hot → Archiv)

`storage/rotate.py`: beim ersten Schreibvorgang einer Entität in einen neuen
Kalendermonat (oder manuell unter **Housekeeping → Rotation**) wird die
Hot-Datei des/der Vormonate(s) als Parquet ins Archiv geschrieben, ihre
Rollup-Zeilen berechnet (`rollup.append_completed_month()`), und die
Hot-CSV-Datei gelöscht. Eine Entität, die aufhört zu senden, würde ihre
letzte Hot-Datei sonst nie automatisch archivieren — daher der manuelle
Nachzieh-Mechanismus.

## Rollups (vorberechnete Aggregate)

Nur **abgeschlossene** Perioden werden vorberechnet; die laufende Periode
berechnet `query.py` immer live aus dem Hot Buffer. Bucket-Größe hängt vom
Aggregationstyp der Entität ab:

| Aggregationstyp | Feine Bucket-Größe | Datei | Bedient |
| --- | --- | --- | --- |
| `counter` (Zähler, `total`/`total_increasing`) | 5 Min. | `tag.parquet` | Woche, Monat |
| `standard` / `switch` (Messwert/Schalter) | 1 Min. | `stunde.parquet` | Woche, Monat |

Zusätzlich je Entität `monat.parquet` (bedient Jahr, bei Zählern auch
Dekade) und bei Standard/Schalter `jahr.parquet` (bedient Dekade). Jede
Zeile trägt `bucket_start` (Unix-Timestamp UTC) plus, je nach Typ,
`value`/`min_value`/`max_value` (Zähler: Summe bzw. Bucket-Extremwerte;
Standard: Mittelwert bzw. Extremwerte) oder `on_seconds` (Schalter:
Einschaltdauer im Bucket). `min_value`/`max_value` werden aus den
tatsächlichen Rohwerten des Buckets berechnet — Vergleichstabellen mit
Aggregation "Min"/"Max" lesen daher das echte Extremum, nicht das Extremum
der Bucket-Durchschnitte.

Ein Aggregationstyp-Wechsel einer Entität (selten, z. B. wenn Home Assistant
eine `device_class` ändert) löst `rollup.rebuild_entity_rollups()` aus —
komplette Neuberechnung aus dem Archiv, da die Bucket-Größen nicht kompatibel
sind.

**Zusätzliche Stunden-Stufe für Energiedashboard-Zählerrollen.** Ist eine
`counter`-Entität als Energiedashboard-Rolle zugeordnet (`entities
.hourly_rollup`, automatisch beim Speichern der Energiedashboard-Konfiguration
gesetzt/gelöscht, siehe `energiedashboard_routes.sync_hourly_rollup_flags()`),
schreibt `rollup.append_completed_month()` **additiv** zusätzlich zu
`tag.parquet` auch `stunde.parquet` fort — `tag.parquet` bleibt für diese
Entitäten unverändert bestehen, kein bestehender Lesepfad (Wochen-/Monats-
Balkendiagramm, Aufbewahrung) muss davon wissen. Grundlage für die
wochentagsweise Aggregation des Tageslastprofils (Energiedashboard) über
Monats-/Jahreszeiträume (`query.query_hourly_counter_series()`). Neu
geflaggte Entitäten bekommen ihre bereits archivierten Monate rückwirkend
nachgebaut: eine Warteschlange (Setting
`energiedashboard_hourly_backfill_pending`) wird vom Wartungsplaner mit einer
Entität je 30-Sekunden-Tick abgearbeitet
(`energiedashboard_routes.process_pending_hourly_backfill()`, nutzt
`rollup.rebuild_entity_rollups(..., hourly_rollup=True)`).

## Soft-Delete und Purge

Bereinigung (Ausreißer/Lücken/Duplikate/Wiederholungen markieren) ist
**nie destruktiv**: `deleted_points` in SQLite speichert eine Zeile pro
gelöschtem *Vorkommen* (nicht pro eindeutigem Zeitstempel — ein Duplikat mit
zwei identischen Zeitstempeln lässt sich so gezielt nur einmal entfernen).

- **Ansicht/Abfrage:** `filter_deleted_occurrences()` filtert markierte
  Vorkommen aus jeder Anzeige/Berechnung heraus, ohne die Rohdatei
  anzufassen.
- **Rückgängig:** Löschen aus `deleted_points`, Rohdatei bleibt unverändert
  — jederzeit möglich, solange nicht purged wurde.
- **Purge** (`cleanup.purge_hot_buffer()` / `purge_archived_months()`, nur
  über **Housekeeping → Speicherplatz**, explizite Bestätigung): entfernt
  die markierten Zeilen physisch. Für den laufenden Monat ein CSV-Rewrite;
  für bereits archivierte Monate ein Parquet-Rewrite **plus** Neuberechnung
  der betroffenen Rollup-Zeilen (`rollup.replace_month()` /
  `remove_month()`). Danach ist der Vorgang endgültig.

## Aufbewahrung (Retention)

Anders als Purge (einzelne, vorher markierte Werte) betrifft Retention ganze,
**nie markierte** Zeiträume: Werte älter als die je Entität konfigurierte
Frist werden bei aktivierter Durchsetzung endgültig gelöscht
(`storage/retention.py`). Arbeitet ausschließlich auf **ganzen Monaten** —
ein archivierter Monat wird komplett gelöscht statt teilweise umgeschrieben,
wodurch Rollup-Zeilen anhand ihres `bucket_start` konsistent mitentfernt
werden können, ohne einen riskanten Parquet-Rewrite. Entitäten mit
Aufbewahrung `unlimited` sind von jeder automatischen Durchsetzung
ausgenommen.

## SQLite-Schema (`index.sqlite`)

Migrationen laufen additiv beim Start (`ALTER TABLE ... ADD COLUMN`, geprüft
über `PRAGMA table_info`) — es gibt kein Versions-/Migrationsnummern-System,
jede neue Spalte bekommt einen Default und einen expliziten
Existenz-Check in `Index.__init__()`.

| Tabelle | Zweck |
| --- | --- |
| `entities` | Eine Zeile je bekannter Entität: Aggregationstyp, Auflösung, Aufbewahrung, Nachkommastellen, Wertfilter, Ausreißer-/Lücken-Schwellen, `first_ts`/`last_ts`/`last_value` (Zustand für Idempotenz- und Filterprüfungen), `row_count`/`size_bytes` (für die Statistik, inkrementell gepflegt statt bei jeder Anzeige neu gezählt) |
| `deleted_points` | Soft-Delete-Markierungen, siehe oben. Indiziert auf `(entity_id, ts)` und `(entity_id, deleted_at)` |
| `ingested_events` | Idempotenz-Ledger des Schreibpfads (siehe [ingestion.md](ingestion.md)); Einträge älter als 7 Tage werden periodisch geprunt |
| `settings` | Generischer Key-Value-Store: globale Auflösungs-/Aufbewahrungs-Standards, Loglevel, Farbschema, API-Token, sowie **gecachte teure Vorschauen** (siehe unten) |
| `stats_snapshots`, `memory_snapshots` | Stündliche Schnappschüsse für Statistik-Verlaufsgrafiken |
| `backup_jobs`, `retention_jobs` | Dauerhafte Job-Historie (Status, Fehler, Kennzahlen) — überlebt Neustarts, im Gegensatz zu einem reinen "letzter Lauf"-Zeitstempel |
| `saved_charts` | Gespeicherte Chart-**Abfragen** (Entitäten + Zeitraum-Einstellungen), kein Datenschnappschuss — Werte werden bei jedem Aufruf live nachgeladen |
| `saved_tables`, `table_columns`, `table_rows` | Vergleichstabellen: Struktur (Zeilen=Größen, Spalten=Zeiträume) getrennt von `style_json` (rein optische Darstellung, siehe [frontend.md](frontend.md)) |
| `dashboards`, `dashboard_pins` | Mehrere benannte Dashboards (Favorit, Standard, Präziser Modus, Lücken auffüllen); eine gemeinsame Pin-Tabelle für Charts, Tabellen UND direkt gepinnte Entitäten ("Werte-Kacheln", `item_type`/`item_id`/`item_entity_id`), da alle drei gemeinsam sortiert werden müssen |

### Eindeutige Namen (`dashboards`, `saved_charts`, `saved_tables`)

Die drei Tabellen führen jeweils eindeutige, höchstens
`MAX_SAVED_NAME_LENGTH` (50) Zeichen lange Namen. Die Prüfung sitzt in
`Index._ensure_valid_name_locked()` und läuft innerhalb derselben
Transaktion wie das `INSERT`/`UPDATE`, das sie absichert — also nicht als
UNIQUE-Constraint in der Tabelle: verglichen wird ohne Groß-/Kleinschreibung
und ohne Randleerzeichen, und zwar mit Pythons `casefold()`. SQLite kennt
ohne ICU-Erweiterung nur ASCII-Faltung und hielte "Küche" und "KÜCHE"
deshalb für verschiedene Namen.

Verletzungen werfen `DuplicateNameError` bzw. `NameTooLongError` (beide
`InvalidNameError`); ein zentraler Exception-Handler in `main.py` übersetzt
sie nach `409` bzw. `400`. Die Duplizieren-Routen umgehen die Kollision
selbst, indem sie über `copy_name_for()` den ersten freien Namen der Reihe
"(Kopie)", "(Kopie 2)" … wählen und den Ursprungsnamen dabei so weit kürzen,
dass der Zusatz noch in die Längengrenze passt.

### Gecachte Vorschauen (`settings`-Tabelle)

Drei rechenintensive Vorschauen (Retention-Übersicht, Duplikat-Übersicht,
Bereinigungsvorschau) sind **nicht** live berechnet, sondern als JSON-Blob
mit `generated_at`-Zeitstempel in `settings` zwischengespeichert und werden
vom Wartungsplaner höchstens stündlich aktualisiert (bzw. sofort erzwungen
nach einer tatsächlichen Aktion wie einem Purge-Klick). Grund: eine naive
Live-Berechnung bei jedem Seitenaufruf skaliert mit der Anzahl markierter
Zeilen × Anzahl Archiv-Monate und wurde bei großen Entitäten (>500k markierte
Zeilen) zum spürbaren Ladezeit-Problem der Einstellungen-Seite (behoben in
0.40.0, siehe `CHANGELOG.md`).

## Warum kein größeres RDBMS / keine Zeitreihen-DB?

Bewusste Entscheidung aus dem ursprünglichen Konzept: Parquet+zstd liefert
für ausschließlich-append-then-read-Workloads bessere Kompression als eine
Zeilen-DB, benötigt keinen laufenden Datenbank-Server-Prozess, und lässt sich
1:1 als Datei kopieren/sichern. SQLite übernimmt ausschließlich Metadaten und
strukturierte, kleine, oft geänderte Objekte — nie die eigentlichen
Zeitreihen.
