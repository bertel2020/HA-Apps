# Benutzerhandbuch

Dieses Dokument ist die ausführliche Anleitung für Nutzer der App — Schritt
für Schritt, aufgabenorientiert, jede Seite im Detail. Für einen kurzen
Überblick (was die App ist, Kernfunktionen auf einen Blick) siehe die
[App-README](../README.md); technische Interna für Entwickler stehen in den
übrigen Dokumenten dieses Ordners (siehe [README.md](README.md)).

## Inhalt

- [Erste Schritte](#erste-schritte)
- [Die Übersichtsseite](#die-übersichtsseite)
- [Übersichten durchsuchen und sortieren](#übersichten-durchsuchen-und-sortieren)
- [Dashboards](#dashboards)
- [Entitäten und Verläufe](#entitäten-und-verläufe)
- [Entität konfigurieren](#entität-konfigurieren)
- [Bereinigung](#bereinigung)
- [Datenhandling](#datenhandling)
- [Charts](#charts)
- [Vergleichstabellen](#vergleichstabellen)
- [Energiedashboard](#energiedashboard)
- [Statistik](#statistik)
- [Housekeeping](#housekeeping)
- [Import und Export](#import-und-export)
- [Backup / Restore](#backup--restore)
- [Einstellungen im Detail](#einstellungen-im-detail)
- [Typische Aufgaben](#typische-aufgaben)
- [Häufige Fragen](#häufige-fragen)

## Erste Schritte

1. **App installieren** — über den Add-on-Store (Repository
   `https://github.com/bertel2020/HA-Apps` hinzufügen) oder manuell. Details:
   [App-README → Installation](../README.md#installation).
2. **API-Token kopieren** — Zeitarchiv über die Home-Assistant-Seitenleiste
   öffnen, **Einstellungen → Verbindung**, Token kopieren. Der Token wird
   beim ersten Start automatisch erzeugt und ist nur für diese Zeitarchiv-
   Installation gültig.
3. **Integration installieren** — [github.com/bertel2020/HA-Zeitarchiv](https://github.com/bertel2020/HA-Zeitarchiv),
   über HACS oder manuell. In Home Assistant unter **Einstellungen → Geräte
   & Dienste → Integration hinzufügen → Zeitarchiv** Host (`localhost`),
   Port (`8127`) und Token eintragen.
4. **Archivfilter festlegen** — auf der Integrationskachel **Konfigurieren →
   Archivfilter bearbeiten**: Domains, einzelne Entitäten, Bereiche oder
   Geräte auswählen. Ohne Filter kommen keine Daten an; die App wartet dann
   untätig, ohne Fehler anzuzeigen.
5. Nach dem ersten empfangenen Wert erscheint die Entität automatisch in
   **Entitäten** — mit den globalen Standardwerten aus **Einstellungen →
   Archivierung**. Diese Standards lassen sich pro Entität jederzeit
   individuell überschreiben (siehe [Entität konfigurieren](#entität-konfigurieren)).
6. Filter, Token oder Standards lassen sich jederzeit nachträglich ändern —
   bereits archivierte Werte bleiben davon unberührt, nur künftige Werte
   folgen den neuen Einstellungen.

**Woran erkenne ich, dass Daten ankommen?** Unter **Einstellungen →
Verbindung** zeigt "Letzter empfangener Wert" den Zeitpunkt des zuletzt
verarbeiteten Schreibvorgangs. Bleibt dieser Wert dauerhaft leer oder alt,
liegt es entweder an fehlenden Archivfiltern (Schritt 4) oder an einem
falschen Token/Host in der Integration (Schritt 3).

## Die Übersichtsseite

Die Startseite (Sidebar-Eintrag "Zeitarchiv") zeigt oben eine
Kennzahlenübersicht (Anzahl Entitäten, Datensätze, Speicherbedarf) und
darunter das **Standard-Dashboard** — dieselbe Kachel-Ansicht wie unter
**Dashboards**, nur fest der Startseite zugeordnet und nicht umbenennbar
oder löschbar. Es lässt sich wie jedes andere Dashboard mit Kacheln
bestücken, umsortieren und fixieren (siehe unten).

Die Glocke in der Kopfzeile (auf jeder Seite sichtbar) zeigt aktuelle
Systemmeldungen — z. B. eine empfohlene Index-Optimierung, einen
fehlgeschlagenen Backup- oder Aufbewahrungslauf, oder ein verfügbares
Update. Nicht-kritische Meldungen (Info/Warnung) lassen sich einzeln für
1 Stunde, 1 Tag, 7 Tage, 30 Tage oder dauerhaft stummschalten; Fehler nie.
Bereits stummgeschaltete Meldungen bleiben unter **Einstellungen →
Meldungen** einsehbar und lassen sich dort vorzeitig wieder aktivieren.

## Übersichten durchsuchen und sortieren

Die drei Übersichten **Dashboards**, **Charts** und **Tabellen** funktionieren
gleich: eine Kachel je Eintrag, darüber eine Zeile zum Suchen und Sortieren.

- **Suche** filtert nach dem Namen, ohne auf Groß- und Kleinschreibung zu
  achten. Umlaute lassen sich auch umschrieben eingeben: „ubersicht“ oder
  „uebersicht“ findet ebenso „Übersicht“.
- **Sortierung**: neueste zuerst (Vorgabe), älteste zuerst, Name A–Z oder
  Name Z–A.
- **Favoriten zuerst** ist ein eigener Schalter neben der Sortierung, kein
  eigener Sortiermodus. Eingeschaltet stehen favorisierte Einträge oben,
  innerhalb der Favoriten und darunter gilt die gewählte Sortierung
  unverändert weiter — beides ist also frei kombinierbar. Der Schalter ist
  standardmäßig aktiv.
- Suchbegriffe wirken nur für den Moment; die gewählte Sortierung und der
  Favoriten-Schalter werden je Übersicht im Browser gemerkt und gelten beim
  nächsten Aufruf wieder.

Ein Klick auf eine Kachel öffnet den Eintrag. Alles Weitere — Bearbeiten,
Duplizieren, Löschen — steht im Kachelmenü (⋮) oben rechts, der Stern daneben
schaltet den Favoriten um.

**Namen** von Dashboards, Charts und Tabellen sind jeweils innerhalb ihrer
Gattung eindeutig und höchstens 50 Zeichen lang. Beim Vergleich spielen Groß-
und Kleinschreibung sowie Leerzeichen am Rand keine Rolle: neben einem Chart
„Wind“ lässt sich kein zweites „wind“ anlegen. Ein bereits vergebener Name
wird beim Speichern mit einem Hinweis abgelehnt. Duplikate zählen selbst
hoch — „Wind (Kopie)“, danach „Wind (Kopie 2)“ und so weiter.

## Dashboards

- **Dashboards**-Menüpunkt (Hauptnavigation) klappt eine Liste aller
  vorhandenen Dashboards auf. Von dort: neues Dashboard anlegen, ein
  bestehendes öffnen, umbenennen, favorisieren, duplizieren, als
  **Standard-Dashboard** festlegen (dieses erscheint dann auf der
  Übersichtsseite und steht in der Dashboard-Übersicht immer an erster Stelle,
  unabhängig von Sortierung oder „Favoriten zuerst“) oder löschen.
  Es gibt keine Obergrenze für die Anzahl der Dashboards.
- Ein Klick auf die Kachel öffnet das Dashboard; „Bearbeiten“, „Duplizieren“,
  „Als Standard festlegen“ und „Löschen“ stehen im Kachelmenü (⋮).
  Suchfeld, Sortierung und der Schalter „Favoriten zuerst“ über den Kacheln
  funktionieren wie bei Charts und Tabellen (siehe
  [Übersichten durchsuchen und sortieren](#übersichten-durchsuchen-und-sortieren)).
- Jedes Dashboard zeigt bis zu 18 Kacheln — Charts, Vergleichstabellen und
  **Werte-Kacheln** gemischt — in frei wählbarer Größe (1×1 bis 3×3, im
  Präzisen Modus bis 6×6). Per Drag-and-drop anordnen; über das Kachelmenü
  (⋮) Größe ändern, duplizieren (Charts/Tabellen) oder entfernen. Das
  Entfernen einer Kachel löscht nur die Platzierung, nicht das zugrunde
  liegende Chart oder die Tabelle.
- **Werte-Kachel:** pinnt den aktuellen Wert einer einzelnen Entität direkt
  aufs Dashboard, ohne dafür ein Chart anzulegen. Nach dem Anheften öffnet
  sich sofort die Konfiguration. Die Sparkline ist standardmäßig aktiv und
  zeigt die im Zeitarchiv gespeicherten Rohpunkte der letzten 24 Stunden;
  alternativ lässt sie sich auf einen Punkt je 5, 15 oder 30 Minuten oder je
  Stunde verdichten. Entität, Anzeige der letzten Aktualisierung,
  Nachkommastellen und Titel sind direkt in der Kachel bearbeitbar. Ist der
  letzte Wert älter als 15
  Minuten bzw. eine Stunde, hebt sich der Kartenrahmen gelb bzw. rot
  hervor. Alle Einstellungen einer Werte-Kachel liegen in einem eigenen,
  größeren Einstellungs-Popup (⋮), da hier deutlich mehr Optionen als bei
  Chart-/Tabellen-Kacheln zusammenkommen.
- **Kachel hinzufügen:** die "+"-Kachel öffnet ein Popup mit Registerkarten
  für Charts, Tabellen und Werte-Kacheln, jeweils mit Suchfeld. Charts und
  Tabellen lassen sich direkt aus der Liste anheften oder über "+ Neuer
  Chart"/"+ Neue Tabelle" neu anlegen (landet nach dem Speichern
  automatisch auf diesem Dashboard); Werte-Kacheln werden über die
  Entitäten-Suche ausgewählt. Ein Chart oder eine Tabelle kann gleichzeitig
  auf mehreren Dashboards angeheftet sein.
- In der geöffneten Ansicht eines gespeicherten Charts oder einer Tabelle
  zeigt **Verwendet in**, auf welchen Dashboards der Eintrag liegt. Ein
  Dashboard ist direkt verlinkt; bei mehreren öffnet der Zähler eine kompakte
  Liste mit Links. Die Zuordnung wird weiterhin im jeweiligen Dashboard
  geändert.
- Chart-Kacheln ab Größe 2×2 (im Präzisen Modus ab 3×3) können über das
  Kachelmenü (⋮) eine Legende einblenden ("Legende anzeigen") — Aussehen
  und Inhalt entsprechen dabei exakt der Legende des zugrundeliegenden
  Charts, inklusive dessen "Werte anzeigen"- und Nachkommastellen-
  Einstellung; ein Klick auf die Legende blendet die jeweilige Reihe
  ein/aus, ohne zum Chart zu navigieren.
- **Präziser Modus** (Dashboard-Editor): verdoppelt das Kachelraster von 3
  auf 6 Spalten bei halber Zeilenhöhe — bestehende Kacheln behalten dabei
  ihre optische Größe, weil ihre Größenangabe automatisch mitverdoppelt
  wird. **Lücken auffüllen** lässt spätere, kleinere Kacheln freie Lücken
  im Raster füllen statt strikt der Anheft-Reihenfolge zu folgen. Beide
  Schalter sind unabhängig voneinander kombinierbar.
  Auf schmalen Displays bleibt die Darstellung auch im Präzisen Modus bewusst
  einspaltig, damit Kacheln lesbar und bedienbar bleiben.
- **Dashboard fixieren** (Editor, Schalter "Fixiert"): sperrt Umsortieren,
  Größenändern und Entfernen von Kacheln auf der Ansicht selbst — schützt
  vor versehentlichem Verschieben auf einem z. B. dauerhaft angezeigten
  Wandtablet. Umbenennen und Löschen des Dashboards bleiben im Editor
  weiterhin möglich; nur die Kachel-Ansicht selbst ist gesperrt.
- **Dashboard löschen** entfernt nur die Kachel-Anordnung dieses
  Dashboards — die zugrunde liegenden Charts/Tabellen bleiben erhalten und
  lassen sich über "+ Kachel hinzufügen" anderswo neu anheften.
- Die Ein-/Ausblend-Animation der Kachel-Charts (kurzes Auf- statt
  Sofort-Erscheinen) gilt zentral für alle Kacheln auf allen Dashboards und
  lässt sich unter **Einstellungen → Darstellung** abschalten.

## Entitäten und Verläufe

**Entitäten** listet alle bekannten Entitäten, durchsuchbar über den Namen
oder die Entity-ID und filterbar (z. B. nach Domain). Ein Klick auf eine
Zeile öffnet die **Verlaufsansicht** dieser einen Entität.

### Zeitraum-Navigation

- Auswahl von Stunde, Tag, Woche, Monat, Jahr oder Dekade als Basiseinheit;
  Vor-/Zurück-Pfeile blättern jeweils um eine Einheit.
- **Laufend** ("bis heute") zeigt den aktuellen, noch nicht abgeschlossenen
  Zeitraum, z. B. "diese Woche bis jetzt" — auch bevor für die restliche
  Periode überhaupt Daten vorliegen, reicht die Achse bis zur vollen
  Kalendergrenze (z. B. bis Sonntag bei "Woche").
- **Rollierend** zeigt stattdessen ein festes Zeitfenster relativ zu jetzt,
  z. B. "letzte 24 Stunden" oder "letzte 30 Tage", unabhängig von
  Kalendergrenzen.

### Darstellung

- **Diagrammtyp:** Linie oder Balken; bei Schalter-Entitäten (`switch`,
  `binary_sensor` u. Ä.) zusätzlich ein Zeitstrahl, der die AN-Intervalle
  als durchgehende Balken zeigt statt einzelner Punkte.
- Linien lassen sich glätten (Optionen-Menü), was kurzfristiges Rauschen
  visuell unterdrückt, ohne die zugrunde liegenden Werte zu verändern.
- **Rohwerte** zeigt statt aggregierter Punkte jeden einzelnen
  gespeicherten Messwert im Zeitraum — sinnvoll bei genauerer Prüfung
  kurzer Zeiträume, bei sehr langen Zeiträumen begrenzt durch das
  Abfragelimit.
- Das Optionen-Menü zeigt nur, was für die aktuelle Darstellung überhaupt
  wirkt: **Punkte** und **Rohwerte** gehören zu Liniencharts, **Werte
  anzeigen** zu Balken. Beim Umschalten des Diagrammtyps wechseln die
  angebotenen Optionen entsprechend mit.
- **Dynamische Y-Achse** skaliert die Achse auf die tatsächliche
  Wertespanne des angezeigten Zeitraums statt bei 0 zu beginnen — macht
  kleine Schwankungen sichtbarer, kann die visuelle Größe von Änderungen
  aber auch überzeichnen.
- **Werte anzeigen** blendet die Zahlenwerte direkt neben den Datenpunkten
  ein.
- **Nachkommastellen** übersteuert für diese Ansicht die globale Anzeige-
  Einstellung der Entität (Automatisch oder fest 0–3).

### Vergleich

Über das Optionen-Menü lässt sich die aktuelle Ansicht mit der Vorperiode
oder dem Vorjahr überlagern. Die Beschriftung passt sich automatisch an den
gewählten Zeitraum an (z. B. "Vortag" bei Tagesansicht, "Vorjahrestag" beim
Jahresvergleich einer Tagesansicht) und erscheint direkt im Button, sodass
die aktive Vergleichsoption ohne Menüaufruf erkennbar bleibt.

### Kennzahlen und Legende

Aktuell/Min/Max/Durchschnitt/Summe des angezeigten Zeitraums lassen sich
wahlweise direkt einblenden — als kompakte Chips oder als kleine Tabelle
(einstellbar über den Legenden-Stil). Beide Darstellungen sind anklickbar,
um einzelne Reihen ein- oder auszublenden, ohne den Zeitraum zu verlassen.

### Ansicht sichern

**Als Chart speichern** (Optionen-Menü) legt die aktuelle Ansicht —
inklusive aller gewählten Optionen und ggf. bereits hinzugefügter weiterer
Entitäten — als eigenständiges Chart ab, das sich danach wie jedes andere
Chart bearbeiten und auf ein Dashboard anheften lässt.

### Gespeicherte Optionen

Alle Optionen im Optionen-Menü (Kontinuierlich/Rollierend, Rohwerte,
Diagrammtyp, Punkte anzeigen, Werte anzeigen, Dynamische Y-Achse,
Legenden-Statistik und -Kennzahlen, Legenden-Stil) werden **pro Entität**
dauerhaft gespeichert und beim nächsten Aufruf automatisch wieder
angewendet. Die Startwerte für neu geöffnete Entitäten lassen sich unter
**Einstellungen → Darstellung** ändern; "Optionen auf Standard
zurücksetzen" (im Optionen-Menü der Entität) wirft nur diese eine Entität
wieder auf diese Startwerte zurück.

## Entität konfigurieren

Über das Zahnrad-Symbol einer Entität (in der Liste oder in der
Verlaufsansicht) erreichbar:

| Feld | Bedeutung |
| --- | --- |
| App-eigener Anzeigename | Optional, bis 40 Zeichen — überschreibt nur die Darstellung in Zeitarchiv, nie Home Assistants eigenen `friendly_name` oder die Entitäts-ID selbst. Ein Tag-Symbol markiert überall, wo er aktiv ist; leer lassen setzt den Standardnamen zurück |
| Auflösung | Mindestabstand zwischen zwei gespeicherten Werten (z. B. "alle 5 Minuten"); engmaschigere Quellwerte werden entsprechend verdichtet |
| Aufbewahrung | Wie lange Werte behalten werden, bevor eine aktivierte automatische Löschung greift (**Unbegrenzt** möglich) |
| Nachkommastellen | „Automatisch“ zeigt bis zu drei Stellen und entfernt Nullen am Ende (z. B. 4 statt 4,000); eine feste Anzahl (0–3) rundet auf genau diese Stellen und ergänzt bei Bedarf Nullen (z. B. 4,00 bei 2 Stellen) |
| Wertänderungsfilter | Überspringt gerundet gleiche Folgewerte (spart Speicherplatz bei trägen Sensoren), behält aber mindestens alle 6 Stunden ein Lebenszeichen, damit lange Stillstände von fehlenden Daten unterscheidbar bleiben. Bei neu erkannten Entitäten standardmäßig aktiv (einstellbar unter **Einstellungen → Archivierung → Standards**) |
| Lücken-Erkennung | Schwellwert von 1 Minute bis 1 Tag (einschließlich 6 und 12 Stunden), ab dem eine Pause zwischen zwei Werten in der Bereinigung als Lücke markiert wird — „Aus“ deaktiviert die Markierung. Wird beim Aktivieren des Wertänderungsfilters automatisch auf mindestens 6 Stunden angehoben, falls kürzer eingestellt — sonst würde dessen eigenes, normales Schweigen ständig als Lücke gemeldet. Lässt sich danach jederzeit wieder manuell verkleinern |
| Ausreißer-Erkennung | Schwellwert in Prozent, um den ein Wert gegenüber dem Vorwert mindestens abweichen muss, um als Ausreißer markiert zu werden — "Aus" deaktiviert die Markierung |
| Anzeigemodus | Nur bei Schaltern: Rohwert (AN/AUS als Zustand) oder Zeit (kumulierte Einschaltdauer je Zeitraum) |

Änderungen an Auflösung, Aufbewahrung oder Nachkommastellen wirken nur auf
künftig eintreffende bzw. künftig berechnete Werte, nie rückwirkend auf
bereits archivierte Daten.

Am Seitenende dieser Konfigurationsseite stehen zwei endgültige Aktionen:

- **Alle Werte löschen** entfernt sämtliche Daten dieser Entität (laufender
  Monat, Archiv, Rollups), behält aber die individuelle Konfiguration
  (Auflösung, Aufbewahrung usw.) bei. Sinnvoll, um bei einer fehlerhaft
  konfigurierten Quelle noch einmal bei null anzufangen, ohne die
  Einstellungen neu setzen zu müssen.
- **Entität entfernen** löscht zusätzlich auch die Konfiguration. Sendet
  Home Assistant die Entität weiter, wird sie beim nächsten empfangenen
  Wert automatisch wieder mit den aktuellen globalen Standards neu
  angelegt.

Beide Aktionen verlangen vor der Ausführung eine eindeutige Bestätigung
(Eingabe des Entitätsnamens) und sind danach nicht rückgängig zu machen.
Charts oder Tabellen, die diese Entität verwenden, zeigen ab diesem
Zeitpunkt schlicht keine Daten mehr für sie.

## Bereinigung

Von der Verlaufsansicht über den Button "Bereinigen" erreichbar, drei
Reiter:

### 1. Bereinigen

Erkannte Ausreißer, Lücken, Duplikate und gerundet gleiche Wiederholungen
werden als Liste angezeigt, je mit einer kurzen Begründung (z. B. "3 Std.
50 Min. seit vorherigem Wert 21,2 °C um 08:10"). Einzelne Einträge oder alle
zusammen auswählen und löschen — das ist zunächst ein **Soft-Delete**: Die
Werte verschwinden sofort aus jeder Anzeige (Charts, Tabellen, Rohwerte),
sind aber über "Rückgängig (letzte Löschung)" wiederherstellbar, solange
noch keine endgültige Bereinigung stattgefunden hat (siehe unten).

Bei steigenden Zählern (Home-Assistant-`state_class` `total_increasing`,
z. B. Energiezähler) werden niedrigere Folgewerte gesondert als mögliche
Zähler-Resets protokolliert und unter "Zählerrückgänge" markiert; sie
bleiben standardmäßig gespeichert, da ein Reset (z. B. Zählertausch) ein
gültiges Ereignis sein kann und nicht automatisch als Fehler gilt.

Wiederholungen (gerundet gleiche Folgewerte) lassen sich mit derselben
Sechs-Stunden-Lebenszeichenregel wie der laufende Wertänderungsfilter auch
nachträglich verdichten — nützlich, wenn der Filter erst später aktiviert
wurde und ältere Daten noch unverdichtet vorliegen.

### 2. Korrigieren

Einzelne Werte direkt bearbeiten (Klick auf die Wert-Zelle in der
Rohwert-Tabelle) statt zu löschen — etwa um einen erkennbaren
Sensor-Ausreißer auf einen plausiblen Wert zu setzen, statt an dieser
Stelle eine Lücke offenzulassen. Die Rohwert-Tabelle zeigt dabei die
Einheit direkt neben jedem Wert.

### 3. Hinzufügen

Einen fehlenden Messpunkt manuell mit Zeitstempel und Wert ergänzen —
Zahlen im deutschen Format mit Komma als Dezimaltrennzeichen (z. B. `21,5`).

### Kopfzeile und endgültiges Entfernen

Die Kopfzeile des Bereinigungsbereichs zeigt sowohl die Datensatzanzahl im
aktuell gewählten Zeitraum als auch den sichtbaren Gesamtbestand samt
Ausreißern/Lücken/Duplikaten/Wiederholungen über die **komplette** Historie
der Entität — unabhängig vom gerade angezeigten Ausschnitt.

Soft-gelöschte Werte belegen weiterhin Speicherplatz, bis sie unter
**Housekeeping → Speicherplatz** physisch bereinigt werden. Dort zeigt
eine Vorschau vorab, wie viele Zeilen tatsächlich entfernbar sind
(inklusive Aufschlüsselung nach laufendem Monat und Archiv), bevor der
Schritt tatsächlich ausgeführt wird. Dieser Schritt ist endgültig — danach
ist "Rückgängig" nicht mehr möglich.

## Datenhandling

Dieser Abschnitt erklärt genauer, was hinter den Kulissen passiert, wenn
Werte gelöscht, geändert, hinzugefügt oder automatisch aufgeräumt werden —
und wie sich das jeweils auf Charts, Tabellen, Speicherplatz und
Wiederherstellbarkeit auswirkt.

### Der Weg eines Werts

Jeder eingehende Wert durchläuft dieselben Stationen, unabhängig davon, ob
er von der Integration kommt oder manuell hinzugefügt wurde:

```text
Eingehender Wert
      │
      ▼
Hot Buffer (laufender Monat, unkomprimiert)
      │
      │  Rotation: automatisch beim ersten Wert eines neuen Kalendermonats,
      │  oder manuell nachgeholt (z. B. bei einer länger stillen Entität)
      ▼
Archiv (abgeschlossene Monate, komprimiert)  ──►  Rollups (Stunde/Tag · Monat · Jahr)
      │                                                     │
      │              Aufbewahrung (Retention)                │
      │     entfernt ganze überfällige Monate aus Archiv     │
      │              UND den zugehörigen Rollups             │
      ▼                                                     ▼
              endgültig entfernt — kein "Rückgängig"
```

Charts und Tabellen greifen für kurze, aktuelle Zeiträume auf den Hot
Buffer zu und für längere/vergangene Zeiträume auf Archiv und Rollups —
diese Umschaltung geschieht automatisch und ist beim Ansehen nicht
sichtbar. Die Rotation selbst verändert an den Werten nichts, sie
verschiebt nur den laufenden Monat vom Hot Buffer ins Archiv, sobald er
abgeschlossen ist.

### Aufbewahrung (Retention)

Die je Entität konfigurierte Aufbewahrungsfrist (Zahnrad-Symbol → **Aufbewahrung**,
siehe [Entität konfigurieren](#entität-konfigurieren)) wird nicht laufend
angewendet, sondern nur, wenn die Aufbewahrungs-Durchsetzung tatsächlich
läuft:

- **Manuell** über **Housekeeping → Aufbewahrung** — mit einer Vorschau,
  die zeigt, was ein Lauf entfernen würde, bevor er tatsächlich ausgeführt
  wird.
- **Automatisch**, wenn unter **Housekeeping → Aufbewahrung** ein Zeitplan
  (täglich oder wöchentlich, mit Uhrzeit) hinterlegt ist. Ein einzelner
  Wartungsplaner prüft im Hintergrund regelmäßig, ob der nächste geplante
  Lauf fällig ist; war die App zum geplanten Zeitpunkt nicht aktiv, wird
  **höchstens ein** verpasster Lauf nachgeholt, nie mehrere auf einmal.
- Ein Lauf betrifft immer **nur ganze, abgeschlossene Kalendermonate** — ein
  Monat wird komplett entfernt, sobald sein Ende älter als die
  Aufbewahrungsfrist ist, niemals teilweise. Entfernt werden dabei
  gleichzeitig der Archiv-Monat und die dazugehörigen Rollup-Zeilen
  (Stunde/Tag, Monat, Jahr), damit beide nie auseinanderlaufen; im laufenden
  Monat (Hot Buffer) werden überfällige Zeilen direkt entfernt.
- Als **Unbegrenzt** markierte Entitäten werden dabei komplett übersprungen.
- Aufbewahrung, Backup, Import und Rotation greifen nie gleichzeitig auf den
  Datenbestand zu — läuft bereits einer dieser Vorgänge, wartet ein
  zeitgleich fälliger automatischer Aufbewahrungslauf nicht, sondern wird
  für diesen Termin übersprungen (der nächste reguläre Termin läuft normal
  weiter).

**Wichtig:** Anders als eine geänderte Auflösung (wirkt nur auf künftig
eintreffende Werte, siehe [Häufige Fragen](#häufige-fragen)) wirkt eine
verkürzte Aufbewahrungsfrist beim nächsten Durchlauf **rückwirkend** auf
bereits gespeicherte, abgeschlossene Monate. Eine Frist heraufzusetzen oder
auf Unbegrenzt zu stellen ist dagegen jederzeit gefahrlos — dadurch wird nie
etwas gelöscht, das bereits entfernt wurde, ist es endgültig weg.

Diese Löschung ist **nicht** das Soft-Delete aus dem Bereinigen-Tab, sondern
sofort endgültig — es gibt keine Vorstufe und kein "Rückgängig". Ein Backup
vor einer erstmalig aktivierten oder deutlich verkürzten Aufbewahrungsfrist
ist deshalb empfehlenswert.

### Werte löschen: drei Stufen

Werte, die über **Bereinigen** entfernt werden (einzeln oder als Auswahl),
durchlaufen drei klar getrennte Stufen — nur die letzte davon ist
endgültig:

```text
Wert vorhanden
      │
      │  Bereinigen → "Löschen"
      ▼
Als gelöscht markiert (Soft-Delete)
  · verschwindet sofort aus Charts, Tabellen, Rohwert-Listen, Export, Statistik
  · die Datei auf der Festplatte bleibt dabei unverändert
  · zählt weiterhin zum belegten Speicherplatz
      │                                    │
      │  Bereinigen →                      │  Housekeeping → Speicherplatz →
      │  "Rückgängig (letzte Löschung)"    │  "Bereinigen" (mit Vorschau)
      ▼                                    ▼
Wert wieder sichtbar                Physisch entfernt
                                       · Archiv- bzw. Hot-Buffer-Datei neu geschrieben
                                       · betroffene Rollups neu berechnet
                                       · endgültig, kein "Rückgängig" mehr möglich
```

Zur Markierung ("Löschen"):

- Es wird nichts aus einer Datei entfernt — lediglich vermerkt, dass dieses
  Vorkommen (Entität + Zeitstempel) ab sofort überall ausgeblendet werden
  soll. Bei zwei Werten mit exakt demselben Zeitstempel (Duplikat) lässt
  sich so gezielt nur einer der beiden löschen, ohne den anderen
  mitzunehmen.
- Jeder Löschvorgang (ein Klick auf "Löschen", egal ob ein Wert oder eine
  ganze Auswahl) bildet einen eigenen **Stapel**.

Zu "Rückgängig (letzte Löschung)":

- Macht **immer nur den zuletzt ausgeführten Stapel** rückgängig — es gibt
  keinen längeren Verlauf und kein Zurückspringen über mehrere
  Löschvorgänge hinweg. Ein zweiter Klick auf "Rückgängig" ohne
  zwischenzeitliches erneutes Löschen bewirkt nichts mehr.
- Funktioniert nur, solange der Stapel noch nicht physisch bereinigt wurde
  (siehe unten) — danach existiert die Markierung nicht mehr, es gibt
  nichts mehr rückgängig zu machen.

Zu "Bereinigen" unter **Housekeeping → Speicherplatz**:

- Das ist der einzige Schritt in diesem Kapitel, der Dateien auf der
  Festplatte tatsächlich verändert: Die betroffene Archiv- bzw.
  Hot-Buffer-Datei wird ohne die markierten Zeilen neu geschrieben, die
  davon abhängigen Rollup-Werte werden neu berechnet. Sind in einem
  gesamten Archiv-Monat alle Werte als gelöscht markiert, wird die
  Monatsdatei (samt Rollup-Zeilen) komplett entfernt statt leer neu
  geschrieben.
- Eine Vorschau zeigt vorab, wie viele Zeilen tatsächlich entfernbar sind
  (aufgeschlüsselt nach laufendem Monat und Archiv), bevor der Schritt
  bestätigt wird.
- Danach sind die betroffenen Werte unwiederbringlich weg — auch mit einem
  neuen Backup lässt sich das nicht mehr innerhalb der App rückgängig
  machen (nur eine Wiederherstellung aus einem **älteren** Backup, das vor
  diesem Schritt erstellt wurde, brächte die Werte zurück).

### Werte ändern und hinzufügen

**Korrigieren** (einen bestehenden Wert bearbeiten) und **Hinzufügen**
(einen fehlenden Messpunkt manuell ergänzen) laufen technisch **anders**
als Löschen: Es handelt sich um direkte Schreibvorgänge, nicht um das oben
beschriebene Markieren-Modell.

> ⚠️ **Für Korrigieren und Hinzufügen gibt es kein "Rückgängig".** Anders
> als beim Löschen wird keine Markierung gesetzt, sondern der Wert sofort
> direkt in der Hot-Buffer- bzw. Archiv-Datei überschrieben bzw. ergänzt.
> Ein versehentlich falsch korrigierter oder falsch eingetragener Wert lässt
> sich nur durch eine erneute manuelle Korrektur beheben — oder, falls
> schon zu spät bemerkt, durch die Wiederherstellung eines vorherigen
> Backups. Vor umfangreicheren manuellen Korrekturen lohnt sich deshalb ein
> kurzer Blick auf **System → Backup / Restore**.

Bei "Korrigieren" wird bei mehreren Werten mit demselben Zeitstempel
(Duplikat) gezielt nur der erste zu diesem Zeitstempel gefundene Wert
angepasst, die übrigen bleiben unverändert. Ändert sich der Wert zwischen
Laden der Seite und Bestätigen des Korrigieren-Dialogs (z. B. weil er
zwischenzeitlich schon gelöscht wurde), passiert schlicht nichts — ohne
Fehlermeldung.

### Lücken, Duplikate, Wiederholungen und Zählerrückgänge im Detail

Die vier zusätzlichen Markierungen im Bereinigen-Tab (neben Ausreißern)
folgen jeweils einer eigenen, festen Regel:

| Kategorie | Regel | Was "Bereinigen" konkret tut |
| --- | --- | --- |
| **Lücke** | Der zeitliche Abstand zweier aufeinanderfolgender Werte überschreitet die je Entität eingestellte Schwelle (**Lücken-Erkennung**, in Minuten; "Aus" deaktiviert die Erkennung komplett) | Nur eine Markierung, keine automatische Aktion — Lücken werden angezeigt, nicht gelöscht |
| **Duplikat** | Zwei oder mehr Werte teilen sich exakt denselben Zeitstempel (nicht nur einen ähnlichen) | Der chronologisch zuerst gespeicherte Wert bleibt erhalten, alle weiteren zum selben Zeitstempel werden zum Löschen vorgeschlagen |
| **Wiederholung** | Ein Wert ist (nach Rundung auf die eingestellten Nachkommastellen) identisch zum zuletzt *behaltenen* Wert — dieselbe Regel, die auch beim Eintreffen neuer Werte laufend zur Verdichtung verwendet wird (siehe [Wertänderungsfilter](#entität-konfigurieren)) | Der erste Wert einer Serie gleicher Werte bleibt erhalten, alle folgenden werden vorgeschlagen — **außer** seit dem letzten behaltenen Wert sind bereits 6 Stunden vergangen (Lebenszeichenregel), dann bleibt auch ein unveränderter Wert erhalten |
| **Zählerrückgang** | Nur bei Zähler-Entitäten: ein Wert liegt niedriger als der unmittelbar vorherige behaltene Wert | Nur eine Markierung, **niemals** automatisch gelöscht — ein Rückgang kann ein echtes Ereignis sein (z. B. Zählertausch, Reset nach Neustart) |

Wichtig: Diese Markierungen schließen sich **nicht gegenseitig aus**. Ein
und derselbe Wert kann z. B. gleichzeitig als Ausreißer **und** als Teil
eines Duplikats markiert sein — die Filter-Reiter im Bereinigen-Tab wählen
jeweils nur aus, welche Werte eine bestimmte Markierung tragen, sie teilen
die Liste nicht in getrennte, überschneidungsfreie Gruppen auf.

## Charts

Die Chart-Übersicht listet alle gespeicherten Charts als Kacheln mit Suche,
Sortierung und Favoriten-Schalter (siehe
[Übersichten durchsuchen und sortieren](#übersichten-durchsuchen-und-sortieren)).
Jede Kachel nennt den Diagrammtyp, die Anzahl der Entitäten und den Zeitraum.
Enthält ein Chart Linien- und Balkenreihen zugleich — etwa eine Temperatur
neben einem Zähler —, werden beide Typen genannt.

Eigener Editor, erreichbar über **Charts** → neues Chart oder Bearbeiten
eines bestehenden (Kachelmenü ⋮):

- Beliebig viele Entitäten überlagern; unterschiedliche Einheiten erhalten
  automatisch getrennte Y-Achsen, sodass z. B. Temperatur und Luftfeuchte
  in einem Chart sinnvoll lesbar bleiben.
- **Auflösung** wählbar, inklusive "Automatisch" — dabei zeigt ein kleiner
  Hinweis direkt an, welche Auflösung das für den aktuell gewählten
  Zeitraum tatsächlich bedeutet (z. B. "≈ 1 Stunde"). Bei Zeitraum "Tag"
  steht zusätzlich die Auflösung "Tag" zur Verfügung: sie fasst den ganzen
  Tag zu einem einzigen Balken je Entität zusammen — praktisch, um z. B.
  Tages-Einspeisung und -Bezug als zwei nebeneinanderstehende Balken direkt
  zu vergleichen. Vergleichen, Kontinuierlich und Dynamische Y-Achse sind
  bei dieser Auflösung deaktiviert, da sie für einen einzelnen
  Tages-Balken keine sinnvolle zusätzliche Aussage liefern.
- Punkte an/aus, Rohwerte, dynamische Y-Achse, Werte anzeigen,
  Nachkommastellen, Legenden-Statistik — dieselben Optionen wie in der
  Verlaufsansicht einer einzelnen Entität, hier aber je Chart konfiguriert
  statt je Entität; Nachkommastellen gilt dabei einheitlich für alle
  Entitäten des Charts. Alle Einstellungen werden mit dem Chart gespeichert
  und gelten dann auch für dessen Vorschau auf Dashboards.
- Bei ausschließlich Schalter-Entitäten (`switch`, `binary_sensor` u. Ä.)
  steht wie in der Verlaufsansicht ein **Zeitstrahl** zur Verfügung — hier
  als mehrzeilige Darstellung mit einer Zeile je Entität, sodass sich
  AN-Intervalle mehrerer Schalter direkt untereinander vergleichen lassen.
- Mehrere Entitäten lassen sich per Ziehen oder über Pfeil-Buttons neu
  anordnen — das bestimmt die Reihenfolge in Legende, Statistik-Anzeige und
  Farbzuordnung.
- Ein gespeichertes Chart zeigt beim Ansehen immer die aktuell verfügbaren
  Daten, kein eingefrorener Schnappschuss zum Speicherzeitpunkt.
- Die geöffnete Ansicht zeigt unter **Verwendet in** die Dashboards, auf denen
  das gespeicherte Chart als Kachel liegt, und verlinkt direkt dorthin.

## Vergleichstabellen

Die Tabellen-Übersicht listet alle gespeicherten Tabellen als Kacheln mit
Suche, Sortierung und Favoriten-Schalter (siehe
[Übersichten durchsuchen und sortieren](#übersichten-durchsuchen-und-sortieren));
jede Kachel nennt die Anzahl ihrer Zeilen und Spalten.

Eigener Editor, erreichbar über **Tabellen** → neue Tabelle oder Bearbeiten
einer bestehenden (Kachelmenü ⋮):

- **Zeilen** sind Größen: eine einzelne Entität, eine Gruppe mehrerer
  Entitäten (wird zu einem Summenwert zusammengefasst), eine Formel, oder
  eine rein optische Trennlinie ohne eigene Daten.
- **Spalten** sind Zeiträume: frei benannt (z. B. "Heute", "Aug Vorjahr",
  "2026"), jeweils mit einem Zeitraum-Typ (Tag, Woche, Monat, Jahr …) und
  einem Versatz relativ zu heute (0 = aktuell, −1 = vorheriger, usw.). So
  lässt sich z. B. derselbe Monat über zwölf aufeinanderfolgende Jahre in
  zwölf Spalten nebeneinanderstellen. Die Beschriftung kann Platzhalter
  wie `{jahr}`, `{monat}`, `{quartal}` oder `{woche}` enthalten, die sich
  automatisch auf den jeweiligen Zeitraum der Spalte auflösen (Einfüge-
  Hilfe direkt im Beschriftungsfeld, mit Live-Vorschau des aufgelösten
  Werts). **Vorjahresvergleich** setzt den Versatz einer Spalte automatisch
  auf denselben Zeitraum ein Jahr zuvor (schaltjahrsicher). Steht neben einer
  vergangenen Spalte (Vortag, Vormonat, Vorjahr …) eine Spalte mit Versatz 0
  desselben Zeitraum-Typs, vergleicht die vergangene Spalte automatisch nur
  den bislang vergangenen Teil ihres Zeitraums ("Gleicher Zeitpunkt"-
  Vergleich) — ein noch laufender Tag wird so fair gegen "Vortag bis zur
  aktuellen Uhrzeit" statt gegen den kompletten Vortag verglichen.
- **Mehrstufige Kopfzeile:** Spalten mit derselben, nicht leeren
  Gruppen-Beschriftung (z. B. "2025" über mehreren Monatsspalten) bekommen
  automatisch eine gemeinsame, übergreifende Kopfzeile darüber.
- Spalten und Zeilen lassen sich über das jeweilige Kärtchen duplizieren
  (⧉) — Zeilen-Duplikate inklusive aller Optionen, Formel-Zeilen mit
  automatisch mitkorrigierten Buchstaben-Referenzen.

### Aggregation und Formatierung

- **Aggregation je Zeile:** Automatisch (bei Zählern die Summe, sonst der
  Durchschnitt), Ø Durchschnitt, Min, Max oder Σ Summe. Min/Max nutzen dabei
  die echten Extremwerte der zugrunde liegenden Rohdaten, nicht den
  Durchschnitt der kleinsten verfügbaren Zeitscheibe.
- **Nachkommastellen je Spalte:** Automatisch oder fest 0–3.
- **% Anteil** (Zeilen-Menü "Optionen"): zeigt statt des absoluten Werts den
  prozentualen Anteil an der Summe aller Entität-/Gruppen-Zeilen derselben
  Spalte seit der letzten Trennlinie.
- **Bei 0 ausblenden** (Zeilen-Menü "Optionen"): blendet eine Entität-/
  Gruppen-Zeile automatisch aus, sobald sie in allen sichtbaren Spalten
  entweder keinen Wert oder 0 hat — etwa ein stillgelegtes Gerät, ohne sie
  manuell aus- und wieder einblenden zu müssen.
- **Summenzeile** (eigener Zeilentyp): Summe oder Durchschnitt aller
  Entität-/Gruppen-Zeilen seit der letzten Trennlinie, aktualisiert sich
  automatisch, wenn darüber Zeilen hinzukommen oder wegfallen.
- **Farbskala** (Spalten-Option): färbt die Zellen einer Spalte nach ihrem
  Wert relativ zu den anderen Entität-/Gruppen-Zeilen im selben Abschnitt
  derselben Spalte ein — heller bei niedrigen, kräftiger bei hohen Werten.
  Formel-, Summen- und Trennzeilen werden dabei weder eingefärbt noch für
  die Skala berücksichtigt.

### Formeln

Formel-Zeilen referenzieren andere Zeilen über ihr Buchstaben-Kürzel (A, B,
C …), z. B. `A / B * 100`. Referenzierbar sind dabei nur Zeilen *oberhalb*
der Formel-Zeile. Beim Umsortieren von Zeilen (Ziehen oder Pfeil-Buttons)
werden die Buchstaben-Referenzen in bestehenden Formeln automatisch
mitkorrigiert, sodass eine Formel weiterhin dieselbe fachliche Zeile
referenziert wie vor dem Verschieben — nicht einfach dieselbe Position. Eine
Formel-Zeile übernimmt, sofern nicht eigens angegeben, automatisch die
Einheit der ersten referenzierten Zeile.

### Darstellung

Rein optische Einstellungen, wirken sich nie auf die berechneten Werte aus:

- **Hervorhebung:** Zebra-Streifen, erste Spalte hervorheben, Header
  hervorheben, Beschriftung fett.
- **Vergleich:** Vergleichsspalten (Vortag, Vormonat, Vorjahr …) optisch
  absetzen, prozentuale Abweichung zur zugehörigen Vergleichsspalte
  anzeigen.
- **Zahlen / Einheiten:** Einheiten ein-/ausblenden, in einer festen Spalte
  ausrichten oder kleiner darstellen, Dezimaltrennzeichen spaltenweise
  ausrichten, fehlende Werte als „Keine Daten“ statt als Gedankenstrich
  ausschreiben.
- **Layout:** Rahmen (horizontal/Gitter/ohne), Dichte (komfortabel/
  kompakt), Header-/Werte-Ausrichtung (linksbündig/zentriert/rechtsbündig,
  Vorgabe jeweils rechtsbündig), alle Werte-Spalten gleich breit
  ("Spalten gleichmäßig", die Beschriftungsspalte bleibt davon unberührt).
  **Erste Spalte fixieren** und **Header fixieren** halten Beschriftungsspalte
  bzw. Kopfzeile beim Scrollen sichtbar — Header fixieren begrenzt die
  Vorschau/Kachel dafür auf eine feste Höhe mit eigenem Scrollbalken.
  Spaltenbreiten lassen sich per Ziehgriff am rechten Rand jeder Kopfzelle
  anpassen (Doppelklick setzt eine Spalte auf automatische Breite zurück);
  ohne manuelle Breite richtet sich jede Spalte nach ihrem Inhalt.

Der Button **CSV** exportiert die aktuell sichtbaren Zeilen/Spalten (inkl.
% Anteil-/Einheiten-Einstellungen) als Semikolon-getrennte Datei.

Gespeicherte Tabellen zeigen beim Ansehen immer aktuelle Werte — wie
Charts, kein eingefrorener Schnappschuss zum Speicherzeitpunkt.
Unter **Verwendet in** sind die Dashboards, auf denen die Tabelle als Kachel
liegt, direkt erreichbar.

## Energiedashboard

Eigenständige Ansicht (kein Eintrag im normalen Dashboard-System), die den
Energiefluss eines Haushalts als Sankey-Diagramm zeigt. Sie wird über eine
feste Kachel oben auf der Dashboard-Übersicht ein- und ausgeschaltet und ist
danach auch im Menü **Dashboards** erreichbar. Beim ersten Aktivieren fragt
ein Rollen-Formular die vorhandenen Entitäten ab: Netzbezug (Pflicht),
Einspeisung, beliebig viele Erzeuger, beliebig viele Speicher (je
Laden/Entladen/SOC) sowie Verbraucher, die sich optional zu frei benannten
Gruppen zusammenfassen lassen.

Navigation läuft wie bei Charts über Stunde/Tag/Monat/Jahr mit Vor-/Zurück.
Ein Verbraucher mit zugewiesener Gruppe hängt im Sankey zweistufig am Bus
(Bus → Gruppe → Gerät), ein ungruppierter direkt am Bus wie ein Erzeuger —
hält den Fluss bei vielen einzelnen Verbrauchern übersichtlich. Gruppen
werden direkt beim Zuordnen eines Verbrauchers angelegt (bestehende
auswählen oder per Freitext eine neue erzeugen) oder über den eigenen
**„Gruppen"**-Button verwaltet (umbenennen, löschen — betroffene Verbraucher
werden dabei nur wieder gruppenlos, nicht verändert). Neben dem Sankey-Fluss
und den KPI-Kacheln (Erzeugung, Verbrauch, Netzbezug, Speicher, Einspeisung —
bei mehreren Speichern/Erzeugern als Summe mit Aufschlüsselung im Tooltip)
zeigen vier Ringe Autarkie, Eigenverbrauch, Speicher-Ladezustand und
Speicher-Wirkungsgrad (bei mehreren Speichern kapazitätsgewichtet
zusammengefasst, damit ein leerer und ein voller Speicher nicht fälschlich
als "50 %" erscheinen) — ein Klick auf einen Ring öffnet den jeweiligen
Monatstrend der letzten drei Jahre. Optionale Badges im Kopfbereich fassen
Kosten- und CO₂-Bilanz (mit eigenem Festpreis-Feld, wenn keine passende
Entität vorhanden ist) sowie die PV-Ertragsprognose zusammen. Der
**„Status"**-Chip öffnet ein Popup mit Bilanzprüfung, den übrigen
Datenqualitäts-Checks (veraltete Sensorwerte, Zählerrücksetzungen,
Einheiten/Zähler-Typ, doppelt zugeordnete Entitäten) und Auffälligkeiten —
Verbraucher oder Gruppen, die deutlich über ihrem Schnitt der letzten
Perioden liegen. Die Schwelle dafür (Standard +50 %) lässt sich im
Rollen-Formular unter „Allgemein" anpassen oder ganz abschalten. Ein
Tageslastprofil zeigt bei Tag/Stunde den stündlichen Verbrauch der letzten
7 Kalendertage; bei Monat/Jahr stattdessen den nach Wochentag gemittelten
Verbrauch (Mo–So) über den gewählten Zeitraum, sodass erkennbar wird, an
welchen Wochentagen typischerweise mehr verbraucht wird.

### Benötigte und sinnvolle Entitäten

Das Rollen-Formular (**Rollen zuordnen** bzw. **Rollen bearbeiten** im
Kartenkopf) wählt ausschließlich
aus bereits archivierten Entitäten aus — für das Energiedashboard muss also
vorher nichts zusätzlich eingerichtet werden, was nicht ohnehin schon in
Zeitarchiv ankommt.

| Rolle | Pflicht? | Erwarteter Wert |
| --- | --- | --- |
| Netzbezug | **ja** | Zählerstand Strombezug aus dem Netz (kWh, aufsteigend) |
| Einspeisung | nein | Zählerstand Netzeinspeisung (kWh, aufsteigend) |
| Erzeuger (beliebig viele) | nein | je ein Ertragszähler (kWh, aufsteigend) mit eigenem Namen — z. B. Dachanlage und Balkonkraftwerk getrennt geführt |
| Speicher: Laden / Entladen (beliebig viele Speicher) | nein | je zwei Zählerstände (kWh, aufsteigend) — Werte über mehrere Speicher hinweg werden addiert |
| Speicher: Ladezustand (SOC) | nein | Momentanwert in Prozent, kein Zähler — bei mehreren Speichern kapazitätsgewichtet gemittelt |
| Verbraucher (beliebig viele) | nein | je ein Verbrauchszähler (kWh, aufsteigend) mit eigenem Namen und optional einer frei benannten Gruppe — alles nicht einzeln zugeordnete bleibt automatisch als „Grundlast“ sichtbar |
| Strompreis (Bezug/Einspeisung) | nein | €/kWh-Entität; ohne passende Entität ersatzweise ein fester Cent-Betrag |
| CO₂-Intensität | nein | g/kWh-Entität; ohne passende Entität ersatzweise ein fester Wert |
| PV-Ertragsprognose | nein | kWh für „Rest heute“ und „morgen“, z. B. aus einer Forecast.Solar-Integration |

Einzig Netzbezug ist Pflicht — alle anderen Rollen schalten lediglich
zusätzliche Kacheln, Ringe oder Badges frei; ohne Speicher-Rolle bleiben
z. B. einfach die Speicher-Kacheln und der Wirkungsgrad-Ring ausgeblendet.

Für Netzbezug, Einspeisung, Erzeuger, Speicher (Laden/Entladen) und
Verbraucher wird ein **kWh-Gesamtzähler** erwartet (Home-Assistant-Gerätetyp
`total_increasing`), keine Momentanleistung in Watt — viele Geräte-
Integrationen bieten beides parallel an, hier zählt jeweils die
kWh-Zähler-Entität, nicht die Watt-Entität. Speicher-SOC, Strompreis,
CO₂-Intensität und PV-Prognose sind dagegen bewusst Momentan-/Messwerte
(`measurement`), keine Zähler.

### Aufbewahrung richtig einstellen

Die je Entität eingestellte [Aufbewahrungsfrist](#aufbewahrung-retention)
wirkt sich unterschiedlich stark auf das Energiedashboard aus — nicht jede
Rolle braucht dieselbe Frist:

- **Netzbezug, Einspeisung, Erzeuger, Speicher (Laden/Entladen/SOC) und
  Verbraucher** sollten großzügig aufbewahrt werden — mindestens
  **2 Jahre**, im Zweifel **Unbegrenzt**. Die Autarkie-, Eigenverbrauchs-,
  SOC- und Wirkungsgrad-Trends im Ring-Popup werten jeweils die letzten drei
  Kalenderjahre aus; eine kürzere Frist lässt diese Trends mit der Zeit
  lückenhaft werden.
- **Strompreis- und CO₂-Entitäten** (falls über eine Entität statt eines
  festen Werts eingebunden) werden je angezeigtem Zeitraum-Bucket
  eingerechnet. Fehlen dafür Werte, weil die Aufbewahrungsfrist sie
  inzwischen entfernt hat, fällt die Kosten-/CO₂-Bilanz für diesen
  vergangenen Zeitraum lediglich kleiner aus — kein Fehler, nur eine
  unvollständige Auswertung. Wer hauptsächlich aktuelle bis wenige Monate
  alte Auswertungen braucht, kommt hier mit **90 Tage** oder **365 Tage**
  aus und spart Speicherplatz: dynamische Tarife und CO₂-Signale
  aktualisieren sich oft im Minutentakt und wachsen entsprechend schnell.
- **PV-Ertragsprognose-Entitäten** werden ausschließlich als aktueller Wert
  angezeigt („Rest heute“ / „morgen“) — unabhängig vom gerade angezeigten
  Zeitraum wird nie ein archivierter, alter Prognosewert gelesen. Hier
  genügt die kürzeste verfügbare Frist (**30 Tage**); mehr Aufbewahrung
  bringt für diese Rolle keinen Vorteil, kostet bei häufig aktualisierenden
  Quellen aber unnötig Speicherplatz.

## Statistik

Zeigt Entitätenzahl, Datensätze, Speicherbedarf und Wachstum über die Zeit,
sowie Aufschlüsselungen nach Typ, Auflösung und Aufbewahrung. Ein interner
Planer erfasst unabhängig von Seitenaufrufen höchstens stündlich einen
realen Bestandsschnappschuss, sodass die Wachstumsansicht auch ohne
regelmäßigen Besuch der Seite aussagekräftig bleibt.

Alle Tabellen lassen sich durch Anklicken ihrer Spaltenüberschriften wie die
Entitätenliste sortieren. Das Wachstumsdiagramm passt seine beiden Y-Achsen
dynamisch an den jeweils sichtbaren Wertebereich an.

In der Speichernutzung führt **Index** zu einer Detailseite. Sie schlüsselt
auf, welche SQLite-Tabellen Entitätsmetadaten, Schreibsicherheit und
Bereinigung, Charts/Tabellen/Dashboards, Statistikverläufe sowie Einstellungen
und Wartungshistorien enthalten. Pro Tabelle und Bereich werden Eintragszahl,
belegte Datenseiten, zugehörige SQLite-Indizes und deren Gesamtgröße angezeigt.
Interne Strukturen und freie SQLite-Seiten bleiben separat ausgewiesen. Die
eigentlichen Messreihen liegen weiterhin in Hot Buffer, Archiv und Rollups,
nicht im Index.

Die Indexdetailseite zeigt außerdem den vollständig freien, durch eine
Kompaktierung reclaimbaren Speicher. SQLite verwendet diese Seiten im
laufenden Betrieb automatisch wieder. Eine manuelle **Index optimieren**-
Aktion schreibt die Datenbankdatei kompakt neu; währenddessen pausieren
Schreibzugriffe kurzzeitig. Eine Empfehlung erscheint erst bei einer
Indexgröße ab 50 MB, mindestens 10 MB reclaimbarem Speicher und mindestens
25 % freien Seiten. In diesem Fall wird auch der Index in der
Speichernutzung mit **Optimierung empfohlen** markiert. Vor der Ausführung
prüft Zeitarchiv den freien Plattenplatz und danach die SQLite-Integrität;
eine automatische Optimierung findet nicht statt.

Während der Optimierung wartet Zeitarchiv zunächst, bis bereits laufende
Schreibvorgänge abgeschlossen sind. Neue Übertragungen der Home-Assistant-
Integration pausieren an der Wartungssperre. Dauert die Optimierung länger als
der HTTP-Timeout, behält die Integration den betroffenen Batch und versucht ihn
ohne festes Retry-Limit erneut. Stabile Ereignis-IDs sorgen dafür, dass ein
erneut gesendeter oder teilweise bereits verarbeiteter Batch keine doppelten
Messwerte erzeugt. Im normalen Betrieb gehen durch die Optimierung daher keine
Werte verloren.

Die Integrationswarteschlange liegt allerdings nur im Arbeitsspeicher und ist
auf 5.000 neue Ereignisse begrenzt. Wird sie während eines außergewöhnlich
langen Rückstaus voll, werden weitere neue Ereignisse verworfen; ein Neustart
von Home Assistant oder der Integration verwirft ebenfalls noch nicht
übertragene Werte. Queue-Größe und verworfene Ereignisse sind auf der
Geräteseite der Integration unter **Diagnose** sichtbar.

Die Speicherplatz-Aufschlüsselung verlinkt direkt zu Import-Reports und
Backups, da auch diese Speicherplatz belegen, aber in der reinen
Entitäten-Statistik nicht enthalten sind.

## Housekeeping

Eigener Menüpunkt unter **System**, unterhalb Statistik — sammelt an einer
Stelle, was sonst leicht übersehen wird, mit derselben seitlichen Navigation
wie die Einstellungen:

| Bereich | Zeigt |
| --- | --- |
| **Duplikate** | Archivweit erkannte doppelte Zeitstempel der letzten 30 Tage, je Entität — derselbe stündliche Hintergrund-Schnappschuss, der auch die Meldung „Duplikate gefunden" auslöst. Entfernbar über „Duplikate automatisch entfernen" auf der jeweiligen Bereinigungs-Seite. |
| **Inaktive Entitäten** | Entitäten ohne neuen Wert seit einem wählbaren Schwellwert (1 bis 30 Tage). Nie empfangene Entitäten erscheinen unabhängig vom Schwellwert immer. Meist harmlos (Standby, seltener Sensor), aber ein früher Hinweis auf eine tote Integration oder eine umbenannte/entfernte HA-Entität. |
| **Speicherplatz** | Indexkonsistenz prüfen/reparieren; markierte Datensätze endgültig aus Hot Buffer und Archiv entfernen (siehe [Bereinigung](#bereinigung)). |
| **Aufbewahrung** | Übersicht aktuell fälliger und bereits gelöschter Datensätze; Vorschau fälliger Löschungen; Zeitplan für automatische Durchsetzung (täglich oder wöchentlich mit Wochentag); Lauf-Historie. |
| **Rotation** | Entitäten mit noch nicht archiviertem Vormonat (passiert normalerweise automatisch beim nächsten empfangenen Wert) — bei Bedarf manuell nachziehbar, z. B. wenn eine Entität längere Zeit keine Werte mehr gesendet hat. |
| **Ungenutzte Elemente** | Charts und Vergleichstabellen, die in keinem Dashboard angepinnt sind — direkt öffnen oder löschen. Verschwindet automatisch aus der Liste, sobald irgendwo angepinnt. |

Jeder Bereich verlinkt aus der passenden Systemmeldung (siehe unten), falls
gerade etwas ansteht — Housekeeping selbst muss dafür nicht regelmäßig
aufgesucht werden.

### Systemmeldungen

Das Meldungs-Center (Glocke in der Kopfzeile) sammelt Hinweise, die
automatisch verschwinden, sobald ihre Ursache behoben ist — kein eigener
Erledigt-Status nötig. Neben Update-Verfügbarkeit, empfohlener
Index-Optimierung und fehlgeschlagenen Backup-/Aufbewahrung-Läufen prüft
Zeitarchiv unter anderem:

- Speicherindex-Prüfung unvollständig oder mit gefundenen (meist bereits
  automatisch reparierten) Abweichungen
- Kein automatischer Backup-Zeitplan aktiv
- Aufbewahrung für Entitäten konfiguriert, aber die automatische Durchsetzung
  ausgeschaltet
- Letzter Import fehlgeschlagen oder nur teilweise abgeschlossen
- Endgültige Bereinigung möglich, Duplikate gefunden, Rotation ausstehend
- Inaktive Entitäten, dreistufig nach Alter (1/3/7 Tage, mit steigendem
  Schweregrad)
- Wertänderungsfilter einer Entität steht im Konflikt mit einer zu kurzen
  Lücken-Erkennung (siehe [Entität konfigurieren](#entität-konfigurieren))
- Tageslastprofil im Energiedashboard wird nach einer Konfigurationsänderung
  noch rückwirkend vervollständigt

Alle Meldungen außer echten Fehlern lassen sich über das 🔕-Icon
stummschalten (1 Stunde bis dauerhaft) — einsehbar und vorzeitig
zurückholbar unter **Einstellungen → Meldungen**.

### Tipps

Im Meldungs-Center rotiert außerdem ein kurzer Praxis-Tipp zu Funktionen der
App — 30 Tipps insgesamt, täglich wechselnd. Unter **Einstellungen →
Meldungen** lässt sich die Tipp-Anzeige komplett abschalten oder ein Dialog
mit allen Tipps und ihrem aktuellen Status öffnen; darin lässt sich der
gerade aktuelle Tipp für den Rest des Tages ausblenden, ohne die Rotation zu
unterbrechen.

## Import und Export

Erreichbar über **Import**, vier Reiter:

### Symcon

ZIP des `db`-Ordners hochladen, optional eine `settings.json` für Namen und
Einheiten ergänzen. Danach: Variablen prüfen und den gewünschten
Home-Assistant-Entitäten zuordnen. Weichen Quell- und Zieleinheit
voneinander ab (z. B. `klx` in Symcon vs. `lx` in Home Assistant), erscheint
ein Hinweis und ein Umrechnungsfaktor kann angegeben werden (hier `1000`).
Vor dem eigentlichen Import lässt sich die Zuordnung noch einmal prüfen.

Für bereits abgeschlossene Monate mit vorhandener Archivdatei gilt
Monatsgranularität: der ganze Monat wird übersprungen, auch wenn er
tatsächlich Lücken enthält — siehe „Duplikatschutz" weiter unten.

### CSV

Trennzeichen sowie Zeit-, Wert- und Zielspalte frei zuordnen, das Ergebnis
vor dem eigentlichen Import prüfen (Vorschau der ersten Zeilen mit erkannten
Werten).

### Home Assistant

Bestehende Recorder-Daten direkt aus der laufenden Home-Assistant-Instanz
übernehmen, ohne Symcon oder eine hochgeladene Datei. Zur Auswahl stehen nur
Entitäten, die bereits in Zeitarchiv bekannt sind — also von der
Home-Assistant-Integration konfiguriert wurden und mindestens einen
Live-Wert übertragen haben.

Der empfohlene **Vollimport** verbindet beide Quellen automatisch. Die
Einzelmodi bleiben für gezielte Importe verfügbar:

- **Vollimport:** ermittelt zuerst die tatsächlich verfügbare Rohhistorie und
  ergänzt davor die ältere Stundenstatistik. Die Schnittstelle wird
  für jede Entität einzeln auf die nächste volle Stunde ab dem ersten
  abgerufenen Rohwert gelegt — aber nur, wenn die Statistik dort lückenlos
  bis zu dieser Stunde reicht. Der letzte bekannte Rohzustand wird an dieser
  Grenze fortgeführt; Statistik-Buckets enden exakt davor. Besteht zwischen
  den beiden HA-Quellen selbst eine Lücke, wird nicht gerundet: die Grenze
  liegt dann exakt beim ersten verfügbaren Rohwert, damit Zeitarchiv diese
  Lücke nicht künstlich vergrößert. So erzeugt Zeitarchiv nie eine
  Überschneidung und vergrößert nie eine bestehende Lücke zwischen den
  Quellen.

- **Rohhistorie:** Einzelmesswerte über die Home-Assistant-REST-API. Home
  Assistant hält diese standardmäßig aber nur einige Tage vor, deckt also
  nur die jüngste Vergangenheit ab.
- **Langzeitstatistik:** von Home Assistant per Voreinstellung dauerhaft
  aufbewahrte Stunden-/Tagesaggregate (Mittelwert bzw. fortlaufende Summe,
  je nachdem was die Entität in Home Assistant führt) über die
  Home-Assistant-WebSocket-API — deckt damit auch deutlich ältere
  Zeiträume ab, allerdings nur als Aggregat statt als Einzelmesswert. Steht
  nur für Entitäten mit Home-Assistant-`state_class` zur Verfügung (i. d. R.
  `sensor.*`-Entitäten), erkennbar an der Markierung "Nicht unterstützt" in
  der Spalte "Art" bei allen anderen.

Ablauf: Importmodus und Zeitraum wählen (die verfügbaren Voreinstellungen unterscheiden
sich je nach Quelle — bei Langzeitstatistik steht z. B. zusätzlich "Letztes
Jahr" zur Verfügung), optional "Verfügbarkeit prüfen" für eine Vorschau,
welche Entitäten in Home Assistant tatsächlich Daten der gewählten Quelle
haben und für welchen Zeitraum. Geprüft werden dabei ausschließlich die
markierten Entitäten; unmarkierte Zeilen bleiben unverändert. Das
Prüfergebnis bleibt je Quelle/Auflösung erhalten — auch nach einem
Seitenwechsel oder einem Wechsel zwischen
Vollimport, Rohhistorie und Langzeitstatistik, bis zum nächsten Neustart des Add-ons.
Ein Status-Chip rechts neben "Verfügbarkeit prüfen" zeigt den laufenden
Prüfstatus und anschließend den Zeitpunkt der letzten Prüfung; ab 15 Minuten
erscheint ein Hinweis, dass der Stand veraltet sein könnte.

Beim Vollimport werden Roh- und Statistikzeitraum getrennt gewählt. Der Dry
Run weist je Entität beide verwendeten Bereiche, die berechnete Schnittstelle,
bewusst verworfene Übergangswerte und den fortgeführten Rohwert-Anker aus.
Entitäten ohne Langzeitstatistik werden weiterhin vollständig mit ihrer
verfügbaren Rohhistorie importiert; ein Ausfall einer Quelle verhindert nicht,
dass erfolgreich abgerufene Werte der anderen Quelle verarbeitet werden.

Der laufende Kalendermonat wird unabhängig vom bereits vorhandenen
Datenbestand immer automatisch in den Hot Buffer importiert. Ohne die Option
"Archivlücken füllen" wird ein bereits abgeschlossener Monat mit
vorhandener Archivdatei komplett übersprungen — wie bei Symcon und CSV
(siehe „Duplikatschutz" weiter unten). Erst mit aktivierter Option werden
solche Monate zeilenweise um fehlende Zeitstempel ergänzt; vorhandene
Zeitstempel und Werte bleiben dabei unverändert.

Nach einem Dry Run kann eine Debug-Datei als ZIP heruntergeladen werden. Sie
enthält alle für die Diagnose relevanten abgerufenen, übernommenen und
verworfenen Werte samt Gründen, Quellenbereichen und Schnittstelle,
Monatszuordnung, Importplan und aktuellem
Archiv-/Hot-Buffer-Zustand. Zugangstoken und Autorisierungsheader werden nicht
aufgenommen. Da der Export dennoch Messwerte und Entitätsmetadaten enthält,
sollte er nur gezielt weitergegeben werden.

Dieser Import benötigt die Add-on-Berechtigung `homeassistant_api` sowie
eine Home-Assistant-Installation mit Supervisor (steht bei Home Assistant
Container nicht zur Verfügung).

### Reports

Jeder tatsächlich ausgeführte Import (Symcon, CSV oder Home Assistant)
bleibt hier mit Quelle, Zuordnung, Laufzeit, importierten und
übersprungenen Datensätzen sowie eventuellen Fehlern nachvollziehbar. Reine
Vorschauen (z. B. "Verfügbarkeit prüfen") erzeugen keinen Report. Die Liste
lässt sich nach Quelle und Status filtern (wirkt sofort bei Auswahl) und
nach jeder Spalte sortieren; ein Klick auf eine Zeile öffnet die
Detailansicht mit JSON-Download. Reports sind seitenweise darstellbar und
lassen sich gesammelt löschen, wenn sie nicht mehr benötigt werden.
Home-Assistant-Reports unterscheiden beim Vollimport Rohhistorie und
Langzeitstatistik und weisen neu archivierte Werte, Ergänzungen des laufenden
Monats, gefüllte Archivlücken sowie aus einem unzulässigen aktuellen Archiv in
den Hot Buffer gerettete Werte getrennt aus.

### Duplikatschutz

Der laufende Kalendermonat landet immer im Hot Buffer und wird dabei
zeilenweise dedupliziert: nur Zeitstempel, die dort noch nicht vorhanden
sind, werden ergänzt.

Für bereits abgeschlossene Archivmonate hängt das Verhalten von der Quelle
ab. Bei **Symcon- und CSV-Import** gilt Monatsgranularität: existiert für
einen Monat bereits eine Archivdatei, wird der gesamte Monat übersprungen —
auch wenn er tatsächlich Lücken enthält. Eine nachträgliche Ergänzung ist
hier nicht möglich, dafür muss der Monat notfalls manuell gelöscht und neu
importiert werden. Beim **Home-Assistant-Import** lässt sich das mit der
Option "Archivlücken füllen" gezielt aufheben: dann werden auch
abgeschlossene Monate um fehlende Zeitstempel ergänzt.

In allen Fällen gilt: vorhandene Messpunkte derselben Entität und desselben
Zeitstempels werden übersprungen — auch bei abweichender Event-ID — und
niemals ersetzt. Ein erneuter Symcon- oder CSV-Upload derselben Quelle
dupliziert also nichts, ebenso wenig ein wiederholter Home-Assistant-Import
über denselben Zeitraum.

### CSV-Export

Von der Verlaufsansicht einer Entität aus: die vollständige
Rohdatenhistorie dieser einen Entität bis zum Exportlimit als CSV
herunterladen.

## Backup / Restore

Eigener Menüpunkt **System → Backup / Restore** (nicht unter Einstellungen):

- **Backup erstellen:** kompletter Datenbestand (Index, Hot Buffer,
  Monatsarchive, Rollups) als ZIP, direkt herunterladbar. Zusätzlich zu,
  nicht statt der automatischen Home-Assistant-Snapshots — ein
  Home-Assistant-Snapshot sichert den Add-on-Zustand als Ganzes, ein
  Zeitarchiv-Backup ist unabhängig davon portabel und lässt sich auch
  außerhalb von Home Assistant aufbewahren.
- **Zeitplan:** automatisch nach Zeitplan (Intervall, Uhrzeit, ggf.
  Wochentag), mit automatischer Aufräumung älterer Backups nach Anzahl
  und/oder Alter, damit der Speicherplatz nicht unbegrenzt wächst.
- **Prüfen:** Prüfsummen-Check eines vorhandenen Backups, ohne es
  anzuwenden — sinnvoll, um die Integrität eines Backups vor einem
  tatsächlichen Wiederherstellungsbedarf zu bestätigen.
- **Wiederherstellen:** ersetzt den aktuellen Datenbestand vollständig
  durch den Inhalt des gewählten Backups. Der bisherige Stand wird vor dem
  Überschreiben in ein Rollback-Verzeichnis verschoben, nicht gelöscht —
  bei Bedarf lässt sich der Zustand vor der Wiederherstellung also
  zurückholen. Nach einer Wiederherstellung empfiehlt sich ein kurzer Blick
  auf **Statistik**, um zu prüfen, ob die erwarteten Entitäten und
  Datensatzmengen wieder vorhanden sind.

## Einstellungen im Detail

| Bereich | Enthält |
| --- | --- |
| **Darstellung** | Farbschema (Zeitarchiv/Home Assistant/Modern), Hell/Dunkel/Automatisch, Schriftgröße, Dashboard-Kachel-Ein-/Ausblendanimation, Startwerte für die Chart-Optionen der Entität-Verlaufsansicht |
| **Archivierung** | Standardwerte für neu erkannte Entitäten (wirken nie rückwirkend auf bestehende Entitäten): Auflösung, Aufbewahrung, Nachkommastellen, Wertänderungsfilter, Lücken-/Ausreißer-Erkennung |
| **Meldungen** | Tipp-Anzeige an-/ausschalten und Dialog mit allen Tipps (siehe [Housekeeping](#housekeeping)); Übersicht stummgeschalteter Systemmeldungen mit verbleibender Dauer, einzeln vorzeitig wieder aktivierbar |
| **Verbindung** | API-Token anzeigen/neu erzeugen, letzter empfangener Wert, Anzahl Schreibzugriffe und Auth-Fehler seit Start |
| **Diagnose** | Nächsten Schreibvorgang einmalig vollständig aufzeichnen (sensible Rohdaten, automatische Löschung spätestens nach 60 Minuten); eine einzelne Entität 15 Minuten lang einschließlich Ingest-Ergebnis verfolgen; Diagnosebericht herunterladen; Prozess-Start und -Laufzeit; **Hintergrundprozesse**-Übersicht (letzter Lauf/Status jeder Wartungsplaner-Aufgabe) |
| **Über Zeitarchiv** | Version (mit Hinweis, sobald ein Update verfügbar ist), Zeitzone, Datenverzeichnis, Links zu Dokumentation/Changelog/Fehlermeldung |

Ein neu erzeugter API-Token unter **Verbindung** ersetzt den bisherigen
sofort — die Zeitarchiv-Integration muss danach mit dem neuen Token
aktualisiert werden, sonst schlagen weitere Schreibversuche fehl.

Anwendungs-Loglevel, HTTP-Zugriffsprotokollierung und die Logansicht selbst
sind keine Einstellungen-Sektion mehr, sondern liegen direkt auf der Seite
**Protokoll** (Home-Assistant-Seitenleiste bzw. Menü).

Für den Normalbetrieb sind `warning` und HTTP **Nur fehlgeschlagene Anfragen**
die empfohlenen Einstellungen. `debug` und der Entity-Trace sind zeitlich
begrenzt zur Fehlersuche gedacht. Die lokale Logquelle reagiert schnell und
enthält nur den begrenzten Puffer des laufenden Prozesses; die
Supervisor-Historie reicht weiter zurück und kann beim Laden etwas länger
dauern. Zugangsdaten werden vor der Ausgabe maskiert. Write-Captures und
Entity-Traces können trotzdem Entity-IDs und Messwerte enthalten und sollten
nur so lange wie nötig aktiv beziehungsweise gespeichert bleiben.

## Typische Aufgaben

**"Ein Sensor sendet unplausible Ausreißer."**
→ Entität öffnen → Zahnrad-Symbol → Ausreißer-Erkennung auf einen
passenden Prozentsatz einstellen → zurück zur Verlaufsansicht →
**Bereinigen** → erkannte Ausreißer prüfen und löschen (Soft-Delete,
rückgängig machbar) → **Housekeeping → Speicherplatz**, wenn der Platz
tatsächlich freigegeben werden soll.

**"Ich will Innen- und Außentemperatur über die letzten 12 Monate
vergleichen."**
→ **Tabellen** → neue Tabelle → 12 Spalten (Zeitraum-Typ "Monat", Versatz 0
bis −11) → zwei Zeilen (je eine Entität) → optional eine Formel-Zeile für
die Differenz.

**"Ein Dashboard auf einem Wandtablet soll sich nicht versehentlich
verändern."**
→ Dashboard öffnen → Editor → "Fixiert" aktivieren.

**"Ich möchte alte Symcon-Daten übernehmen, ohne HA-Live-Daten zu
verdoppeln."**
→ **Import → Symcon** → ZIP hochladen → Zuordnung prüfen → Import starten.
Bereits vorhandene Zeitstempel werden automatisch übersprungen, unabhängig
von der Quelle.

**"Ich nutze kein Symcon und möchte trotzdem die bisherige HA-Historie
übernehmen."**
→ **Import → Home Assistant** → Entitäten auswählen, optional
"Verfügbarkeit prüfen" → Vorschau (Dry Run) → Import starten.

**"Eine Entität sendet nicht mehr, ich will sie aber behalten."**
→ Entität einfach unverändert lassen — bereits archivierte Werte bleiben
erhalten, Charts und Tabellen zeigen weiterhin die vorhandene Historie.
Erst bei Bedarf über das Zahnrad-Symbol **Alle Werte löschen** oder
**Entität entfernen** verwenden.

**"Ich will vor einem größeren Eingriff (Import, Bereinigung, Update) auf
Nummer sicher gehen."**
→ **System → Backup / Restore** → Backup erstellen → herunterladen oder im
konfigurierten Zeitplan belassen.

## Häufige Fragen

**Wirkt sich eine geänderte Auflösung auf bereits gespeicherte Werte aus?**
Nein. Eine geänderte Auflösung wirkt ausschließlich auf künftig
eintreffende Werte, nie rückwirkend auf bereits archivierte Daten.

**Wirkt sich eine geänderte Aufbewahrungsfrist auf bereits gespeicherte
Werte aus?**
Ja, sobald die Aufbewahrungs-Durchsetzung als Nächstes läuft — anders als
bei der Auflösung ist das hier bewusst rückwirkend gewollt: eine verkürzte
Frist entfernt dann auch längst archivierte, überfällige Monate. Details
und wie man das sicher handhabt (Vorschau, Unbegrenzt, Backup vorher)
stehen unter [Datenhandling → Aufbewahrung](#aufbewahrung-retention).

**Ist eine gelöschte Entität wirklich weg?**
Nach "Alle Werte löschen" oder "Entität entfernen" ja, endgültig. Vorher
lohnt sich ein Backup (siehe oben), falls die Löschung ein Versehen war.

**Warum sieht ein Chart trotz aktiver Integration keine neuen Werte?**
Meist fehlt ein passender Archivfilter in der Integration (siehe
[Erste Schritte](#erste-schritte), Schritt 4), oder Token/Host in der
Integrationskonfiguration stimmen nicht mit **Einstellungen → Verbindung**
überein.

**Kann ich ein Chart oder eine Tabelle für mehrere Dashboards
verwenden?**
Ja — ein und dasselbe Chart oder dieselbe Tabelle lässt sich auf beliebig
vielen Dashboards anheften; es gibt jeweils nur eine gemeinsame
Definition, Änderungen wirken sich überall gleichzeitig aus.

**Was passiert mit einer Kachel, wenn das zugrunde liegende Chart oder
die Tabelle gelöscht wird?**
Die Kachel verschwindet von allen Dashboards, auf denen sie angeheftet
war.
