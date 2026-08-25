<p align="center">
  <img src="https://raw.githubusercontent.com/bertel2020/HA-Apps/main/zeitarchiv/logo.png" alt="Zeitarchiv" width="160">
</p>

<h1 align="center">Zeitarchiv App</h1>

<p align="center">
  Langfristige, kompakte Zeitreihen für Home Assistant.<br>
  <sub>PARQUET + ZSTD · INGRESS · CHARTS · TABELLEN · IMPORT · BACKUP</sub>
</p>

Zeitarchiv bewahrt ausgewählte Zustandsänderungen unabhängig von der
Aufbewahrungsdauer des Home-Assistant-Recorders auf. Die
[Zeitarchiv-Integration](../custom_components/zeitarchiv/README.md) sammelt
die gewünschten Werte; diese App speichert, verdichtet, durchsucht und
visualisiert sie.

## Auf einen Blick

| | |
| --- | --- |
| **Kompaktes Archiv** | Abgeschlossene Monate als Parquet mit zstd-Kompression |
| **Schnelle Langzeitansichten** | Vorbereitete Rollups von Stunde bis Jahr |
| **Eigene Dashboards** | Frei kombinierbare Charts und Vergleichstabellen |
| **Datenpflege** | Ausreißer, Lücken und Duplikate untersuchen und bereinigen |
| **Datenübernahme** | Symcon-ZIPs samt Einheitenprüfung und frei zuordenbare CSV-Dateien importieren |
| **Sicherung** | Prüfbare ZIP-Backups mit Wiederherstellung und Zeitplan |

## Zusammenspiel

```text
Home-Assistant-Entitäten
          │ state_changed
          ▼
Zeitarchiv-Integration
  Filter · Queue · Batch · Retry
          │ POST /api/write + Bearer-Token
          ▼
Zeitarchiv App
  Hot Buffer ──► Monatsarchiv ──► Rollups
          │
          └────► Ingress-Oberfläche
                 Charts · Tabellen · Pflege · Export
```

Die Verantwortlichkeiten bleiben bewusst getrennt:

- Die **Integration** entscheidet, welche Home-Assistant-Entitäten gesendet
  werden, und hält Übertragungsfehler von Home Assistant fern.
- Die **App** entscheidet über Auflösung, Aufbewahrung, Speicherung,
  Aggregation und Darstellung.

## Installation

### 1. App installieren

Für eine lokale Installation den Inhalt dieses Verzeichnisses als
`/addons/zeitarchiv` auf den Home-Assistant-Host kopieren. Danach den App-Store
neu laden, **Zeitarchiv** installieren und starten. Unterstützt werden
`amd64` und `aarch64`.

Nach dem Start erscheint Zeitarchiv in der Home-Assistant-Seitenleiste. Die
vollständige Oberfläche läuft über den authentifizierten Supervisor-Ingress;
ein separates Benutzerkonto ist nicht erforderlich.

### 2. API-Token kopieren

Zeitarchiv öffnen und unter **Einstellungen → Verbindung** den automatisch
erzeugten API-Token kopieren. Der Token kann dort später neu generiert werden.

### 3. Integration verbinden

Die [Custom Integration](../custom_components/zeitarchiv/README.md)
installieren und in Home Assistant über **Einstellungen → Geräte & Dienste →
Integration hinzufügen → Zeitarchiv** einrichten. Benötigt werden Host, Port
`8127` und der API-Token.

### 4. Archivfilter festlegen

Auf der Integrationskachel **Konfigurieren → Archivfilter bearbeiten** öffnen
und Domains, Entitäten, Bereiche oder Geräte auswählen. Ohne passende Filter
wartet die App auf Daten, archiviert aber nichts.

## Die Oberfläche

### Dashboard

Die Startseite zeigt Archivkennzahlen und bis zu 18 frei angeordnete Kacheln.
Charts und Tabellen lassen sich mischen, per Drag-and-drop sortieren und in
Größen von 1×1 bis 3×3 darstellen.

### Entitäten und Verläufe

Die Entitätenliste ist durchsuchbar, filterbar und konfigurierbar. Jede
Entität besitzt eine eigene Verlaufsansicht mit:

- Linie oder Balken;
- bei Linien den letzten Messwert bis zum nächsten Datenpunkt fortführen;
- Navigation von Stunde bis Dekade;
- laufendem oder rollierendem Zeitfenster;
- Vergleich mit Vorperiode oder Vorjahr;
- individuell einstellbarer Auflösung, Aufbewahrung und Rundung.

### Charts und Tabellen

Eigene Charts können mehrere Entitäten überlagern. Unterschiedliche Einheiten
erhalten getrennte Y-Achsen. Vergleichstabellen unterstützen einzelne
Entitäten, Summengruppen, Formeln, Trennzeilen und frei gewählte Zeitspalten.

### Bereinigung

Der Bearbeitungsbereich einer Entität erkennt konfigurierbare Ausreißer,
Lücken und doppelte Zeitstempel. Markierungen sind zunächst weich und können
rückgängig gemacht werden. Erst **Einstellungen → Speicherplatz** entfernt
markierte Werte physisch und berechnet betroffene Rollups neu.

Zusätzlich stehen zwei bewusst getrennte, endgültige Aktionen bereit:

- **Alle Werte löschen** in der Entitätskonfiguration entfernt Hot Buffer,
  Monatsarchive und Rollups, behält aber die individuelle Entitätskonfiguration.
- **Entität entfernen** in der Entitätskonfiguration löscht Werte und Konfiguration.
  Sendet die HA-Integration die Entity-ID weiter, wird sie beim nächsten Wert
  automatisch wieder mit den aktuellen Standards angelegt.

Beide Aktionen verlangen vor der Ausführung eine eindeutige Bestätigung.

### Statistik

Die Statistik zeigt Entitäten, Datensätze, Speicherbedarf, Wachstum sowie
Aufschlüsselungen nach Typ, Auflösung und Aufbewahrung. Ein interner Planer
erfasst unabhängig von Seitenaufrufen höchstens stündlich einen realen
Bestandsschnappschuss.

### Import und Export

- **Symcon:** ZIP des `db`-Ordners hochladen, optional eine `settings.json`
  für Namen und Einheiten ergänzen, Variablen prüfen und HA-Entitäten
  zuordnen. Weichen Quell- und Zieleinheit voneinander ab, erscheint ein
  Hinweis und ein Umrechnungsfaktor kann angegeben werden, etwa `1000` für
  `klx` nach `lx`.
- **Eigene CSV:** Trennzeichen, Zeit-, Wert- und Zielspalte frei zuordnen und
  das Ergebnis vor dem Import prüfen.
- **Reports:** Im dritten Import-Reiter bleibt jeder tatsächlich ausgeführte Import mit Quelle,
  Zuordnung, Laufzeit, importierten und übersprungenen Datensätzen sowie
  möglichen Fehlern nachvollziehbar und kann als JSON heruntergeladen werden.
- **CSV-Export:** Die vollständige Rohdatenhistorie einer Entität bis zum
  Exportlimit herunterladen.

Importe ergänzen fehlende Zeitstempel im laufenden Hot Buffer. Bereits
vorhandene Messpunkte derselben Entität und desselben Zeitstempels werden
auch bei abweichender Event-ID übersprungen; bestehende Monatsarchive werden
dabei nicht überschrieben. Dieselbe Zeitstempelprüfung schützt auch die
laufende Datenübernahme vor unmittelbar entstehenden Duplikaten.

## Speicherung und Aufbewahrung

```text
laufender Monat      abgeschlossene Monate        Langzeitabfragen
Hot Buffer (CSV)  ─► Parquet + zstd            ─► vorberechnete Rollups
```

Neu erkannte Entitäten übernehmen die globalen Standards unter
**Einstellungen → Archivierung**. Auf der Konfigurationsseite einer Entität
lassen sich diese Werte individuell überschreiben.

Die automatische Aufbewahrung ist standardmäßig deaktiviert. Wird sie
aktiviert, läuft sie täglich zur gewählten lokalen Uhrzeit. Vor jedem Lauf ist
eine Vorschau verfügbar; Werte mit der Einstellung **Unbegrenzt** bleiben
unangetastet.

Eine Hot-Datei wird normalerweise beim ersten Wert eines neuen Monats
rotiert. Entitäten, die nicht mehr senden, können unter **Einstellungen →
Rotation** manuell nachgezogen werden.

## Backup und Wiederherstellung

Unter **Einstellungen → Sicherung** lassen sich vollständige, portable
ZIP-Backups erstellen, planen, prüfen, herunterladen und wiederherstellen.
Enthalten sind Index, Hot Buffer, Monatsarchive und Rollups.

Zeitarchiv prüft vor einer Wiederherstellung unter anderem:

- Manifest und SHA-256-Prüfsummen;
- ZIP-Struktur und Pfade;
- entpackte Größe und Kompressionsverhältnis;
- Integrität des SQLite-Indexes.

Die Veröffentlichung eines Backups erfolgt atomar. Eine Wiederherstellung ist
vorbereitet und rollback-fähig. Supervisor-Backups stoppen die App als Cold
Backup; separat erzeugte portable ZIPs werden nicht redundant eingebettet.

## Sicherheit und Netzwerk

| Zugang | Erreichbarer Umfang |
| --- | --- |
| Supervisor-Ingress, intern Port `8099` | Vollständige Oberfläche und Verwaltung |
| Veröffentlichter Port `8127` | Nur `GET /api/health` und `POST /api/write` |

Beide API-Endpunkte auf Port `8127` benötigen einen Bearer-Token. Oberfläche,
Abfragen, Exporte, Backups, Importe und Verwaltungsrouten antworten dort mit
HTTP 404. Importpfade werden normalisiert, Archive auf Zip-Bomb-Muster geprüft
und dynamische Inhalte mit restriktiven Sicherheitsheadern ausgeliefert.

### Ressourcenlimits

| Operation | Grenze |
| --- | ---: |
| Events je Schreibbatch | 1.000 |
| Entitäten je Multi-Abfrage | 25 |
| Punkte je Rohwertabfrage | 100.000 |
| Zeilen je CSV-Export | 5.000.000 |
| Importzeilen je Entität | 10.000.000 |
| ZIP-Upload | 2 GiB |
| CSV-Upload | 256 MiB |
| `settings.json` | 16 MiB |
| Entpackte ZIP-Daten | 5 GiB |

## Einstellungen

| Bereich | Zweck |
| --- | --- |
| Darstellung | Farbschema, Hell-/Dunkelmodus und Schriftgröße |
| Archivierung | Standards für Auflösung und Aufbewahrung neuer Entitäten |
| Rotation | Ausstehende Monatsrotationen prüfen und ausführen |
| Speicherplatz | Speicherindex prüfen/reparieren sowie Löschmarkierungen endgültig anwenden |
| Aufbewahrung | Vorschau, täglicher Zeitplan und Laufhistorie |
| Sicherung | Portable Backups, Zeitplan, Prüfung und Restore |
| Verbindung | API-Token und aktueller Schreibstatus |
| Protokollierung | Loglevel und HTTP-Protokollierung |
| Über Zeitarchiv | Version, Zeitzone und Datenverzeichnis |

Die einzige Supervisor-Option ist `timezone`; erwartet wird eine IANA-Zeitzone
wie `Europe/Berlin`. Alle übrigen Einstellungen liegen im Zeitarchiv-Index.

Beim Start sowie nach Datenimporten gleicht Zeitarchiv die abgeleiteten
Indexkennzahlen automatisch mit Parquet-Archiv und Hot Buffer ab. Eine
zusätzliche Vorschau unter **Speicherplatz → Indexkonsistenz** zeigt mögliche
Abweichungen vor einer manuellen Reparatur; Rohwerte und Rollups werden dabei
nicht verändert.

## Lokal entwickeln

Im Verzeichnis `addon/`:

```bash
docker compose -f docker-compose.dev.yml up --build
```

Die Entwicklungsumgebung bindet Port `8127` ausschließlich an Loopback und
verwendet den Token `devtoken`. Ein Testevent lässt sich so senden:

```bash
curl -X POST http://127.0.0.1:8127/api/write \
  -H "Authorization: Bearer devtoken" \
  -H "Content-Type: application/json" \
  -d '{"events":[{"entity_id":"sensor.test","domain":"sensor","ts":1755000000,"value":21.4,"state_class":"measurement","unit":"°C"}]}'
```

Alternativ ohne Docker:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
ZEITARCHIV_DATA_DIR=/tmp/zeitarchiv-data \
ZEITARCHIV_API_TOKEN=devtoken \
  .venv/bin/uvicorn app.main:app --port 8127
```

Die vollständige Testsuite wird aus dem Repository-Stamm gestartet:

```bash
python3 -m pytest -q
```

Die kanonische Produktversion steht in `addon/VERSION`. Synchronisation und
Driftprüfung:

```bash
python3 scripts/sync_versions.py
python3 scripts/sync_versions.py --check
```

## Bekannte Grenzen

- Die App ist für ein einzelnes lokales Home-Assistant-System und einen
  gemeinsamen API-Token ausgelegt; Mehrbenutzer- oder Mandantentrennung gibt
  es derzeit nicht.
- Die Ingress-Seiten sind nicht als eigenständige Lovelace-Karten oder
  öffentliche Chart-URLs gedacht.
- Manuelle Lösch- und Retention-Aktionen können endgültig sein. Vor größeren
  Eingriffen sollte ein geprüftes Backup erstellt werden.

---

<p align="center">
  <a href="../custom_components/zeitarchiv/README.md">Integration einrichten</a>
  ·
  <a href="https://github.com/bertel2020/HA-Apps/blob/main/zeitarchiv/CHANGELOG.md">Changelog</a>
  ·
  <a href="https://github.com/bertel2020/HA-Apps/blob/main/zeitarchiv/DOCS.md">App-Store-Dokumentation</a>
</p>
