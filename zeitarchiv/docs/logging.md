# Logging und Ingest-Observability

*[English version](en/logging.md)*

Zeitarchiv schreibt Anwendungslogs nach `stdout`/`stderr`; Home Assistant
Supervisor übernimmt die dauerhafte Haltung. Zusätzlich hält der Prozess
einen thread-sicheren Ringpuffer mit höchstens 2.000 Einträgen für die lokale
Live-Ansicht. Die Logseite kann zwischen beiden Quellen wechseln:

- **Live:** schnell, ohne Supervisor-Aufruf, nur aktueller Prozess und
  begrenzter Puffer;
- **Supervisor-Historie:** weiter zurückreichend, abhängig von Supervisor und
  dessen Aufbewahrung.

Die Live-Ansicht aktualisiert sich alle 15 Sekunden. Erfolgreiche Abrufe von
`/api/logs`, `/api/health` und dem Debug-Status erzeugen selbst keine
Access-Logzeilen. Fehler bleiben sichtbar.

## Logseite: Level, Bereich und Aufbewahrung

Die Logseite filtert nach **Level** und nach **Bereich** (Logger-Gruppe). Beide
wirken auf Anzeige, Download und Supervisor-Historie und lassen sich mit der Suche
kombinieren. Die Zuordnung läuft nach Logger-Name; alles Unbekannte fällt in
„Sonstiges“, ein neuer Logger bleibt also sichtbar.

| Bereich | Logger |
| --- | --- |
| KI-Zugriff | `ai_routes`, `ai_settings_routes` |
| Aufnahme & API | `api_routes`, `storage.ingestion`, `storage.hotbuffer`, `trace` |
| Wartung & Sicherung | `background`, `housekeeping_routes`, `index_optimization`, `supervisor_stats`, `storage.backup_external` |
| Import & Export | `import_routes`, `export_routes`, `storage.ha_import`, `storage.ha_statistics`, `storage.vm_import`, `storage.zip_guard` |
| Daten bearbeiten | `entity_delete_routes`, `marked_values_routes` |
| HTTP-Zugriffe | `http`, `access` |
| Sonstiges | alles andere (`main`, `uvicorn`, `currency`, …) |

Sicherung und Aufbewahrung loggen beide aus `background` und bilden daher mit der
übrigen Wartung einen Bereich. Fortsetzungszeilen (Tracebacks) gehören zum Bereich der
Zeile davor.

Aufbewahrung: Zeitarchiv schreibt keine eigene Logdatei. Der Ringpuffer hält die
letzten 2.000 Einträge im Arbeitsspeicher und ist nach einem Neustart leer. Zeilen
unterhalb des eingestellten Levels werden gar nicht erst gespeichert. Die
Supervisor-Historie zeigt nur den aktuellen Containerstart (etwa die letzten 2.000 bis
8.000 Zeilen, höchstens 8 MB). Wie lange der Supervisor Zeilen vorhält, bestimmt Home
Assistant. Dauerhaft festgehalten werden nur die KI-Zähler und das KI-Änderungsprotokoll
(siehe unten), im Index statt im Log.

## Empfohlene Konfiguration

Im Normalbetrieb: Anwendungslevel `warning`, HTTP-Protokoll **Nur
fehlgeschlagene Anfragen**. `info` dokumentiert erfolgreich abgeschlossene
System- und Benutzeraktionen; `debug` ergänzt technische Kennzahlen und sollte
nur gezielt aktiviert werden.

| Level | Bedeutung |
| --- | --- |
| `debug` | Batch-Zähler, Laufzeiten, Recovery- und Pruning-Details |
| `info` | erfolgreich abgeschlossene Aktion oder Job |
| `warning` | auffällige, eingeschränkte oder selbst reparierbare Situation |
| `error` | fehlgeschlagene Aktion bei weiterhin kontrolliertem Betrieb |
| Exception mit Stacktrace | unerwarteter Fehler mit Operationskontext |

## Korrelation und Ereigniscodes

Jeder HTTP-Request erhält eine zufällige Request-ID. Sie erscheint im
`X-Request-ID`-Antwortheader sowie in HTTP-, Ingest- und Entity-Trace-Logs.
Wichtige Meldungen besitzen einen stabilen `event=`-Code und je nach Vorgang
`request_id`, `job_id`, Zähler und `duration_ms`/`duration_s`. Dadurch bleiben
die deutschen Meldungen lesbar und zugleich gezielt durchsuchbar.

Langsame erfolgreiche HTTP-Anfragen werden ab zwei Sekunden als Warnung
sichtbar, selbst wenn das normale Access-Log ausgeschaltet ist. Wiederkehrende
Warnungen wie Auth-Fehler, Zählerrückgänge oder auffällige Ingest-Quoten sind
zeitlich gedrosselt; der nächste sichtbare Eintrag nennt die Zahl der
unterdrückten Wiederholungen.

## Ingest

Ein normaler `/api/write`-Batch erzeugt auf `debug` genau eine Zusammenfassung:
Anzahl der Events, `written`, `skipped`, `filtered`, `duplicate`, `recovered`,
Laufzeit und Events pro Sekunde. Kleine Batches erzeugen keine Quotenwarnung.
Auffällig langsame Batches, hohe Duplikatquoten und nahezu vollständig
gefilterte Batches werden gedrosselt auf `warning` gemeldet.

Die Start-Recovery protokolliert offene Claims, Alter des ältesten Claims,
wiederhergestellte Events, betroffene Entitäten und Pruning. Claims im Zustand
`processing`, die mindestens fünf Minuten alt sind, werden gesondert gewarnt.
Der verbindliche Zustand bleibt immer die SQLite-Tabelle `ingested_events`;
das Log ist ausschließlich die Beobachtungsschicht.

## KI-Zugriff

Alle Meldungen sind `info`, also nur bei Anwendungslevel `info` oder `debug` sichtbar.

| Event | Wann |
| --- | --- |
| `ai_tool_call` | ein KI-Werkzeug wurde ausgeführt (Werkzeug, Status, Zahl der Entitäten, Dauer) |
| `ai_tool_rejected` | Aufruf mit `403` abgelehnt (`reason=ai_disabled` oder `tool_disabled`); je Grund und Werkzeug höchstens einmal in fünf Minuten, sonst mit `unterdrueckte_wiederholungen` |
| `ai_access_changed` | Freigabe gespeichert (`enabled`, `scope`, Zahl der Entitäten, `exclude_presence`, Zahl abgeschalteter Werkzeuge); nie der Notiztext |

Nutzungszähler (je Tag, 90 Tage) und die letzten 50 Änderungen liegen zusätzlich in den
Einstellungen des Index und erscheinen unter Einstellungen → KI-Zugriff → Nutzung.

## Redaction und Diagnosewerkzeuge

Vor jeder Ausgabe werden Bearer-Token und typische Secrets (`token`,
`api_token`, `password`, `secret`) aus Text, JSON, Headern und Querystrings
maskiert. Das gilt für App-, HTTP-, Trace-, Uvicorn- und FastAPI-Ausgaben; beim
Einlesen von Supervisor-Zeilen wird erneut maskiert. Zeitstempel verwenden ISO
8601 mit Millisekunden und lokaler Zeitzone.

Entity-IDs und Messwerte sind keine Zugangsdaten, können aber sensible
Betriebsinformationen darstellen:

- Der **Write-Capture** zeichnet genau den nächsten Batch ohne
  Authorization-Header auf, läuft spätestens nach 60 Minuten ab und wird auch
  ohne weiteren Seitenaufruf gelöscht. Der Download ist mit
  `Cache-Control: no-store` gekennzeichnet.
- Der **Entity-Trace** läuft höchstens 15 Minuten und zeigt Eingangswert,
  gekürzte Event-ID und finales Ingest-Ergebnis. Er bleibt bewusst unabhängig
  vom allgemeinen Loglevel sichtbar.

Beide Werkzeuge nur gezielt starten und heruntergeladene Capture-Dateien nach
der Diagnose sicher entfernen.
