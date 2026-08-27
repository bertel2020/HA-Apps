# Benutzerhandbuch

Dieses Dokument ist die ausführliche Anleitung für Nutzer der App — Schritt
für Schritt, aufgabenorientiert. Für einen kurzen Überblick (Installation,
Funktionsliste) siehe die [App-README](../README.md); technische Interna für
Entwickler stehen in den übrigen Dokumenten dieses Ordners (siehe
[README.md](README.md)).

## Erste Schritte

1. **App installieren** — über den Add-on-Store (Repository
   `https://github.com/bertel2020/HA-Apps` hinzufügen) oder manuell. Details:
   [App-README → Installation](../README.md#installation).
2. **API-Token kopieren** — Zeitarchiv öffnen, **Einstellungen → Verbindung**,
   Token kopieren.
3. **Integration installieren** — [github.com/bertel2020/HA-Zeitarchiv](https://github.com/bertel2020/HA-Zeitarchiv),
   über HACS oder manuell. In Home Assistant unter **Einstellungen → Geräte
   & Dienste → Integration hinzufügen → Zeitarchiv** Host (`localhost`),
   Port (`8127`) und Token eintragen.
4. **Archivfilter festlegen** — auf der Integrationskachel **Konfigurieren →
   Archivfilter bearbeiten**: Domains, einzelne Entitäten, Bereiche oder
   Geräte auswählen. Ohne Filter kommen keine Daten an.
5. Nach dem ersten empfangenen Wert erscheint die Entität automatisch in
   **Entitäten** — mit den globalen Standardwerten aus **Einstellungen →
   Archivierung**.

## Die Übersichtsseite

Die Startseite zeigt eine Kennzahlenübersicht (Anzahl Entitäten, Datensätze,
Speicherbedarf) und darunter das **Standard-Dashboard** — dieselbe
Kachel-Ansicht wie unter **Dashboards**, nur fest der Startseite zugeordnet.

## Dashboards

- **Dashboards**-Menüpunkt (Hauptnavigation) klappt eine Liste aller
  vorhandenen Dashboards auf. Von dort: neues Dashboard anlegen, ein
  bestehendes öffnen, umbenennen oder löschen.
- Jedes Dashboard zeigt bis zu 18 Kacheln (Charts und Vergleichstabellen
  gemischt) in frei wählbarer Größe (1×1 bis 3×3). Per Drag-and-drop
  anordnen; über das Kachelmenü (⋮) Größe ändern oder entfernen.
- **Kachel hinzufügen:** die "+"-Kachel öffnet ein Menü mit direktem Link
  "**+ Neuer Chart**"/"**+ Neue Tabelle**" (führt sofort in den jeweiligen
  Editor) sowie einer Liste bereits gespeicherter, noch nicht angehefteter
  Charts/Tabellen zum Anklicken.
- **Dashboard fixieren** (Editor, Schalter "Fixiert"): sperrt Umsortieren,
  Größenändern und Entfernen von Kacheln auf der Ansicht selbst — schützt
  vor versehentlichem Verschieben auf einem z. B. dauerhaft angezeigten
  Wandtablet. Umbenennen und Löschen des Dashboards bleiben im Editor
  weiterhin möglich. Löschen eines Dashboards entfernt nur die
  Kachel-Anordnung — die zugrunde liegenden Charts/Tabellen bleiben erhalten
  und lassen sich anderswo neu anheften.

## Entitäten und Verläufe

**Entitäten** listet alle bekannten Entitäten, durchsuchbar und filterbar.
Ein Klick öffnet die **Verlaufsansicht** einer einzelnen Entität:

- Zeitraum-Navigation von Stunde bis Dekade, vor/zurück blätterbar, wahlweise
  laufendes ("bis heute") oder rollierendes Fenster ("letzte 24 h").
- Linie oder Balken; bei Schaltern zusätzlich ein Zeitstrahl mit AN/AUS-
  Intervallen.
- **Vergleich** (Optionen-Menü): Vorperiode oder Vorjahr, mit passender
  Beschriftung ("Vortag", "Vorjahrestag" …).
- **Min/Max/Durchschnitt** wahlweise direkt in der Legende sichtbar.
- **Als Chart speichern** (Optionen-Menü) legt die aktuelle Ansicht als
  eigenständiges, ggf. mehrere Entitäten umfassendes Chart ab — von dort aus
  an ein Dashboard anheftbar.

### Entität konfigurieren

Über das Zahnrad-Symbol einer Entität:

| Feld | Bedeutung |
| --- | --- |
| Auflösung | Mindestabstand zwischen zwei gespeicherten Werten (z. B. "alle 5 Minuten") |
| Aufbewahrung | Wie lange Werte behalten werden, bevor eine aktivierte automatische Löschung greift (**Unbegrenzt** möglich) |
| Nachkommastellen | Automatisch oder feste Anzahl (0–3) für die Anzeige |
| Wertänderungsfilter | Überspringt gerundet gleiche Folgewerte, behält aber mindestens alle 6 Stunden ein Lebenszeichen |
| Lücken-Erkennung | Schwellwert in Minuten für die Bereinigung — "Aus" deaktiviert die Markierung |
| Ausreißer-Erkennung | Schwellwert in Prozent (Sprung gegenüber dem Vorwert) — "Aus" deaktiviert die Markierung |
| Anzeigemodus | Bei Schaltern: Rohwert (AN/AUS) oder Zeit (Einschaltdauer) |

Am Seitenende: **Alle Werte löschen** (entfernt alle Daten, behält die
Konfiguration) und **Entität entfernen** (löscht auch die Konfiguration —
sendet Home Assistant die Entität weiter, wird sie beim nächsten Wert mit
den aktuellen Standards neu angelegt). Beide verlangen eine explizite
Bestätigung und sind nicht rückgängig zu machen.

## Bereinigung

Von der Verlaufsansicht über "Bereinigen" erreichbar, drei Reiter:

1. **Bereinigen** — erkannte Ausreißer, Lücken, Duplikate und gerundet
   gleiche Wiederholungen als Liste, je mit Begründung (z. B. "3 Std. 50 Min.
   seit vorherigem Wert 21,2 °C um 08:10"). Auswählen und löschen — das ist
   zunächst ein **Soft-Delete**: die Werte verschwinden aus jeder Anzeige,
   sind aber über "Rückgängig (letzte Löschung)" wiederherstellbar, solange
   nicht endgültig bereinigt wurde (siehe unten).
2. **Korrigieren** — einzelne Werte direkt bearbeiten (Klick auf die
   Wert-Zelle) statt zu löschen, z. B. um einen erkennbaren Sensor-Ausreißer
   auf einen plausiblen Wert zu setzen statt die Lücke offen zu lassen.
3. **Hinzufügen** — einen fehlenden Messpunkt manuell mit Zeitstempel und
   Wert ergänzen (deutsches Zahlenformat, Komma als Dezimaltrennzeichen).

Die Kopfzeile zeigt sowohl die Datensatzanzahl im gewählten Zeitraum als auch
den sichtbaren Gesamtbestand samt Ausreißern/Lücken/Duplikaten/
Wiederholungen über die **komplette** Historie der Entität.

**Endgültig entfernen:** Soft-gelöschte Werte bleiben Datenträgerplatz, bis
sie unter **Einstellungen → Speicherplatz** physisch bereinigt werden — dort
zeigt eine Vorschau vorab, wie viele Zeilen tatsächlich entfernbar sind
(inklusive Aufschlüsselung nach laufendem Monat/Archiv). Dieser Schritt ist
endgültig.

## Charts

Eigener Editor (**Charts** → neues Chart oder Bearbeiten eines bestehenden):
mehrere Entitäten überlagern, automatische getrennte Y-Achsen bei
unterschiedlichen Einheiten, wählbare Auflösung (inkl. "Automatisch" mit
Anzeige der tatsächlich verwendeten Auflösung), Punkte an/aus, Rohwerte,
dynamische Y-Achse, Legenden-Statistik. Mehrere Entitäten lassen sich per
Ziehen oder Pfeil-Buttons neu anordnen — das bestimmt Legenden-, Statistik-
und Farbreihenfolge.

## Vergleichstabellen

Eigener Editor (**Tabellen** → neue Tabelle): **Zeilen** sind Größen
(Entität, Gruppe mehrerer Entitäten, Formel, oder eine rein optische
Trennlinie), **Spalten** sind Zeiträume (frei benannt, z. B. "Heute", "Aug
Vorjahr", "2026", jeweils mit Zeitraum-Typ und Versatz).

- **Aggregation je Zeile:** Automatisch (Zähler → Summe, sonst Durchschnitt),
  Ø Durchschnitt, Min, Max oder Σ Summe — Min/Max nutzen die echten
  Extremwerte der Rohdaten, nicht den Durchschnitt der kleinsten Zeitscheibe.
- **Nachkommastellen je Spalte:** Automatisch oder fest 0–3.
- **Formeln** referenzieren andere Zeilen über ihr Buchstaben-Kürzel (A, B, C
  …), z. B. `A / B * 100`; nur Zeilen *oberhalb* sind referenzierbar. Beim
  Umsortieren von Zeilen werden Formel-Referenzen automatisch mitkorrigiert.
- **Darstellung** (Zebra-Streifen, Kopfzeile/erste Spalte hervorheben,
  Beschriftung fett, Rahmen horizontal/Gitter/ohne, Dichte
  komfortabel/kompakt) ist rein optisch, ändert nie berechnete Werte.

Gespeicherte Tabellen zeigen beim Ansehen immer aktuelle Werte — kein
eingefrorener Schnappschuss.

## Statistik

Entitätenzahl, Datensätze, Speicherbedarf, Wachstum über die Zeit sowie
Aufschlüsselungen nach Typ, Auflösung und Aufbewahrung. Die
Speicherplatz-Aufschlüsselung verlinkt direkt zu Import-Reports und Backups,
wo diese ebenfalls Platz belegen.

## Import und Export

- **Symcon:** ZIP des `db`-Ordners hochladen, optional `settings.json` für
  Namen/Einheiten, Variablen prüfen und Home-Assistant-Entitäten zuordnen.
  Bei abweichenden Einheiten (z. B. `klx` → `lx`) einen Umrechnungsfaktor
  angeben.
- **CSV:** Trennzeichen sowie Zeit-, Wert- und Zielspalte frei zuordnen,
  Ergebnis vor dem eigentlichen Import prüfen.
- **Home Assistant:** bestehende Recorder-Daten direkt aus der laufenden
  Home-Assistant-Instanz übernehmen, ohne Symcon oder eine hochgeladene
  Datei. Zur Auswahl stehen nur Entitäten, die bereits in Zeitarchiv bekannt
  sind (also von der Home-Assistant-Integration konfiguriert wurden und
  mindestens einen Live-Wert übertragen haben). Zwei Quellen zur Wahl:
  - **Rohhistorie:** Einzelmesswerte über die Home-Assistant-REST-API — Home
    Assistant hält sie standardmäßig aber nur einige Tage vor.
  - **Langzeitstatistik:** von Home Assistant per Voreinstellung dauerhaft
    aufbewahrte Stunden-/Tagesaggregate (Mittelwert bzw. fortlaufende Summe,
    je nachdem was die Entität führt) über die Home-Assistant-WebSocket-API
    — deckt damit auch deutlich ältere Zeiträume ab, dafür nur als Aggregat
    statt als Einzelmesswert. Steht nur für Entitäten mit Home-Assistant-
    `state_class` zur Verfügung (i. d. R. `sensor.*`), erkennbar an der
    Markierung „Nicht unterstützt“ in der Spalte „Art“.

  Zeitraum wählen (Voreinstellung/verfügbare Voreinstellungen unterscheiden
  sich je nach Quelle — bei Langzeitstatistik z. B. auch „Letztes Jahr“),
  optional „Verfügbarkeit prüfen“ für eine Vorschau, welche Entitäten in
  Home Assistant tatsächlich Daten der gewählten Quelle haben und für
  welchen Zeitraum. Das Ergebnis bleibt je Quelle/Auflösung erhalten — auch
  nach einem Seitenwechsel oder einem Wechsel zwischen Rohhistorie und
  Langzeitstatistik (bis zum nächsten Neustart des Add-ons) —, der
  Spaltenkopf „Verfügbar“ zeigt dazu den Zeitpunkt der letzten Prüfung; ab
  15 Minuten erscheint ein Hinweis, dass der Stand veraltet sein könnte.
  Benötigt die Add-on-Berechtigung `homeassistant_api` sowie eine
  Home-Assistant-Installation mit Supervisor (nicht bei Home Assistant
  Container).
- **Reports** (vierter Reiter): jeder tatsächlich ausgeführte Import bleibt
  mit Quelle, Zuordnung, importierten/übersprungenen Datensätzen und
  eventuellen Fehlern nachvollziehbar, filterbar nach Quelle/Status,
  sortierbar, mit JSON-Download je Eintrag. Reine Vorschauen erzeugen keinen
  Report.
- **CSV-Export:** vollständige Rohdatenhistorie einer Entität herunterladen.

Importe füllen nur **fehlende** Zeitstempel im laufenden Hot Buffer auf —
bereits vorhandene Messpunkte werden übersprungen, bestehende Monatsarchive
nie überschrieben. Ein erneuter Symcon-Upload derselben Quelle dupliziert
also nichts.

## Backup / Restore

Eigener Menüpunkt **System → Backup / Restore** (nicht unter Einstellungen):

- **Backup erstellen:** kompletter Datenbestand als ZIP, herunterladbar.
  Zusätzlich zu, nicht statt der automatischen Home-Assistant-Snapshots.
- **Zeitplan:** automatisch nach Zeitplan (Intervall, Uhrzeit, ggf.
  Wochentag), mit automatischer Aufräumung älterer Backups nach Anzahl
  und/oder Alter.
- **Prüfen:** Prüfsummen-Check eines vorhandenen Backups, ohne es
  anzuwenden.
- **Wiederherstellen:** ersetzt den aktuellen Datenbestand — der bisherige
  Stand wird vor dem Überschreiben in ein Rollback-Verzeichnis verschoben,
  nicht gelöscht (siehe [operations.md](operations.md) für die technischen
  Garantien).

## Einstellungen im Detail

| Bereich | Enthält |
| --- | --- |
| **Darstellung** | Farbschema (Zeitarchiv/Home Assistant/Modern), Hell/Dunkel/Automatisch, Schriftgröße, Dashboard-Kachel-Ein-/Ausblendanimation |
| **Archivierung** | Standard-Auflösung/-Aufbewahrung für neu erkannte Entitäten (wirkt nie rückwirkend auf bestehende) |
| **Rotation** | Zeigt Entitäten mit noch nicht archiviertem Vormonat (passiert normalerweise automatisch beim nächsten Wert) — manuell nachziehbar |
| **Speicherplatz** | Indexkonsistenz prüfen/reparieren; markierte Datensätze endgültig aus Hot Buffer und Archiv entfernen (siehe "Bereinigung" oben) |
| **Aufbewahrung** | Vorschau fälliger Löschungen, Zeitplan für automatische Durchsetzung, Lauf-Historie |
| **Verbindung** | API-Token anzeigen/neu erzeugen, letzter empfangener Wert, Schreibzugriffe/Auth-Fehler seit Start |
| **Protokollierung** | Anwendungs-Loglevel, HTTP-Zugriffsprotokollierung |
| **Diagnose** | Nächsten Schreibvorgang einmalig vollständig aufzeichnen; eine Entität 15 Minuten lang verfolgen; Diagnosebericht herunterladen; Prozess-Start/-Laufzeit |
| **Über Zeitarchiv** | Version, Zeitzone, Datenverzeichnis, Links zu Dokumentation/Changelog/Fehlermeldung |

## Typische Aufgaben

**"Ein Sensor sendet unplausible Ausreißer."** → Entität öffnen →
Zahnrad-Symbol → Ausreißer-Erkennung auf einen passenden Prozentsatz
einstellen → zurück zur Verlaufsansicht → **Bereinigen** → erkannte
Ausreißer prüfen und löschen (Soft-Delete, rückgängig machbar) →
**Einstellungen → Speicherplatz**, wenn der Platz tatsächlich freigegeben
werden soll.

**"Ich will Innen- und Außentemperatur über die letzten 12 Monate
vergleichen."** → **Tabellen** → neue Tabelle → 12 Spalten (Zeitraum-Typ
"Monat", Versatz 0 bis −11) → zwei Zeilen (je eine Entität) → optional eine
Formel-Zeile für die Differenz.

**"Ein Dashboard auf einem Wandtablet soll sich nicht versehentlich
verändern."** → Dashboard öffnen → Editor → "Fixiert" aktivieren.

**"Ich möchte alte Symcon-Daten übernehmen, ohne HA-Live-Daten zu
verdoppeln."** → **Import → Symcon** → ZIP hochladen → Zuordnung prüfen →
Import starten. Bereits vorhandene Zeitstempel werden automatisch
übersprungen, unabhängig von der Quelle.

**"Ich nutze kein Symcon und möchte trotzdem die bisherige HA-Historie
übernehmen."** → **Import → Home Assistant** → Entitäten auswählen,
optional „Verfügbarkeit prüfen“ → Vorschau (Dry Run) → Import starten.
