# Betrieb

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

## Retention-Durchsetzung

`storage/retention.py`, ausgeführt manuell (Vorschau + Klick) oder geplant
(täglich oder wöchentlich zur konfigurierten lokalen Uhrzeit,
`settings.retention_enforcement` + `retention_enforcement_time`; bei
wöchentlichem Modus zusätzlich `retention_enforcement_weekday`). Nächster/
letzter Lauf in `retention_jobs` protokolliert (überlebt Neustarts). Nach
einer Downtime wird höchstens **ein** verpasster Lauf nachgeholt, nie
mehrere rückwirkend.

## Wartungsplaner

`main.py:_maintenance_scheduler_loop()`, ein einzelner Daemon-Thread, alle 30
Sekunden geprüft. Bündelt: Statistik-/RAM-Schnappschüsse, Cache-Auffrischung
(Retention-Übersicht, Duplikat-Übersicht, Bereinigungsvorschau — siehe
[data-model.md](data-model.md)), geplante Backups, geplante Retention. Ein
Fehler in einem Durchlauf wird geloggt und bricht die Schleife nicht ab.

Backup, Import, Rotation und Retention greifen wegen
`StorageCoordinator.exclusive()` nie gleichzeitig auf den Datenbestand zu —
sie warten ggf. aufeinander, nie parallel.

## SQLite-Index-Wartung

Die Indexdetailseite zeigt mit `PRAGMA freelist_count` ausschließlich
vollständig freie, von SQLite im laufenden Betrieb automatisch
wiederverwendbare Seiten. „Optimierung empfohlen“ erscheint konservativ ab
50 MB Indexgröße, 10 MB reclaimbarem Speicher und 25 % freien Seiten.

Die ausschließlich manuell gestartete Optimierung führt `VACUUM` unter
`StorageCoordinator.exclusive()` und dem Index-Lock aus. Vorher müssen die
doppelte aktuelle Indexgröße plus 16 MB Sicherheitsreserve frei sein; danach
läuft `PRAGMA quick_check`. Es gibt bewusst weder einen periodischen Lauf
noch eine automatische Ausführung beim Löschen von Messwerten.

## Versionierung und Release

**Kanonische Version:** `addon/VERSION` (SemVer, eine Zeile). Alles andere
wird daraus abgeleitet:

```bash
python3 scripts/sync_versions.py          # addon/config.yaml aktualisieren
python3 scripts/sync_versions.py --check  # Drift prüfen, Exit-Code 1 bei Abweichung
```

Die Integration (`custom_components/zeitarchiv`) versioniert sich unabhängig
über ihre eigene `manifest.json` — `sync_versions.py` prüft dort nur auf
gültiges SemVer, gleicht sie aber nicht an die App-Version an (zwei getrennte
Produkte, siehe unten).

**Wichtig — zwei Zielrepositories, drei Arbeitskopien:**

| Verzeichnis | Rolle |
| --- | --- |
| `addon/` | Aktive Entwicklungskopie der App (kein eigenes Git) |
| `custom_components/zeitarchiv/` | Aktive Entwicklungskopie der Integration (kein eigenes Git) |
| `HA-Apps/zeitarchiv/` | Git-Arbeitskopie von `github.com/bertel2020/HA-Apps` (öffentliches Add-on-Repo) |
| `HA-Zeitarchiv/` | Git-Arbeitskopie von `github.com/bertel2020/HA-Zeitarchiv` (öffentliches HACS-Integrations-Repo) |

Änderungen entstehen in `addon/` bzw. `custom_components/zeitarchiv/` und
müssen **manuell** (`rsync`, siehe unten) in die jeweilige Git-Arbeitskopie
übertragen werden, bevor sie committet/gepusht werden können — es gibt
keinen automatischen Sync. `addon/CODE_REVIEW.md`, `addon/PERFORMANCE.md`
und `addon/LOGGING_KONZEPT.md` sind bewusst rein lokal und werden **nie**
mit übertragen (Setup für lokale Entwicklung ist dagegen bewusst öffentlich,
siehe [development.md](development.md)). Ebenso lokal bleiben `*.local.md`
(z. B. Konzeptentwürfe unter `docs/`) und `demo-data/` (per
`scripts/generate_demo_data.py` erzeugt, nicht Teil der Auslieferung).

```bash
rsync -av \
  --exclude='.venv' --exclude='data' --exclude='demo-data' \
  --exclude='__pycache__' --exclude='.pytest_cache' \
  --exclude='.DS_Store' --exclude='.claude' --exclude='.git' \
  --exclude='CODE_REVIEW.md' --exclude='PERFORMANCE.md' \
  --exclude='LOGGING_KONZEPT.md' --exclude='*.local.md' \
  --exclude='*.pyc' \
  addon/ HA-Apps/zeitarchiv/
```

Anschließend in `HA-Apps/` (bzw. `HA-Zeitarchiv/`) committen. Nach dem
Pushen `git status -sb` prüfen (`ahead N`) — Pushes erfolgen nie automatisch
ohne expliziten Auftrag.

`CHANGELOG.md` folgt "Keep a Changelog"-Konvention (Neu/Geändert/Behoben je
Version, neuestes oben).
