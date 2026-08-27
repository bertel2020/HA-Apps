# Changelog

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
