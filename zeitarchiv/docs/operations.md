# Betrieb

*[English version](en/operations.md)*

## Backup und Restore

Implementierung: `storage/backup.py`.

**Enthalten:** `index.sqlite`, `hot/`, `archive/`, `rollup/`. **Bewusst
ausgeschlossen:** `symcon_import/`/`csv_import/` (temporäre Upload-
Zwischenablage, potenziell groß, jederzeit neu hochladbar) und `server.log`
(reine Diagnose). Parquet-Dateien werden `ZIP_STORED` statt `ZIP_DEFLATED`
gepackt — sie sind bereits zstd-komprimiert, ein zweiter Kompressionslauf
kostet nur CPU-Zeit.

Jedes Backup enthält `zeitarchiv-manifest.json` (Format-Kennung
`zeitarchiv-portable-backup`, Format-Version, Dateiliste mit Größe und
SHA-256 je Datei). `validate_backup()` prüft vor jeder Wiederherstellung:

1. Manifest vorhanden und Format-Kennung korrekt.
2. Jede gelistete Datei existiert im ZIP mit exakt passender Größe und
   SHA-256.
3. (In `create_backup`/beim Hochladen zusätzlich:) ZIP-Struktur, entpackte
   Gesamtgröße und Kompressionsverhältnis gegen die Grenzen aus
   `app/limits.py` (siehe [security.md](security.md)).

Eine Wiederherstellung ist **vorbereitet und rollback-fähig**: der aktuelle
Datenbestand wird vor dem Überschreiben in ein Rollback-Verzeichnis
verschoben (`.zeitarchiv-restore-rollback-*`), nicht gelöscht. Schlägt das
Schreiben des wiederhergestellten Bestands fehl, wird aus diesem Verzeichnis
zurückgerollt. Die Veröffentlichung eines neu erzeugten Backups selbst ist
atomar (Schreiben in eine temporäre Datei, dann `rename()`).

Sowohl Backup als auch Restore laufen unter
`StorageCoordinator.exclusive()` (siehe [architecture.md](architecture.md)) —
kein gleichzeitiger Schreibverkehr während der Operation.

### Externes Ziel

Implementierung: `storage/backup_external.py` (Netzwerkspeicher),
`storage/webdav.py` (WebDAV), Ablauf in `background.py`
(`external_transfer`), Oberfläche in `backup_external_routes.py`. Konzept und
Begründungen: `BACKUP_EXTERN_KONZEPT.md` (Dev-Dokument).

**Ablauf.** Der Backup-Worker erzeugt das Backup lokal und kopiert es danach
auf das Ziel; erst dann räumt er lokal auf. Der Job bleibt `success`, auch wenn
nur die Übertragung scheitert — Status, Ziel, Zeitpunkt und Fehlertext stehen in
`backup_jobs.external_status` (`NULL`, `ok`, `failed`, `running`),
`external_target`, `external_finished_at`, `external_error`. Backups von vor der
Einführung haben `NULL` und werden wie bisher aufgeräumt.

**Netzwerkspeicher.** Die App mountet nichts: Sie sieht nur Freigaben, die Home
Assistant unter `/media` oder `/share` eingebunden hat (`map` in `config.yaml`),
gefunden über `/proc/self/mountinfo`. Ein systemd-Automount (`autofs`) zählt als
eingebunden und wird beim ersten Zugriff aktiviert. Weil NFS beliebig lange
blockieren kann, laufen Test und Übertragung hinter einem Watchdog
(`run_with_watchdog`): Rührt sich der Transfer 120 s nicht, bricht die App ab
und meldet einen Fehler. Der Thread bleibt als Daemon zurück; ein hängender
Mount belegt also einen Thread, aber nicht den Worker.

**Übertragung.** Geschrieben wird als `.zeitarchiv-backup-*.part` und per
`os.replace` umbenannt, unter dem Endnamen liegt nie eine halbe Datei. Die
Änderungszeit wird übernommen, damit die Reihenfolge der Aufbewahrung stimmt.
Übrig gebliebene `.part`-Dateien (Abbruch, Absturz) räumt die App nach 24 h
am Ziel weg. Bei WebDAV heißt der Zwischenstand
`.zeitarchiv-upload-<zufall>-<name>` — er endet bewusst wie der Endname auf
`.zip`, weil Nextcloud ab Version 31 Dateinamen auf `.part` mit Status 400
ablehnt — und wird per `PUT`, `PROPFIND` (Größenvergleich) und `MOVE`
abgeschlossen. Reste abgebrochener Uploads (auch alte `.part`-Namen) räumt
die App vor jeder Übertragung weg. Weiterleitungen
folgt der Client nicht, sondern nennt die Zieladresse im Fehlertext.

**Aufbewahrung.** Lokal gilt dieselbe Anzahl-/Alters-Regel wie bisher, mit einer
Ausnahme: Backups mit `external_status` `failed` oder `running` werden nicht
gelöscht, bis sie übertragen sind (`prune_backups(..., protected)`). Am Ziel
gibt es nur „wie lokal“ oder „nie löschen“, und gelöscht werden ausschließlich
Dateien nach dem Muster `zeitarchiv-backup-*.zip`. Bei WebDAV gibt es keine
verlässliche Änderungszeit; die Reihenfolge kommt aus dem Zeitstempel im
Dateinamen.

**Fehlersuche bei fehlgeschlagener Übertragung.**

1. Meldung `backup.external_failed` und Spalte „Extern“ auf der Seite Sicherung
   zeigen den Fehlertext. Er ist bewusst für die Oberfläche formuliert
   („Zugriff verweigert“, „Anmeldung abgelehnt (401)“, Zertifikat,
   Zeitüberschreitung …).
2. „Verbindung testen“ legt eine Testdatei an und löscht sie wieder; so lässt
   sich das Ziel prüfen, ohne ein Backup abzuwarten.
3. „Übertragen“ bzw. „Erneut übertragen“ in der Backup-Tabelle wiederholt die Übertragung eines
   einzelnen Backups. Ohnehin versucht die App nach jeder erfolgreichen
   Übertragung, offene frühere Backups nachzuliefern.
4. Ein beim Neustart noch laufender Transfer wird als `failed` markiert
   (`recover_interrupted_backup_jobs`).

**Passwort (WebDAV).** Nur als Add-on-Option `webdav_password` in
`options.json` (Ersatz: Umgebungsvariable `ZEITARCHIV_WEBDAV_PASSWORD`),
nie im Index: Der Index liegt in jedem Backup, und das Passwort würde sonst
mit auf das Ziel kopiert. Eine Änderung wirkt erst nach Neustart des Add-ons.

## Retention-Durchsetzung

`storage/retention.py`, ausgeführt manuell (Vorschau + Klick) oder geplant
(täglich oder wöchentlich zur konfigurierten lokalen Uhrzeit,
`settings.retention_enforcement` + `retention_enforcement_time`; bei
wöchentlichem Modus zusätzlich `retention_enforcement_weekday`). Nächster/
letzter Lauf in `retention_jobs` protokolliert (überlebt Neustarts). Nach
einer Downtime wird höchstens **ein** verpasster Lauf nachgeholt, nie
mehrere rückwirkend.

## Wartungsplaner

`background.py:BackgroundService._maintenance_scheduler_loop()`, ein einzelner
Daemon-Thread, alle 30 Sekunden geprüft. Bündelt: Statistik-/RAM-Schnappschüsse, Cache-Auffrischung
(Retention-Übersicht, Duplikat-Übersicht, Bereinigungsvorschau — siehe
[data-model.md](data-model.md)), geplante Backups, geplante Retention, die
automatische, rückwirkende Verdichtung archivierter Monate
(`_run_automatic_compaction_if_due()`, Housekeeping → Verdichten,
standardmäßig aus) sowie die automatische Bereinigung markierter
Datensätze (`_run_automatic_purge_if_due()`, Housekeeping → Speicherplatz,
standardmäßig aus) — beide höchstens einmal täglich, unter derselben
`StorageCoordinator.exclusive()`-Sperre wie die jeweilige manuelle Aktion —
sowie — nur im Demo-Modus bzw. bei einer liegengebliebenen Demo-Instanz relevant
(siehe [Benutzerhandbuch → Demo-Modus](user-guide.md#demo-modus)) —
`_refresh_demo_dir_info_if_stale()` (Belegter-Platz-Cache für den Zustand
„ungenutzt") und `_run_demo_append_if_due()` (fälligkeitsbasiertes
automatisches Ergänzen der Demo-Daten). Jeder dieser Schritte läuft einzeln
abgesichert (`_maintenance_step(name)`): scheitert einer, wird das geloggt, und
die übrigen Schritte desselben Durchlaufs laufen trotzdem — ein dauerhaft
scheiternder Schritt (z. B. die Duplikat-Übersicht) blockiert damit weder
Backup-Zeitplan noch Aufbewahrung, Verdichtung oder automatische Bereinigung.
Das Log ist dabei gedrosselt: der erste Fehler eines Schritts erscheint mit
Traceback (`event=maintenance_step_failed step=<name>`), danach höchstens
stündlich eine Kurzzeile mit Zähler
(`event=maintenance_step_still_failing`), und bei der Rückkehr zum Normalbetrieb
einmal `event=maintenance_step_recovered`. Ein Fehler außerhalb der Schritte
(`event=maintenance_scheduler_failed`) bricht die Schleife ebenfalls nicht ab.

Backup, Import, Rotation und Retention greifen wegen
`StorageCoordinator.exclusive()` nie gleichzeitig auf den Datenbestand zu —
sie warten ggf. aufeinander, nie parallel.

## Was gerade läuft

Alle zwölf Vorgänge, die spürbar dauern, melden sich seit 0.85.0 an der
Glocke in der Kopfzeile an (Abschnitt „Läuft gerade", auf jeder Seite
sichtbar). Für den Betrieb ist vor allem der Teil interessant, den niemand
ausgelöst hat: der Speicherabgleich beim Start, der Stunden-Rollup-Backfill
im Wartungsplaner und der Rollup-Neuaufbau, den ein Typwechsel aus Home
Assistant mitten im Schreibpfad auslöst. Steht die App scheinbar grundlos,
ist die Glocke die erste Stelle zum Nachsehen — mehrere dieser Vorgänge
halten die globale Wartungssperre und pausieren damit auch die Aufnahme.

Bleibt dort etwas ungewöhnlich lange stehen, sind die nächsten Stellen die
Stall-Meldungen (`system.scheduler_stalled`,
`system.storage_reconcile_stalled`, ab 5 Minuten ohne Fortschritt) und das
Log. Technischer Unterbau: [architecture.md](architecture.md).

## SQLite-Index-Wartung

Die Indexdetailseite zeigt mit `PRAGMA freelist_count` ausschließlich
vollständig freie, von SQLite im laufenden Betrieb automatisch
wiederverwendbare Seiten. „Optimierung empfohlen“ erscheint konservativ ab
50 MB Indexgröße, 10 MB reclaimbarem Speicher und 25 % freien Seiten.

Die ausschließlich manuell gestartete Optimierung führt `VACUUM` unter
`StorageCoordinator.exclusive()` und dem Index-Lock aus — für ihre Dauer
steht die gesamte Anwendung einschließlich der Aufnahme, weshalb sie sich
wie die übrigen langen Vorgänge an der Glocke anmeldet (siehe oben). Vorher müssen die
doppelte aktuelle Indexgröße plus 16 MB Sicherheitsreserve frei sein; danach
läuft `PRAGMA quick_check`. Es gibt bewusst weder einen periodischen Lauf
noch eine automatische Ausführung beim Löschen von Messwerten.

## Versionierung

**Kanonische Version:** `addon/VERSION` (SemVer, eine Zeile). Alles andere
wird daraus abgeleitet:

```bash
python3 scripts/sync_versions.py          # addon/config.yaml aktualisieren
python3 scripts/sync_versions.py --check  # Drift prüfen, Exit-Code 1 bei Abweichung
```

Die Integration (`custom_components/zeitarchiv`) versioniert sich unabhängig
über ihre eigene `manifest.json` — `sync_versions.py` prüft dort nur auf
gültiges SemVer, gleicht sie aber nicht an die App-Version an (zwei getrennte
Produkte).

`CHANGELOG.md` folgt "Keep a Changelog"-Konvention (Neu/Geändert/Behoben je
Version, neuestes oben).
