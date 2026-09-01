# Changelog

## 0.71.0 - 2026-09-01

### Neu

- **Energiedashboard**: neue, eigenständige Sankey-Energiefluss-Seite
  (aktivierbar über eine Kachel auf der Dashboard-Übersicht) mit
  Stunde-/Tag-/Monat-/Jahr-Navigation. Rollen-Setup für Netzbezug,
  Einspeisung, beliebig viele Erzeuger, einen Speicher und Verbraucher.
  KPI-Kacheln sowie Autarkie-, Eigenverbrauchs-, Speicher-SOC- und
  Wirkungsgrad-Ringe mit anklickbarem Monatstrend über die letzten drei
  Jahre; Kosten- und CO₂-Bilanz (mit optionalem Festpreis-Fallback ohne
  passende Entität), PV-Ertragsprognose, Datenqualitäts-Check
  (Bilanzprüfung, veraltete Werte) und ein Tageslastprofil der letzten
  7 Tage.
- Demo-Daten-Generator: 13 neue Entitäten für ein Balkonkraftwerk mit
  2-kWh-Speicher (PV-Leistung, Lade-/Entladeleistung, SoC, Speicherstand,
  Ertrags-/Lade-/Entlade-Zähler mit Tages-Reset-Variante, Online-Status)
  sowie eine Netz-CO2-Intensität und eine PV-Ertragsprognose für die
  Dachanlage — mindert, wie ein reales Zweitsystem, den simulierten
  Netzbezug unabhängig von der bestehenden Dachanlage. Neue eigenständige
  Aktion `--clear {values,entities}` zum gezielten Löschen aller
  Demo-Entitäten (Werte oder komplett inkl. Konfiguration).

### Verbesserung

- „Zurück"-Links in Chart-, Dashboard-, Tabellen- und
  Report-Detailansichten nennen jetzt das Ziel (z. B. „zurück zu
  Charts") statt eines generischen „Zurück".

### Dokumentation

- Benutzerhandbuch und README beschreiben das neue Energiedashboard.

## 0.70.0 - 2026-08-31

### Neu

- Zeilen-Menü "Optionen" für Vergleichstabellen: % Anteil an der Summe des
  Abschnitts, Zeile bei 0/keinem Wert automatisch ausblenden, sowie ein
  eigener Summenzeilen-Typ (Summe/Durchschnitt seit der letzten Trennlinie,
  aktualisiert sich automatisch).
- Farbskala je Spalte färbt Entität-/Gruppen-Zeilen relativ zu den anderen
  Zeilen desselben Abschnitts ein; Formel- und Summenzeilen bleiben davon
  unberührt.
- Mehrstufige Kopfzeile: Spalten mit gleicher Gruppen-Beschriftung (z. B.
  "2025" über mehreren Monatsspalten) bekommen automatisch eine
  übergreifende Kopfzeile.
- Spalten und Zeilen lassen sich einzeln duplizieren.
- CSV-Export für Vergleichstabellen.
- Entity-Link in der HA-Import-Trefferliste, wie in der Entitätenliste.
- "Gleicher Zeitpunkt"-Vergleich: eine vergangene Vergleichsspalte (Vortag,
  Vormonat, Vorjahr …) wird automatisch auf denselben, bislang verstrichenen
  Zeitanteil gekappt, wenn eine Spalte desselben Zeitraum-Typs mit Versatz 0
  danebensteht — ein noch laufender Tag vergleicht sich so fair gegen
  "Vortag bis zur aktuellen Uhrzeit" statt gegen den ganzen Vortag.
- Erste Spalte und Kopfzeile lassen sich beim Scrollen fixieren
  ("Erste Spalte fixieren", "Header fixieren").
- Spaltenbreiten (inkl. Beschriftungsspalte) lassen sich per Ziehgriff am
  rechten Zellrand anpassen und werden gespeichert; ohne manuelle Breite
  richtet sich eine Spalte weiterhin nach ihrem Inhalt.
- Neue Layout-Optionen: Ausrichtung von Kopfzeile und Werte-Zellen
  (linksbündig/zentriert/rechtsbündig) sowie "Spalten gleichmäßig" für
  gleich breite Werte-Spalten, unabhängig von der Beschriftungsspalte.

### Verbesserung

- Die bisherigen Einzel-Chips einer Zeile (Fett, Überschrift zeigen,
  Hervorheben, % Anteil, Bei 0 ausblenden) sind in ein gemeinsames
  "Optionen"-Menü gewandert; der Menü-Button hebt sich hervor, sobald eine
  der enthaltenen Optionen aktiv ist.
- Alle Tooltips der Vergleichstabellen-Ansicht folgen jetzt einheitlich dem
  App-weiten Tooltip-Standard statt der nativen Browser-Tooltips.
- Spaltenüberschriften sind standardmäßig zentriert bzw. über die neue
  Ausrichtungs-Option konfigurierbar.

### Fehlerbehebung

- Bei fixierter Kopfzeile UND fixierter erster Spalte blieb die Eck-Zelle
  (Kopfzeile × Beschriftungsspalte) in Safari beim Scrollen nicht stehen —
  `position:sticky` auf `<thead>`/`<tr>` wird dort nicht zuverlässig
  unterstützt. Jetzt `position:sticky` auf jeder Kopfzelle einzeln, mit
  gemessenem Versatz für eine zweite (Gruppen-)Kopfzeile.
- "Header hervorheben" zusammen mit "Header fixieren" ergab einen weißen,
  textlosen Header (beide CSS-Regeln hatten dieselbe Spezifität, die
  spätere weiße Sticky-Hintergrundfarbe gewann).
- Dashboard-Kacheln von Vergleichstabellen kürzten Zeilen/Spalten auf eine
  von der Kachelgröße abhängige Obergrenze mit "+N weitere Zeile"-Hinweis,
  obwohl die Kachel ohnehin einen eigenen Scrollbalken hat — zeigt jetzt
  immer alle Zeilen/Spalten.

### Dokumentation

- Benutzerhandbuch und Frontend-Architektur beschreiben die neuen
  Vergleichstabellen-Optionen, den "Gleicher Zeitpunkt"-Vergleich sowie die
  Sticky-Header- und Gleichmäßig-Spalten-Lösung.

## 0.69.0 - 2026-08-31

### Neu

- Geöffnete Charts und Vergleichstabellen zeigen, auf welchen Dashboards sie
  verwendet werden. Ein einzelnes Dashboard ist direkt verlinkt; bei mehreren
  öffnet der Zähler eine kompakte, ebenfalls verlinkte Liste.
- Die Lücken-Erkennung bietet zusätzlich Schwellen von 6 und 12 Stunden.

### Verbesserung

- Größere Vergleichstabellen laden alle benötigten Entitäten und Zeiträume in
  einer gemeinsamen Batch-Abfrage. Quelldateien werden dabei je Anfrage nur
  einmal gelesen und an den Browser gehen nur die benötigten Aggregatwerte;
  Dashboard-Kacheln berechnen außerdem nur ihren sichtbaren Ausschnitt.
- Das Standard-Dashboard bleibt in der Dashboard-Übersicht immer an erster
  Stelle — unabhängig von Sortierung und dem Schalter „Favoriten zuerst“.
- Der Hilfetext zu Nachkommastellen erklärt „Automatisch“ und feste
  Stellenzahlen jetzt mit konkreten Beispielen.
- Im präzisen Dashboard-Modus werden Kacheln auf schmalen Displays zuverlässig
  einspaltig dargestellt.

### Dokumentation

- Benutzerhandbuch, Frontend-Architektur und API-Referenz beschreiben die neue
  Verwendungsanzeige, Tabellen-Batch-Abfrage und Speicheroptimierungen.

## 0.68.0 - 2026-08-31

### Neu

- Die Übersichten von Dashboards, Charts und Tabellen haben ein Suchfeld und
  eine wählbare Sortierung (neueste, älteste, Name auf- oder absteigend).
  „Favoriten zuerst“ ist dabei ein eigener Schalter und lässt sich mit jeder
  Sortierung kombinieren. Beides wird je Ansicht im Browser gemerkt.
- Chart-Kacheln zeigen zusätzlich den Diagrammtyp. Enthält ein Chart Linien-
  und Balkenreihen zugleich, werden beide genannt.
- Die Sparkline einer Werte-Kachel lässt sich zusätzlich auf einen Punkt je
  15 Minuten verdichten.
- Der Demo-Datengenerator kann mit `--append` eine bestehende Demo-Instanz um
  die Werte seit dem letzten Lauf ergänzen, statt die komplette Historie neu
  zu erzeugen. Regelmäßig ausgeführt bleibt eine Demo-Instanz dadurch aktuell,
  ohne bei jedem Lauf Monate neu zu berechnen.

### Verbesserung

- Vergleichstabellen laden ihre Spalten parallel statt nacheinander. Die
  Ladezeit hängt dadurch kaum noch von der Spaltenanzahl ab.
- Dashboards, Charts und Tabellen führen jeweils eindeutige Namen: Groß- und
  Kleinschreibung sowie Randleerzeichen bleiben dabei unberücksichtigt.
  Namen sind auf 50 Zeichen begrenzt. Kopien zählen selbstständig hoch
  („(Kopie)“, „(Kopie 2)“ …).
- Die Kacheln der drei Übersichten sind kompakter: „Ansehen“ und „Öffnen“
  entfallen, weil die gesamte Kachel den Eintrag öffnet, und „Bearbeiten“ ist
  ins Kachelmenü gewandert. Alle Kacheln sind gleich hoch und bieten Platz für
  den längsten erlaubten Namen; die Angaben darunter stehen dadurch über alle
  Kacheln hinweg auf einer Linie.
- Chart-Optionen zeigen nur noch, was für die aktuelle Darstellung Wirkung
  hat: „Punkte“ und „Rohwerte“ bei Linien, „Werte anzeigen“ bei Balken.
  Bisher waren diese Schalter dauerhaft sichtbar und lediglich deaktiviert.
- Meldungen und Rückfragen erscheinen durchgängig im App-Look statt als
  Browserdialog mit vorangestellter Serveradresse. Die Index-Optimierung
  fragt vor dem Start nach und meldet den Fortschritt am Schaltknopf.
- Im Präzisen Modus fügt sich die Kachel „+“ in das feinere Raster ein, statt
  über ihre Zeile hinauszuragen.

### Fehlerbehebung

- Auf den Übersichten ließen sich mehrere Kachelmenüs gleichzeitig öffnen,
  und ein geöffnetes Menü wurde von der Kachel darunter überdeckt.

## 0.67.0 - 2026-08-30

### Neu

- Die Indexdetailseite zeigt vollständig freie, reclaimbare SQLite-Seiten,
  eine konservative Optimierungsempfehlung und die geschätzte Dateigröße
  nach einer Kompaktierung. Eine manuelle Aktion führt ein abgesichertes
  `VACUUM` mit Schreibsperre, Speicherplatzprüfung und anschließender
  Integritätsprüfung aus.
- Empfiehlt Zeitarchiv eine Kompaktierung, wird der Index zusätzlich in der
  Speichernutzung der Statistik entsprechend markiert.

### Verbesserung

- Das Farbschema „Modern“ verwendet jetzt kühle Slate-Flächen, Cobalt für
  Navigation und aktive Bedienelemente sowie Teal als Daten- und
  Diagrammakzent. Hell- und Dunkelmodus, Statusfarben, Schatten und die
  vollständige Chart-Palette wurden auf bessere Trennung und Kontraste
  abgestimmt.

## 0.66.0 - 2026-08-30

### Neu

- Die Speichernutzung des SQLite-Index ist aus der Statistik heraus bis auf
  einzelne Fachtabellen aufgeschlüsselt. Datenseiten, zugehörige Indizes und
  Gesamtgröße werden je Tabelle und fachlichem Bereich getrennt ausgewiesen.

### Verbesserung

- Die Home-Assistant-Verfügbarkeitsprüfung fragt nur noch markierte Entitäten
  ab. Ohne Auswahl bleibt die Aktion deaktiviert; unmarkierte Ergebnisse
  bleiben unverändert.
- Die Verfügbarkeitsanzeige verwendet hinter „Roh“ und „Statistik“ einheitlich
  „Werte“ und erhält durch angepasste Spaltenbreiten mehr Platz.
- Home-Assistant-Import-Reports bilden den Vollimport vollständig ab und
  unterscheiden neu archivierte Werte, den laufenden Monat, gefüllte
  Archivlücken und in den Hot Buffer gerettete Werte.
- Die Index-Größenanalyse nutzt `dbstat` direkt und fällt bei abweichenden
  Python-SQLite-Builds auf eine read-only Abfrage über das mitgelieferte
  SQLite-Werkzeug zurück, ohne Speichergrößen zu schätzen.

## 0.65.0 - 2026-08-30

### Neu

- Die Sparkline-Auflösung von Werte-Kacheln ist pro Kachel als Rohdaten,
  5 Minuten, 30 Minuten oder 1 Stunde konfigurierbar.

### Verbesserung

- Neue Werte-Kacheln öffnen direkt ihre Konfiguration und zeigen die
  Sparkline standardmäßig an. Entität, Sparkline-Auflösung, letzte
  Aktualisierung, Nachkommastellen und Titel lassen sich dort bearbeiten.
- Titel, Wertezeile, Einheit und letzte Aktualisierung nutzen den verfügbaren
  Kachelraum ausgewogener und bleiben auch bei kleinen Kacheln lesbar.
- Chart-Optionen zeigen die Nachkommastellen ohne Umbruch in einer eigenen,
  zentrierten zweiten Zeile.
- Statistik: Das Wachstumsdiagramm verwendet dynamische Y-Achsen; sämtliche
  Tabellen sind nach demselben Muster wie die Entitätenliste sortierbar.
- Der Home-Assistant-Import bietet eine durchsuchbare Entitätenauswahl und
  orientiert Suchfeld, Verfügbarkeitsprüfung und Statusanzeige am App-Standard.
  Die Option heißt nun „Archivlücken füllen“ und erklärt ihre Wirkung in
  einer eigenen kompakten Hilfe.
- Tooltips erscheinen beim Überfahren nach 600 ms statt nach 450 ms. Per
  Tastatur fokussierte Hinweise bleiben ohne Verzögerung zugänglich.

### Fehlerbehebung

- Die Dashboard-Datenbankmigration erhält die Einstellung zur
  Legendendarstellung vorhandener Kacheln.

## 0.64.0 - 2026-08-29

### Neu

- Der Home-Assistant-Dry-Run bietet einen Debug-Download als ZIP. Die Datei
  enthält die abgerufenen, übernommenen und verworfenen Werte samt Gründen,
  Monatszuordnung, Importplan und aktuellem Archiv-/Hot-Buffer-Zustand, aber
  keine Zugangstoken oder Autorisierungsheader.

### Verbesserung

- Suchfeld und Schaltfläche der Verfügbarkeitsprüfung entsprechen wieder den
  kompakten Standardmaßen der App. Der laufende Zustand und der Zeitstempel
  der letzten Prüfung stehen in einem dynamisch breiten Status-Chip direkt
  neben der Schaltfläche.
- Die Option zum Ergänzen bestehender Daten heißt nun präziser
  "Archivierte Monate ergänzen". Sie ist nur für echte Lücken in bereits
  abgeschlossenen Monatsarchiven erforderlich; der laufende Monat wird immer
  automatisch in den Hot Buffer importiert.
- Langzeitstatistik-Abfragen entfernen doppelte Werte an Chunk-Grenzen und
  sämtliche Home-Assistant-Importpfade verwerfen nicht endliche Zahlen
  (`NaN`/`Inf`) nachvollziehbar.

### Fehlerbehebung

- Daten des laufenden Kalendermonats landen wieder zuverlässig im Hot Buffer,
  auch wenn bereits frühere Monatsarchive vorhanden sind.
- Irrtümlich angelegte Archive des laufenden Monats werden beim Import
  verlustfrei in den Hot Buffer zurückgeführt und anschließend entfernt.
- Beim Ergänzen und Reparieren von Archiven bleiben Zusatzspalten wie die
  Event-ID erhalten; vorhandene Zeitstempel und Werte werden nicht ersetzt.

## 0.63.0 - 2026-08-29

### Neu

- Home-Assistant-Import: Bereits archivierte Monate können jetzt optional
  ergänzt werden. Dabei werden ausschließlich noch fehlende Zeitstempel
  übernommen; vorhandene Werte bleiben unverändert.
- Dry Run und Importergebnis weisen ergänzte Bestandsmonate und die Anzahl
  der neu übernommenen Zeilen separat aus.

### Verbesserung

- Nach dem Ergänzen archivierter Monate werden die Rollups der betroffenen
  Entität vollständig neu aufgebaut, damit insbesondere Zählergrenzen und
  nachfolgende Monatswerte konsistent bleiben.

## 0.62.0 - 2026-08-29

### Neu

- Werte-Kachel: pinnt den aktuellen Wert einer Entität direkt aufs
  Dashboard (mit Sparkline, Altersanzeige und eigenem Nachkommastellen-/
  Titel-Override), inklusive eines größeren Einstellungs-Popups dafür.
- Dashboards: Favoriten, Duplizieren, wählbares Standard-Dashboard,
  Präziser Modus (6 statt 3 Spalten) und "Lücken auffüllen".
- Charts und Tabellen lassen sich jetzt duplizieren; "Kachel hinzufügen"
  zeigt Charts/Tabellen/Werte-Kacheln übersichtlich in Registerkarten
  statt einer langen Liste.
- Nachkommastellen-Override jetzt auch im Chart-Editor und in der
  Entität-eigenen Verlaufsansicht einstellbar.
- Vergleichstabellen: Platzhalter-Variablen (Jahr, Monat, Quartal, Woche …)
  für Spaltenbeschriftungen mit Einfüge-Hilfe, sowie ein schaltjahrsicherer
  Vorjahresvergleich für Spalten.

### Fehlerbehebung

- Dashboard-Kacheln eines Charts übernahmen "Werte anzeigen" und die
  Nachkommastellen-Einstellung bislang nicht vom gespeicherten Chart.
- Diverse kleinere Layout- und Bedienungskorrekturen an Dashboard-Kacheln
  und deren Menüs.

## 0.61.0 - 2026-08-29

### Neu

- Zeitstrahl (An/Aus-Verlauf als durchgehende Balken statt einzelner Punkte)
  jetzt auch im Chart-Editor für mehrere Schalter-Entitäten gleichzeitig
  nutzbar, mit korrekt als Dauer (statt Rohsekunden) ausgewiesener
  Einschaltdauer-Summe.
- Neue Auflösung "Tag" für Zeitraum "Tag": fasst den ganzen Tag zu einem
  einzigen Balken je Entität zusammen — praktisch, um z. B. Tages-Einspeisung
  und -Bezug direkt nebeneinander zu vergleichen.
- Farbschema "Modern" überarbeitet: bessere Kontraste, klarere Chart-Farbpalette.

### Verbesserung

- Diagramm-Achsen zeigen durchgängig die korrekte Anzahl an Einteilungen
  (kein zusätzlicher Phantom-Tick mehr bei Woche/Monat); Balken am Rand
  werden nicht mehr abgeschnitten.
- Dashboard-Kacheln übernehmen jetzt zuverlässig den gespeicherten
  Diagrammtyp (z. B. Zeitstrahl statt fälschlich Balken).

### Fehlerbehebung

- Zeitstrahl-Auswahl im Chart-Editor ging beim Speichern verloren.
- Die Auflösungs-Auswahl im Chart-Editor zeigte beim erneuten Öffnen zum
  Bearbeiten teils "Automatisch" statt der tatsächlich gespeicherten
  Auflösung an, obwohl der Chart selbst korrekt gerendert wurde.
- Vereinzelte, sich über die Zeit ansammelnde Tooltip-Reste auf
  Dashboard-Kacheln nach mehrfachem Anheften/Umsortieren.

## 0.60.0 - 2026-08-28

### Verbesserung

- Tooltips zeigen jetzt überall einheitlich mit kurzer Verzögerung statt
  teils sofort.
- Optionen-Menüs (Entität, Chart) kompakter; Vergleichen-Menü schmaler.
- Dashboards-Übersicht: 3 statt 4 Kacheln pro Zeile.
- README und Benutzerhandbuch überarbeitet.

## 0.59.0 - 2026-08-28

### Neu

- Automatische Aufbewahrungs-Durchsetzung kann jetzt auch wöchentlich
  statt nur täglich laufen (mit Wochentag-Auswahl).
- Mehrere Tabellen in den Einstellungen und der Statistik (Bereinigungs-
  Vorschau, Indexkonsistenz, Ausführungsverläufe, Duplikate je Entität,
  Symcon-Zuordnungsbericht) sind jetzt sortierbar und blättern bei vielen
  Zeilen automatisch um.

### Verbesserung

- Allgemeine Überarbeitung der Einstellungen-Seite: klarere optische
  Trennung der Bereiche, einheitlichere Auswahl-Listen und Suchfelder,
  aufgeräumtere Diagnose- und Verbindungs-Ansicht.
- Durchsuchbares Entitäten-Dropdown (aus „Entität verfolgen“) jetzt auch
  im Tabellen-Editor nutzbar; Symcon-Import zeigt bei der HA-Zuordnung
  eine durchsuchbare, scrollbare Liste statt der nativen Browser-Vorschläge.
- Charts- und Tabellen-Übersicht: kompakteres Kachel-Raster, Chart-Kacheln
  zeigen keine rohen Entitäts-IDs mehr.

### Fehlerbehebung

- Einstellungen-Seite ließ sich unter bestimmten Bedingungen nicht mehr
  scrollen.
- Tabellen-Editor zeigte die Formel-Buchstaben-Spalte teils auch außerhalb
  des Bearbeiten-Modus an.

## 0.58.0 - 2026-08-28

### Verbesserung

- Einstellungen → Darstellung, Archivierung, Aufbewahrung, Protokollierung
  und Diagnose (Prozess) nutzen jetzt durchgängig dieselbe kompakte
  Zeilen-Liste wie bisher schon „Chart-Optionen“, mit kurzem Erklärtext zu
  jeder Option statt freistehender Felder; auf schmalen Bildschirmen
  bricht die Auswahl jetzt in eine eigene Zeile um.
- Einstellungen → Schriftgröße: die Dropdown-Optionen (Kleiner … Größer)
  zeigen jetzt eine echte Vorschau in der jeweiligen Schriftgröße statt
  einheitlicher Schrift.
- Diagnose → „Entität verfolgen“: Freitextfeld durch ein durchsuchbares
  Entitäten-Dropdown ersetzt (Feld heißt jetzt „Entität“ statt
  „Entity-ID“), mit Leeren-Button und Tooltip zur Entity-ID — sowohl in
  der Liste als auch am ausgewählten Feld.
- Verbindungsstatus (Einstellungen → Verbindung): Hinweisbox zu „Zähler
  seit App-Neustart“/„Bei Auth-Fehlern“ entfernt.

### Fehlerbehebung

- `/favicon.ico` lieferte bisher 404 und erzeugte dadurch Log-Spam;
  liefert jetzt das Addon-Icon aus.

## 0.57.0 - 2026-08-28

### Neu

- Chart-Optionen (Kontinuierlich, Rohwerte, Diagrammtyp, Punkte, Werte
  anzeigen, Dynamische Y-Achse, Statistik in Legende, Legenden-Kennzahlen,
  Legenden-Stil) werden jetzt pro Entität dauerhaft gespeichert, statt bei
  jedem Seitenaufruf auf die Werkseinstellung zurückzufallen. Unter
  Einstellungen → Darstellung lassen sich die globalen Startwerte dafür
  festlegen; im Optionen-Menü der Entität-Seite gibt es „Optionen auf
  Standard zurücksetzen“.
- Neue Legendenkennzahl „Aktuell“ (letzter Wert) in Chart-Legenden;
  „Letzter“ heißt jetzt „Aktuell“.
- Neuer Legenden-Stil „Tabelle“ als Alternative zu den Chip-Legenden, in
  der Entität-Chart-Seite, im Chart-Editor und auf Dashboard-Kacheln.
  Tabellen- wie Chip-Legenden lassen sich jetzt beide per Klick auf eine
  Zeile/einen Chip ein- und ausblenden.
- Dashboard-Kacheln (ab Größe 2×2) können jetzt optional eine Legende
  anzeigen („Legende anzeigen“ im Kachel-Menü) — Aussehen und Inhalt
  entsprechen dabei exakt der Legende des zugrundeliegenden Charts.
- Option „Werte anzeigen“ (Datenwerte direkt über Balken/Punkten, ohne
  Einheit) für die Entität-Chart-Seite und den Chart-Editor.

### Verbesserung

- Chart-Tooltips (Entität-Seite, Chart-Editor, Dashboard-Kacheln) zeigen
  das Datum jetzt einheitlich oben an; teilen sich mehrere Reihen denselben
  Zeitpunkt, erscheint das Datum nur noch einmal statt pro Zeile.
- Chart-Tooltip-Datumsformat richtet sich jetzt nach der tatsächlich
  angezeigten Bucket-Auflösung (Tag/Woche/Monat/Jahr) statt nach dem Namen
  des gewählten Zeitraums; bei Tages-Buckets erscheint zusätzlich die
  Wochentagsabkürzung; Sekunden entfallen bei untertägigen Zeitstempeln.
- Home-Assistant-Import: Bedienelemente (Rohhistorie/Langzeitstatistik,
  Zeitraum, „Verfügbarkeit erneut prüfen“, Zeitpunkt/Frische-Hinweis) in
  eine eigene Zeile unter dem Hinweistext gruppiert; „Wiederholungen
  verdichten“/„Duplikate automatisch entfernen“ werden nur noch bei
  passendem aktivem Filter eingeblendet statt nur ausgegraut; „Schnitt“
  heißt jetzt „Durchschnitt“.
- „Chart-Optionen (Standardwerte)“ unter Einstellungen → Darstellung
  kompakter und einheitlicher zur restlichen App gestaltet; die globale
  Diagrammtyp-Vorgabe entfällt (bleibt immer „Automatisch“, die
  Übersteuerung je Entität ist davon unberührt); „Ø“ heißt dort jetzt
  ausgeschrieben „Durchschnitt“.
- Markierte-Datensätze-Dialog (Einstellungen) nutzt jetzt dieselbe
  Pagination wie der Rest der App (Erste/Letzte Seite, editierbare
  Seitenzahl, Seitengröße wählbar).

### Fehlerbehebung

- Chart-Editor: Tooltip-Datumsformat berücksichtigte bei aktivem manuellem
  Auflösungs-Preset die unresamplten statt der tatsächlich angezeigten
  Daten und formatierte dadurch falsch (z. B. Uhrzeit statt Wochentag/Datum
  bei Tages-Buckets).
- Entität-Chart-Seite: Farbe des Legenden-Punkts entsprach nicht immer der
  tatsächlichen Balken-/Linienfarbe.
- Dashboard-Kachel-Legende: Stil-Änderung wurde nicht gespeichert; die
  Legende diente außerdem versehentlich als Link zur Chart-Seite statt zum
  Ein-/Ausblenden von Reihen; die Tabellen-Legende füllte nicht die volle
  Kachelbreite; der Tooltip wurde vom `overflow:hidden` der Kachel
  abgeschnitten.
- Dashboard-Detailseiten zeigten bei Bestätigungsabfragen den nativen,
  ungestylten Browser-Dialog statt des app-eigenen Dialogs
  (fehlendes Skript-Include).

## 0.56.0 - 2026-08-27

### Neu

- Home-Assistant-Import: markierte Zeilen werden jetzt wie beim
  Symcon-Import farblich hervorgehoben und an den Tabellenanfang verschoben
  — bleibt eine aktive Spaltensortierung dabei erhalten, gilt sie nur noch
  innerhalb der markierten bzw. unmarkierten Gruppe.

## 0.55.2 - 2026-08-27

### Verbesserung

- Home-Assistant-Import, Spalte „Verfügbar“: Zeitpunkt der letzten Prüfung
  steht jetzt als eigener, farblich hervorgehobener Chip in Klammern direkt
  hinter dem Spaltentitel (vorher brach er wegen `th a{display:block}` auf
  eine eigene Zeile um). Das Prüfergebnis je Zeile steht jetzt auf zwei
  Zeilen (Zeitraum, dann Anzahl) statt einem zusammengesetzten Text; „…
  Punkte“ heißt jetzt „… Werte“.

## 0.55.1 - 2026-08-27

### Fehlerbehebung

- Home-Assistant-Import: „Verfügbarkeit prüfen“ für Langzeitstatistik brach
  mit HTTP 500 ab (`AttributeError: 'list' object has no attribute
  'values'`). Ursache: die reine Debug-Protokollierung in
  `ha_statistics._ws_call()` nahm für jede WebSocket-Antwort dieselbe
  `result`-Form an (dict) — `recorder/list_statistic_ids` liefert sie aber
  als Liste. Live auf einer echten Home-Assistant-Instanz gefunden.

## 0.55.0 - 2026-08-27

### Neu

- Home-Assistant-Import: eine geprüfte Verfügbarkeit übersteht jetzt einen
  Seitenwechsel/Neuladen sowie einen Wechsel zwischen Rohhistorie und
  Langzeitstatistik (je Quelle/Auflösung getrennt gemerkt, bis zum nächsten
  Add-on-Neustart). Der Spaltenkopf „Verfügbar“ zeigt dazu den Zeitpunkt der
  letzten Prüfung; ist dieser älter als 15 Minuten, erscheint der Hinweis
  „⏳ Nicht mehr taufrisch“.

## 0.54.0 - 2026-08-27

### Neu

- Home-Assistant-Import: neue Quelle „Langzeitstatistik" neben der
  bisherigen Rohhistorie. Home Assistant bewahrt Langzeitstatistik
  (Stunden-/Tagesaggregate, Mittelwert bzw. fortlaufende Summe je nach
  Entität) im Gegensatz zur Rohhistorie standardmäßig dauerhaft auf — damit
  lassen sich jetzt auch deutlich ältere Zeiträume importieren, für die HA
  keine Einzelmesswerte mehr vorhält. Umschalter „Rohhistorie“/
  „Langzeitstatistik“ oberhalb der Auswahltabelle, dazu eine Auflösung
  (Stunden-/Tageswerte) und eigene Zeitraum-Voreinstellungen (u. a.
  „Letztes Jahr“). Entitäten ohne Home-Assistant-`state_class` (i. d. R.
  außerhalb von `sensor.*`) führen keine Langzeitstatistik — als „Nicht
  unterstützt“ in der Spalte „Art“ erkennbar. Nutzt dafür erstmals die
  Home-Assistant-WebSocket- statt der REST-API
  (`recorder/statistics_during_period`).

## 0.53.0 - 2026-08-27

### Neu

- Home-Assistant-Import: Spalte „Entität" schmaler, Spalten „Art" und
  „Verfügbar" entsprechend breiter (mehr Platz für deren i. d. R. längeren
  Inhalt).

### Intern

- Deutlich mehr Protokollierung für die Fehlersuche, insbesondere beim
  Home-Assistant-Import:
  - **DEBUG:** jede einzelne HTTP-Anfrage an die Home-Assistant-API
    (Pfad, Anzahl gefilterter Entitäten, Zeitfenster) sowie deren Antwort
    (Anzahl Arrays/Einträge) — vorher auch mit aktiviertem Debug-Loglevel
    nicht sichtbar. Zusammenfassung jedes Home-Assistant-Dry-Runs (geplante
    Entitäten/Zeilen).
  - **WARNING:** ein Abruf, bei dem ein großer Anteil der Punkte als nicht
    importierbar übersprungen wird (Home-Assistant- und CSV-Import); ein
    abgeschlossener Home-Assistant-Import ohne Fehler, der aber 0 Zeilen
    geschrieben hat; eine Symcon-Variable, deren Ziel-Entität beim Import
    nicht gefunden wurde (bisher nur im UI-Report sichtbar, nie im
    Add-on-Log).

## 0.52.0 - 2026-08-27

### Neu

- Home-Assistant-Import: Auswahltabelle überarbeitet — Entität kürzt lange
  Namen/IDs jetzt mit Tooltip statt in die Nachbarspalte zu ragen, Spalte
  „Typ“ (Standard/Zähler/Schalter) schmaler und zentriert wie in der
  Entitätenübersicht, Tabelle sortierbar (Spaltenköpfe anklickbar),
  Spaltenbreiten per Ziehen anpassbar, Seitenweise mit 20 Zeilen pro Seite
  (beides bereits bestehende, app-weite Mechanismen, hier erstmals auf diese
  Tabelle angewendet). Werkzeugleiste in eine Zeile zusammengefasst,
  Suchfeld/„Verfügbarkeit prüfen“-Button kompakter, Button direkt neben dem
  Suchfeld, der reine Zähler-Chip „N bekannte Entitäten“ entfernt.
- Fehlermeldungen bei einer nicht erreichbaren oder fehlschlagenden
  Home-Assistant-API sind jetzt aussagekräftig (z. B. „antwortete mit 401:
  Unauthorized“ statt einer nichtssagenden Pauschalmeldung) — wichtig u. a.
  um eine fehlende `homeassistant_api`-Berechtigung (siehe 0.51.0, wirkt erst
  nach einem Add-on-Update/Rebuild) von einer echten Nichterreichbarkeit zu
  unterscheiden.

### Intern

- Deutlich mehr Protokollierung im Add-on-Log für alle drei Import-Wege
  (Symcon/CSV/Home Assistant): Start und Abschluss mit Kennzahlen
  (Entitäten/Zeilen importiert/zusammengeführt/Fehler) auf INFO-Ebene,
  fehlgeschlagene Einzelabrufe/Verfügbarkeitsprüfungen gegen Home Assistant
  auf WARNING-Ebene (bisher nur in der Oberfläche sichtbar, nie im Log),
  Löschen hochgeladener Quelldaten auf INFO-Ebene.

## 0.51.0 - 2026-08-27

### Neu

- Home-Assistant-Import: Zeitraum-Auswahl als Dropdown („Verfügbare Historie
  (max.)“ / „Letzte 10 Tage“ / „Letzte 30 Tage“ / „Eigener Zeitraum …“) statt
  zweier freier Datumsfelder — bei „Eigener Zeitraum“ erscheinen weiterhin
  Von/Bis-Felder.
- Neuer Button „Verfügbarkeit prüfen“: zeigt für alle gelisteten Entitäten,
  ob und in welchem Zeitraum Home Assistant tatsächlich Rohhistorie liefert
  (Spalten „Art“ und „Verfügbar“, z. B. „12.08.2025 – 29.08.2025 · 1.245
  Punkte“) — bewusst ein expliziter Klick statt automatischer Prüfung beim
  Öffnen des Reiters, dafür aber ein einziger gebündelter Abruf für alle
  Entitäten gleichzeitig (Home Assistants `filter_entity_id` akzeptiert eine
  kommagetrennte Liste), nicht ein Request pro Zeile.
- Die bisherige Spalte „Art“ (Standard/Zähler/Schalter) heißt jetzt „Typ“ —
  die neue Spalte „Art“ beschreibt stattdessen, ob Home Assistant Rohhistorie
  oder keine Daten liefert.
- Vorschau und Ergebnis des Home-Assistant-Imports zeigen jetzt zusätzlich
  den in Home Assistant tatsächlich gefundenen Zeitraum je Entität, in einer
  eigenen, für diesen Import passenderen Darstellung.
- „Bereinigen“/„Korrigieren“/„Hinzufügen“-Reiter auf der Bearbeitungsseite
  einer Entität optisch an die Import-Reiter angeglichen (gleiche Schrift,
  Größe, Gewichtung).

### Intern

- Der komplette Symcon-/CSV-/Home-Assistant-Import-Bereich wurde aus
  `main.py` in ein eigenes Modul (`import_routes.py`) ausgelagert, nach
  demselben Muster wie zuvor schon `api_routes.py`/`report_routes.py` —
  `main.py` schrumpft dadurch von 5.427 auf rund 4.070 Zeilen.

## 0.50.0 - 2026-08-27

### Neu

- Import-Reiter „Home Assistant": bestehende Recorder-Rohhistorie direkt aus
  der laufenden Home-Assistant-Instanz importieren, ohne Symcon oder eine
  hochgeladene Datei — Entitäten und Zeitraum wählen, Vorschau prüfen, Import
  starten. Zur Auswahl stehen ausschließlich Entitäten, die bereits in
  Zeitarchiv bekannt sind (also über die Home-Assistant-Integration
  konfiguriert wurden und mindestens einen Live-Wert übertragen haben) —
  es werden keine neuen Entitäten automatisch angelegt.
  Benötigt die neue Add-on-Berechtigung `homeassistant_api` (Zugriff auf die
  Home-Assistant-Core-API über den Supervisor-Proxy).

  Mindestanforderung: Home Assistant mit Supervisor (Home Assistant OS oder
  Supervised) sowie die REST-History-API mit den Parametern
  `minimal_response`/`no_attributes`. Beides ist seit sehr vielen Jahren
  fester, stabiler Bestandteil von Home Assistant — eine exakte
  Mindestversion war nicht mit Sicherheit zu ermitteln, in der Praxis
  funktioniert aber jede aktuell noch unterstützte/aktualisierte
  Home-Assistant-Installation. Nicht unterstützt: Core-only-Installationen
  ohne Supervisor (z. B. Home Assistant Container) — dort fehlt der
  `http://supervisor/core/api/`-Proxy, über den dieser Import läuft.

## 0.40.0 - 2026-08-27

### Neu

- Dashboards aufklappbar in der Navigation: „Dashboards" im Hauptmenü zeigt
  beim Klick eine Liste aller vorhandenen Dashboards (Desktop-Dropdown und
  mobiles Menü), statt direkt zur Übersichtsliste zu verlinken.
- Dashboard fixieren: ein Schalter im Editor sperrt Umsortieren,
  Größenändern und Entfernen von Kacheln auf der Ansicht — serverseitig
  durchgesetzt, nicht nur optisch versteckt. Umbenennen/Löschen bleiben im
  Editor weiterhin möglich.
- „Gesamter Zeitraum"-Kennzahlen auf der Bereinigungsseite: zusätzlich zur
  gewählten Periode eine dauerhafte Zeile mit Ausreißern/Lücken/Duplikaten/
  Wiederholungen über die komplette Historie (gecacht, 15 Min.).
- Neuer Chart/Neue Tabelle direkt im Anheften-Menü eines Dashboards, auch
  wenn bereits Kacheln vorhanden sind.
- Einheit beim Wert: die Rohwert-Tabelle (Bereinigen/Korrigieren) zeigt die
  Einheit der Entität jetzt direkt neben jedem Wert.
- Vergleichstabellen: Aggregation je Zeile wählbar (Automatisch/Ø/Min/Max/
  Summe) statt der bisher festen Zähler-Summe/Durchschnitt-Regel.
- Vergleichstabellen: Nachkommastellen je Spalte einstellbar (Automatisch
  oder 0–3).
- Einstellungen → Diagnose → Prozess zeigt Startzeitpunkt und Laufzeit der
  App.

### Geändert

- Reichhaltigere Begründungstexte bei Ausreißer/Lücke/Duplikat/Wiederholung
  (Vorwert samt Zeitstempel bzw. alle betroffenen Werte statt nur einer
  Kurzformel); zugehörige Tooltips überlappen nicht mehr die scrollbare
  Tabelle.
- Zeitstempel zeigen durchgängig das Jahr.
- Reihenfolge der Bereinigungs-Tabs: Bereinigen / Korrigieren / Hinzufügen.
- Spaltenbreiten der Rohwert-Tabelle neu austariert, Zeilenhöhe zwischen
  Bereinigen- und Korrigieren-Modus vereinheitlicht.
- Wert-Eingabefeld unter „Hinzufügen" nutzt durchgängig deutsches
  Zahlenformat (Komma).
- Löschen-Button auf der Bereinigungsseite in den App-Standardfarben/
  -Bestätigungsdialog statt nativem Browser-Popup.
- Redundante Breadcrumb auf Bereinigungs- und Konfigurationsseite entfernt.
- Übersichtsseite zeigt statt eines festen „Übersicht"-Titels den
  tatsächlichen Namen des Standard-Dashboards.
- Löschen-Bestätigung für Dashboards klargestellt: nur die Kachel-Anordnung
  geht verloren, zugrunde liegende Charts/Tabellen bleiben erhalten.
- Tabellen-Editor-Vorschau: Buchstaben-Kürzel (A/B/C …) sitzen jetzt sichtbar
  abgesetzt neben statt innerhalb der Tabelle, per Messung pixelgenau
  ausgerichtet.
- „Erste Spalte hervorheben" ohne den bisherigen Farbbalken am Rand.
- Zeilenabstand „Kompakt" in Vergleichstabellen spürbar enger.
- Einstellungen-Menü bleibt auf dem Smartphone beim Scrollen sichtbar
  (sticky, unterhalb der Kopfzeile) und folgt horizontal dem aktuell
  hervorgehobenen Abschnitt.
- Diagnose-Werkzeuge (Schreibvorgang aufzeichnen, Entität verfolgen,
  Diagnosebericht) in einem eigenen Abschnitt „Diagnose" zusammengefasst
  statt auf „Protokollierung"/„Über Zeitarchiv" verteilt.
- „Über Zeitarchiv" mit Logo, Versions-Badge und kompakterer
  Kachel-Darstellung neu gestaltet.
- Zahlreiche Hinweistexte in den Einstellungen gekürzt.

### Behoben

- Duplikate-Zähler in der Bereinigungsleiste zeigte die Anzahl
  Duplikat-Gruppen statt der tatsächlich gelisteten Zeilen.
- „Duplikate automatisch entfernen" schlug bei Entitäten mit mehr als
  500.000 Rohwerten im gewählten Zeitraum mit einem stillen 413-Fehler fehl.
- Einstellungen-Seite lud bei vielen Entitäten bzw. vielen markierten
  Löschungen spürbar langsam — der Rotation-Zähler durchsuchte den Hot
  Buffer je Entität einzeln, die Bereinigungsvorschau las bei jedem Aufruf
  erneut alle betroffenen Archiv-Monate. Beides jetzt einmalig berechnet
  bzw. gecacht.

## 0.30.1 - 2026-08-27

### Behoben

- Die Importseite lieferte bei Symcon-Variablen mit mindestens 1.000
  Datensätzen einen „Internal Server Error“, weil die Datensatzanzahl vor dem
  Rendern doppelt formatiert wurde.

## 0.30.0 - 2026-08-27

### Neu

- Globale Menüleiste ersetzt die bisherige Seitenleiste auf allen Seiten
  (Übersicht/Entitäten/Charts/Tabellen/Dashboards + System-Dropdown, inkl.
  mobilem Menü).
- Mehrere Dashboards: Kacheln lassen sich jetzt auf beliebig viele benannte
  Dashboards verteilen (Liste, Kachel-Ansicht, Editor) statt nur auf die
  eine Startseite.
- Drittes Farbschema „Modern" (Zinc/Indigo).

### Geändert

- Import-/Export-Icons in der Navigation getauscht, damit die Pfeilrichtung
  zur Semantik passt.
- Aufgeräumte Navigation: redundante „Charts →"/„Tabellen →"-Links und
  doppelte Breadcrumbs entfernt, wo bereits eine „← Zurück"-Zeile vorhanden
  ist.
- Tabellen (Entitäten, Symcon-Import, Reports, Formel-Tabellen) nutzen die
  durch den Wegfall der Seitenleiste gewonnene Breite; Entitäten-Tabelle
  berechnet ihre Mindestbreite jetzt serverseitig.
- Tabellen-Editor: Zeilentyp-, Entität-/Gruppen-Auswahl und Formel/Einheit
  kompakter in einer Zeile, Aktionen rechtsbündig.
- Deutsche Zahlenformatierung an mehreren bisher übersehenen Stellen ergänzt
  (Import-Vorschau/-Ergebnis, Bereinigungsvorschau, Aufbewahrungs-Übersicht
  u. a.).
- Diverse Oberflächenpolitur: „+"-Kachel und Bearbeiten-Button auf
  Charts-/Tabellen-Übersicht, „Über Zeitarchiv"/„Protokollierung" klarer
  strukturiert, größeres Logo, Tooltip für die HA-Entität-Zuordnung im
  Import.
- Mehrere interne Performance-Optimierungen in Statistik-Seite,
  Entitätenliste und Bereinigungs-Vorschau (siehe `PERFORMANCE.md`).

### Behoben

- Dropdown-Menüs funktionierten wegen fehlendem Alpine.js auf den meisten
  Seiten nicht, nur auf der Übersicht.
- NUL-Bytes im Chart-Editor-Template entfernt.

## 0.20.1 - 2026-08-26

### Neu

- Unter **Einstellungen → Über Zeitarchiv** zeigt eine neue Zeile den
  aktuellen RAM-Verbrauch der App.
- Seitennavigation (Entitäten, Bereinigung, Export, Import-Reports, Backup):
  Eingabefeld zur direkten Seitenwahl sowie Sprung zur ersten/letzten Seite.

### Geändert

- Speichernutzung (Statistik): „Import-Reports" steht jetzt nach „Backups"
  in Liste und Diagramm.

### Behoben

- Im Bearbeitungsbereich einer Entität blieb der Zeitraum „Jahr" bei sehr
  datenreichen Entitäten (mehr als 500.000 Rohwerte im Jahr) ohne
  Fehlermeldung auf dem vorherigen Zeitraum stehen. „Jahr" nutzt jetzt
  denselben speicherschonenden Abfragepfad wie „Gesamt" und funktioniert
  unabhängig von der Datenmenge.

## 0.20.0 - 2026-08-26

### Geändert

- Allgemeine Performance-, Stabilitäts- und Wartbarkeitsverbesserungen in
  Aufnahme, Speicherung, Rollups, Backup und Startablauf.
- Robustere Datenmigrationen, reproduzierbare Abhängigkeiten und eine
  modularere interne Struktur bei unverändertem Bedienkonzept.
- Die automatisierte Testsuite wurde an den aktuellen Funktionsstand angepasst
  und vollständig erfolgreich ausgeführt.

## 0.12.0 - 2026-08-26

### Neu

- Verlaufsansichten von Schalter-Entitäten (binary_sensor/switch/
  input_boolean) bieten zusätzlich zu Linie und Balken einen Zeitstrahl, der
  die AN-Intervalle als durchgehendes Band zeichnet.
- Statistik-Seite mit Kacheln zur Anzahl gespeicherter Charts, Tabellen und
  Dashboards sowie der Event-Rate pro Stunde/Tag.
- Zwei neue Werkzeuge zur Fehlersuche unter Einstellungen → Protokoll: eine
  einmalige Aufzeichnung des nächsten von Home Assistant gesendeten
  Schreib-Requests sowie eine zeitlich begrenzte Verfolgung einzelner
  Entitäten über 15 Minuten — beides unabhängig vom eingestellten Loglevel
  und ohne dauerhaften Mehraufwand.
- Schalter-Entitäten mit Dauer-Anzeigemodus zeigen Bucket-Werte in Charts als
  Zeitdauer (Stunden/Minuten) statt als Rohsekunden.
- Backup-Seite: Tabelle sortierbar, mit Seitennavigation sowie einer
  bestätigten Aktion zum Löschen aller Backups.

### Geändert

- Sämtliche native Auswahlfelder der Oberfläche (u. a. Einstellungen,
  Tabellen-Editor, Symcon-Import) verwenden jetzt ein einheitliches,
  durchsuchbares Auswahlmenü statt des Browser-Standard-Dropdowns.
- Die Uhrzeit-Auswahl ist ein Popup mit zwei numerischen Feldern samt
  Auf/Ab-Pfeilen statt einer Liste.
- Die Auswahl der Zeilenanzahl pro Seite sitzt jetzt einheitlich unten neben
  der Seitennavigation (Entitäten, Export, Bereinigung, Reports, Symcon-Import,
  Backup); der Export- und der Backup-Seite fehlte diese Navigation bisher
  teilweise.

## 0.11.0 - 2026-08-26

### Neu

- Gespeicherte Charts unterstützen eine zeitraumabhängige Anzeigeauflösung
  (mit Anzeige der tatsächlich aktiven Auflösung bei „Automatisch“) und eine
  optionale dynamische Y-Achse. Ein laufender Zeitraum zeigt dabei immer bis
  zur vollen Kalendergrenze (z. B. bis Sonntag), auch ohne Daten in der
  Zukunft.
- Charts zeigen Minimum, Maximum und Durchschnitt wahlweise direkt in der
  Legende an, zusammen mit der Ein-/Ausblendung einzelner Entitäten. Die
  Vergleichsfunktion sitzt in einem Auswahlmenü, benennt beide Optionen
  passend zum Zeitraum (z. B. „Vortag“, „Vorjahrestag“) und zeigt die aktive
  Wahl direkt im Button. Seltener geänderte Chart-Einstellungen sind in
  einem Optionen-Menü gebündelt.
- Entitäten in gespeicherten Charts sowie Spalten und Zeilen in
  Vergleichstabellen lassen sich per Ziehen oder über Pfeil-Buttons neu
  anordnen; Formel-Referenzen (A/B/C …) werden beim Umsortieren automatisch
  korrigiert.
- „Als Chart speichern“ auf der Entität-eigenen Chart-Seite übernimmt
  Entität, Zeitraum und Vergleichseinstellung in ein neues, mehrere
  Entitäten fähiges Chart.
- Die Dashboard-Kachel-Animation ist jetzt eine globale Einstellung unter
  Darstellung statt einer Einstellung je Chart.
- Import-Reports lassen sich nach jeder Spalte sortieren, filtern automatisch
  nach Quelle/Status und öffnen die Details per Klick auf die Zeile. Sie
  besitzen außerdem Seitennavigation und eine bestätigte Aktion zum Löschen
  aller Reports.
- Ein optionaler Wertänderungsfilter verwirft gerundet gleiche eingehende
  Folgewerte, behält aber spätestens alle sechs Stunden ein Lebenszeichen.
- Der Bearbeitungsbereich erkennt und verdichtet vorhandene gerundet gleiche
  Folgewerte. Für `total_increasing`-Zähler markiert er niedrigere Folgewerte
  als mögliche Zähler-Resets, ohne diese automatisch zu löschen.
- Die Lückenerkennung bietet zusätzlich „1 Tag“ als Schwellwert.

### Geändert

- Liniencharts werden in Entitätsansicht, gespeicherten Charts und Dashboard
  wieder geglättet dargestellt.
- Eingehende Werte werden unabhängig von der konfigurierten zeitlichen
  Mindestauflösung angenommen; Duplikat- und Wertänderungsfilter bleiben aktiv.
- Die Kennzahlen des Bearbeitungsbereichs stehen als kompakte Statusleiste
  zwischen Entitätskopf und Tabs. Sie unterscheiden „insgesamt“ von
  „im Zeitraum“ und heben erkannte Auffälligkeiten hervor.
- Die Lückenschwelle „60 Minuten“ heißt nun „1 Stunde“.
- Das Zahlenformat der Oberfläche ist jetzt durchgängig deutsch (Komma als
  Dezimal-, Punkt als Tausendertrennzeichen) statt vorher an manchen Stellen
  uneinheitlich Punkt/Komma.
- Der CSV-Import-Reiter heißt „CSV-Datei“ statt „Eigene CSV-Datei“.

### Behoben

- Der Symcon-Import startet wieder korrekt, wenn Entitätsdaten als
  `sqlite3.Row` vorliegen; ein Fehler vor dem eigentlichen Lauf hinterlässt
  außerdem keinen fälschlich laufenden Importstatus mehr.
- Werte mit vielen Nachkommastellen erzeugen bei aktiviertem Wertfilter keine
  ungebremsten, gerundet identischen Folgeeinträge mehr.
- Die dynamische Y-Achse in Charts hatte bisher keine sichtbare Wirkung
  (ECharts erzwang weiterhin die Einbindung der Null) und wirkt jetzt
  tatsächlich.
- Formel-Konstanten in Vergleichstabellen akzeptieren jetzt auch ein Komma
  als Dezimaltrennzeichen (z. B. „A * 3,5“).

## 0.10.0 - 2026-08-25

### Neu

- Der Symcon-Import liest Einheiten aus `settings.json`, zeigt sie in der
  Vorschau und vergleicht sie mit der Einheit der gewählten
  Home-Assistant-Entität.
- Bei abweichenden Einheiten kann ein Umrechnungsfaktor angegeben werden;
  bekannte Umrechnungen wie `klx` nach `lx` werden vorgeschlagen.

### Geändert

- Liniencharts stellen Messwerte als Stufen dar: Der letzte Wert bleibt bis
  zum nächsten Messpunkt gültig, auch über den Beginn und das Ende des
  sichtbaren Zeitfensters hinweg.

### Behoben

- Beim Import und bei der laufenden Übernahme werden vorhandene Messpunkte
  derselben Entität und desselben Zeitstempels unabhängig von ihrer Event-ID
  als Duplikat erkannt.
