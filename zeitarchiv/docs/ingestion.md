# Schreibpfad (Ingestion)

`storage/ingestion.py` — `IngestionService.ingest()` ist der einzige Weg, wie
ein Wert dauerhaft wird. Sowohl `/api/write` (Integration) als auch Import
(Symcon/CSV) laufen letztlich hier durch.

## Idempotenz

Jedes Event trägt eine `event_id` (stabil auf Integrationsseite erzeugt,
übersteht Retries derselben Übertragung). Fehlt sie (ältere Integrations-
version), erzeugt `legacy_event_id()` deterministisch einen SHA-256-Hash aus
den restlichen Feldern.

Ablauf pro Event, unter der **Entitätssperre** (`StorageCoordinator.entity()`,
siehe [architecture.md](architecture.md)):

1. **Claim** in `ingested_events` (SQLite, `event_id` als Primärschlüssel).
   Existiert der Claim schon mit Status `done` → `duplicate`, sofort zurück.
2. Ein **vorhandener, noch offener** Claim (Status `processing`, kann nur aus
   einem Absturz zwischen Dateianhang und DB-Commit stammen) löst eine
   gezielte Existenzprüfung in Hot-CSV/Archiv-Parquet aus → bei Fund
   `recovered` (Wert war schon geschrieben, nur der DB-Abschluss fehlte).
3. Neuer Claim: **Zeitstempel-Duplikatsprüfung**. Für den normalen,
   monoton steigenden Live-Pfad genügt ein Vergleich mit `entities.last_ts`
   (kein Datei-I/O); nur wenn der neue Zeitstempel nicht hinter dem Index-
   Maximum liegt, wird die Datei tatsächlich durchsucht.
4. **Auflösungsfilter** (`should_accept_write`) — z. B. "nur alle 5 Minuten"
   bei individuell konfigurierter Auflösung.
5. **Wertänderungsfilter** (`should_accept_value`) — überspringt gerundet
   gleiche Folgewerte, behält aber mindestens alle sechs Stunden ein
   Lebenszeichen (verhindert, dass ein Chart bei einem seit Tagen
   unveränderten Sensor eine Lücke zeigt).
6. **Zähler-Rückgang-Erkennung**: bei `state_class = total_increasing` wird
   ein niedrigerer Folgewert nur **protokolliert** (`logger.warning`,
   sichtbar unter "Zählerrückgänge" in der Bereinigung), niemals blockiert —
   ein echter Zähler-Reset (Gerätetausch, Neustart) ist ein valider Wert.
7. **Rotation-Check** (`rotate.rotate_if_needed()`) — Monatswechsel seit dem
   letzten Schreibvorgang dieser Entität?
8. **Anhängen** an die Hot-CSV (`hotbuffer.append()`), Claim auf `done`.

Rückgabewerte (auch die HTTP-Antwort von `/api/write` je Event, aggregiert
als Zähler): `written`, `duplicate`, `recovered`, `skipped` (Auflösung),
`filtered` (Wertänderung).

## Crash-Recovery

`IngestionService.recover_pending()` läuft beim App-Start: iteriert alle
`processing`-Claims (per Definition höchstens die, die beim letzten
Absturz gerade in Bearbeitung waren) und schließt sie ab, falls der Wert
tatsächlich schon in Datei steht. Kein Datenverlust, keine Dopplung —
derselbe Mechanismus wie Schritt 2 oben, nur einmalig statt pro Event.

Die Idempotenz-Tabelle wird selbst nicht unbegrenzt groß: alle 10.000
abgeschlossenen Events (`_PRUNE_EVERY_COMPLETIONS`) werden Einträge älter als
7 Tage (`_IDEMPOTENCY_RETENTION_SECONDS`) entfernt.

## Beobachtbarkeit

Der normale Schreibpfad erzeugt keine Logzeile pro Messwert. Stattdessen wird
je `/api/write`-Batch auf `debug` eine Zusammenfassung mit Request-ID,
Ergebniszählern (`written`, `duplicate`, `recovered`, `skipped`, `filtered`),
Laufzeit und Durchsatz geschrieben. Auffällig langsame Batches sowie hohe
Duplikat- oder Filterquoten werden erst ab sinnvollen Mindestgrößen als
gedrosselte Warnung sichtbar.

Beim Start meldet die Recovery Anzahl und Alter offener Claims,
wiederhergestellte Events, betroffene Entitäten und bereinigte Ledger-Einträge.
Ein `processing`-Claim ab fünf Minuten Alter erzeugt eine Warnung; offene
Claims werden niemals durch das Pruning abgeschlossener Events gelöscht.

Der gezielt gestartete Entity-Trace ergänzt Eingangsdaten um das finale
Ingest-Ergebnis und eine gekürzte Event-ID. Details zu Log-Leveln, Event-Codes
und Datenschutz stehen in [logging.md](logging.md).

## Neue Entität

`Index.get_or_create_entity()` legt eine neue Zeile in `entities` an, sobald
die Home-Assistant-Integration eine bisher unbekannte Entity-ID sendet.
Auflösung/Aufbewahrung übernehmen dabei die globalen Standards aus
**Einstellungen → Archivierung** — spätere Standard-Änderungen wirken sich
nie auf bereits bekannte Entitäten aus, nur auf neu hinzukommende.

Ändert sich der Aggregationstyp einer bekannten Entität (z. B. Home Assistant
liefert plötzlich eine andere `state_class`), triggert das denselben
`on_type_change`-Callback wie ein manueller Wechsel: vollständige
Rollup-Neuberechnung (`rollup.rebuild_entity_rollups()`), da die Bucket-
Größen zwischen den Typen nicht kompatibel sind (siehe
[data-model.md](data-model.md)).
