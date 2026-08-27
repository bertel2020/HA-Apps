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
[Zeitarchiv-Integration](https://github.com/bertel2020/HA-Zeitarchiv) sammelt
die gewünschten Werte; diese App speichert, verdichtet, durchsucht und
visualisiert sie.

Ausführliches, aufgabenorientiertes Benutzerhandbuch:
[docs/user-guide.md](docs/user-guide.md). Technische Dokumentation für
Entwickler: [docs/](docs/README.md).

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
  Aggregation und Darstellung. Die Entitäts-Auflösung dient der Darstellung;
  eingehende Werte werden nicht mehr über einen zeitlichen Mindestabstand
  verworfen.

## Installation

### 1. App installieren

**Über den Add-on-Store:** In Home Assistant **Einstellungen → Add-ons →
Add-on-Store → ⋮ → Repositories** öffnen, `https://github.com/bertel2020/HA-Apps`
eintragen und hinzufügen. **Zeitarchiv** erscheint danach im Store; installieren
und starten. Unterstützt werden `amd64` und `aarch64`.

**Manuell:** Alternativ den Inhalt dieses Verzeichnisses als
`/addons/zeitarchiv` auf den Home-Assistant-Host kopieren und den Store neu
laden.

Nach dem Start erscheint Zeitarchiv in der Home-Assistant-Seitenleiste. Die
vollständige Oberfläche läuft über den authentifizierten Supervisor-Ingress;
ein separates Benutzerkonto ist nicht erforderlich.

### 2. API-Token kopieren

Zeitarchiv öffnen und unter **Einstellungen → Verbindung** den automatisch
erzeugten API-Token kopieren. Der Token kann dort später neu generiert werden.

### 3. Integration verbinden

Die [Zeitarchiv-Integration](https://github.com/bertel2020/HA-Zeitarchiv)
installieren (über HACS oder manuell, siehe deren README) und in Home
Assistant über **Einstellungen → Geräte & Dienste → Integration hinzufügen →
Zeitarchiv** einrichten. Benötigt werden Host, Port `8127` und der API-Token.

### 4. Archivfilter festlegen

Auf der Integrationskachel **Konfigurieren → Archivfilter bearbeiten** öffnen
und Domains, Entitäten, Bereiche oder Geräte auswählen. Ohne passende Filter
wartet die App auf Daten, archiviert aber nichts.

## Die Oberfläche

### Dashboards

Kacheln lassen sich auf beliebig viele benannte Dashboards verteilen statt
nur auf eine Startseite. Der Menüpunkt **Dashboards** klappt eine Liste
aller vorhandenen Dashboards auf, pro Dashboard eine Kachel-Ansicht (bis zu
18 frei angeordnete Kacheln, Charts und Tabellen gemischt, Drag-and-drop,
Größen 1×1 bis 3×3, Direktlink zum Anlegen eines neuen Charts/einer neuen
Tabelle im Anheften-Menü) sowie einen Editor zum Anlegen/Umbenennen/Löschen.
Ein Dashboard lässt sich dort zusätzlich fixieren — verhindert versehentliches
Umsortieren, Größenändern oder Entfernen von Kacheln auf der Ansicht, ohne
Umbenennen/Löschen im Editor einzuschränken. Die Ein-/Ausblend-Animation der
Kachel-Charts gilt weiterhin zentral für alle Kacheln und lässt sich unter
**Einstellungen → Darstellung** deaktivieren.

### Entitäten und Verläufe

Die Entitätenliste ist durchsuchbar, filterbar und konfigurierbar. Jede
Entität besitzt eine eigene Verlaufsansicht mit:

- Linie oder Balken, bei Schalter-Entitäten zusätzlich als Zeitstrahl mit den
  AN-Intervallen;
- geglättete Linien oder Balken;
- Navigation von Stunde bis Dekade;
- laufendem oder rollierendem Zeitfenster;
- Vergleich mit Vorperiode oder Vorjahr;
- individuell einstellbarer Auflösung, Aufbewahrung und Rundung;
- optionalem Wertänderungsfilter, der gerundet gleiche Folgewerte überspringt
  und mindestens alle sechs Stunden ein Lebenszeichen behält.

Ein laufender, nicht-kontinuierlicher Zeitraum zeigt dabei immer bis zur
vollen Kalendergrenze (z. B. bis Sonntag bei „Woche"), auch bevor für die
restliche Periode bereits Daten vorliegen. Über „Als Chart speichern" im
Optionen-Menü lässt sich die aktuelle Ansicht direkt als eigenständiges,
mehrere Entitäten fähiges Chart ablegen.

### Charts und Tabellen

Eigene Charts können mehrere Entitäten überlagern. Unterschiedliche Einheiten
erhalten getrennte Y-Achsen, Linien werden geglättet. Bei „Automatisch"
zeigt ein kleiner Hinweis direkt an, welche Auflösung das gerade tatsächlich
bedeutet (z. B. „≈ 1 Stunde"). Minimum, Maximum und Durchschnitt des
aktuellen Zeitraums stehen wahlweise direkt in der Legende, zusammen mit der
Ein-/Ausblendung einzelner Entitäten. Der aktivierbare Vergleich benennt
Vorperiode und Vorjahresperiode passend zum Zeitraum, beispielsweise
„Vortag“ und „Vorjahrestag“, und zeigt die gewählte Option direkt im Button.
Seltener geänderte Einstellungen (Auflösung, Punkte, Kontinuierlich,
Rohwerte, dynamische Y-Achse, Legenden-Statistik) sitzen gesammelt in einem
Optionen-Menü. In einem Chart lassen sich mehrere Entitäten sowohl per
Ziehen als auch über Pfeil-Buttons neu anordnen — das bestimmt Legenden-,
Statistik- und Farbreihenfolge.

Vergleichstabellen unterstützen einzelne Entitäten, Summengruppen, Formeln,
Trennzeilen und frei gewählte Zeitspalten. Jede Entitäts-/Gruppen-Zeile
wählt ihre Aggregation selbst (Automatisch, Ø Durchschnitt, Min, Max, Σ
Summe), jede Spalte ihre Nachkommastellen (Automatisch oder 0–3). Eine
Trennzeile zieht eine durchgehende Linie über die gesamte Tabellenbreite,
rein optisch zur Gliederung, ohne eigene Daten. Spalten und Zeilen lassen
sich per Ziehen oder über Pfeil-Buttons neu anordnen; beim Umsortieren von
Zeilen werden die Buchstaben-Referenzen (A/B/C …) in Formel-Zeilen
automatisch korrigiert, sodass eine Formel weiterhin dieselbe Zeile
referenziert wie vor dem Verschieben. Eine Formel-Zeile übernimmt ohne
eigene Angabe die Einheit der ersten referenzierten Zeile. Die Darstellung
(Zebra-Streifen, Kopfzeile/
erste Spalte hervorheben, Beschriftung fett, Rahmen horizontal/Gitter/ohne,
Dichte komfortabel/kompakt) ist rein optisch und wirkt sich nie auf die
berechneten Werte aus.

### Bereinigung

Der Bearbeitungsbereich einer Entität (Tabs **Bereinigen**/**Korrigieren**/
**Hinzufügen**) erkennt konfigurierbare Ausreißer, Lücken, doppelte
Zeitstempel und gerundet gleiche Folgewerte; jeder Fund zeigt eine
Begründung mit Vorwert/Zeitstempel bzw. den betroffenen Werten. Wiederholungen
lassen sich mit derselben Sechs-Stunden-Lebenszeichenregel auch nachträglich
verdichten. Bei steigenden Zählern (`total_increasing`) werden niedrigere
Folgewerte als mögliche Zähler-Resets protokolliert und unter
„Zählerrückgänge“ markiert; sie bleiben standardmäßig gespeichert. Die
Rohwert-Tabelle zeigt die Einheit direkt neben jedem Wert.
Die Kopfzeile zeigt Datensatzanzahl im gewählten Zeitraum, sichtbaren
Gesamtbestand sowie Ausreißer/Lücken/Duplikate/Wiederholungen über die
komplette Historie der Entität.
Markierungen sind zunächst weich und können rückgängig gemacht
werden. Erst **Einstellungen → Speicherplatz** entfernt markierte Werte
physisch und berechnet betroffene Rollups neu.

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
- **CSV-Datei:** Trennzeichen, Zeit-, Wert- und Zielspalte frei zuordnen und
  das Ergebnis vor dem Import prüfen.
- **Reports:** Im dritten Import-Reiter bleibt jeder tatsächlich ausgeführte Import mit Quelle,
  Zuordnung, Laufzeit, importierten und übersprungenen Datensätzen sowie
  möglichen Fehlern nachvollziehbar. Die Liste lässt sich nach Quelle und
  Status filtern (wirkt sofort bei Auswahl) und nach jeder Spalte sortieren;
  ein Klick auf eine Zeile öffnet die Detailansicht mit JSON-Download.
  Reports sind seitenweise darstellbar und können gesammelt gelöscht werden.
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

Unter **System → Backup / Restore** lassen sich vollständige, portable
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
| Darstellung | Farbschema, Hell-/Dunkelmodus, Schriftgröße und Dashboard-Kachel-Animation |
| Archivierung | Standards für Auflösung und Aufbewahrung neuer Entitäten |
| Rotation | Ausstehende Monatsrotationen prüfen und ausführen |
| Speicherplatz | Speicherindex prüfen/reparieren sowie Löschmarkierungen endgültig anwenden |
| Aufbewahrung | Vorschau, täglicher Zeitplan und Laufhistorie |
| Verbindung | API-Token und aktueller Schreibstatus |
| Protokollierung | Loglevel und HTTP-Protokollierung |
| Diagnose | Schreibvorgang aufzeichnen, Entität verfolgen, Diagnosebericht, Prozess-Laufzeit |
| Über Zeitarchiv | Version, Zeitzone und Datenverzeichnis |

Die einzige Supervisor-Option ist `timezone`; erwartet wird eine IANA-Zeitzone
wie `Europe/Berlin`. Alle übrigen Einstellungen liegen im Zeitarchiv-Index.

Beim Start sowie nach Datenimporten gleicht Zeitarchiv die abgeleiteten
Indexkennzahlen automatisch mit Parquet-Archiv und Hot Buffer ab. Eine
zusätzliche Vorschau unter **Speicherplatz → Indexkonsistenz** zeigt mögliche
Abweichungen vor einer manuellen Reparatur; Rohwerte und Rollups werden dabei
nicht verändert.

## Bekannte Grenzen

- Die App ist für ein einzelnes lokales Home-Assistant-System und einen
  gemeinsamen API-Token ausgelegt; Mehrbenutzer- oder Mandantentrennung gibt
  es derzeit nicht.
- Die Ingress-Seiten sind nicht als eigenständige Lovelace-Karten oder
  öffentliche Chart-URLs gedacht.
- Manuelle Lösch- und Retention-Aktionen können endgültig sein. Vor größeren
  Eingriffen sollte ein geprüftes Backup erstellt werden.

## Lizenz

Dieses Projekt steht unter der [Apache License 2.0](LICENSE).
Copyright 2026 Roberto / bertel2020.

Die verwendeten Drittanbieter-Komponenten und deren Lizenzen sind in
[THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md) aufgeführt.

---

<p align="center">
  <a href="https://github.com/bertel2020/HA-Zeitarchiv">Integration einrichten</a>
  ·
  <a href="https://github.com/bertel2020/HA-Apps/blob/main/zeitarchiv/CHANGELOG.md">Changelog</a>
  ·
  <a href="https://github.com/bertel2020/HA-Apps/blob/main/zeitarchiv/DOCS.md">App-Store-Dokumentation</a>
</p>
