# Changelog

## 0.80.3 - 2026-09-04

### Verbessert

- **`_storage_breakdown()` (ZP-011).** "Archiv" nutzte einen vollen
  Dateisystem-Walk bei jedem Diagnose-Download statt der ohnehin schon
  inkrementell gepflegten Index-Summe. Nutzt jetzt
  `index.get_overview()["total_size_bytes"]`. "Rollup" bleibt bewusst ein
  echter Walk — der Index führt keine Rollup-Dateigrößen, nur
  Archiv-Parquet-Größen.
- **`rotate_month_file()` (ZP-006).** Baute die drei Parquet-Spalten aus
  einer materialisierten Tupel-Liste per dreifacher Listcomprehension.
  Ein Durchlauf über `iter_records()` befüllt die Spalten jetzt direkt.
- **`_read_rollup_rows()` (ZP-007).** Las Zellen einzeln
  (`.as_py()` pro Zugriff) statt vektorisiert. Nutzt jetzt `to_pylist()`
  je Spalte, analog zum bestehenden Muster in `rollup.append_completed_month`.
- **Backup-Validierung (ZP-009).** `validate_backup()` hashte beim
  internen Aufruf aus `create_backup()` jede gerade erst geschriebene
  Datei ein zweites Mal komplett neu. Neuer `verify_checksums`-Parameter
  (Default weiterhin `True` für Upload/Restore/manuellen Prüfen-Button);
  `create_backup()` ruft intern mit `False` auf. `zf.testzip()` prüft
  dabei weiterhin die (viel billigere) ZIP-eigene CRC32-Summe —
  Strukturschutz bleibt erhalten.
- **Hot-Buffer-Mehrfachlesen (ZP-012, teilweise).** Der eigentlich
  heißeste Pfad (`ingestion._event_exists()`/`_timestamp_exists()`) war
  bereits vor dieser Änderung für den Normalfall entschärft. Tatsächlich
  behoben: `cleanup.iter_raw_rows()` wird über `analyze_raw_rows_page()`
  zweimal pro Anfrage aufgerufen (Zwei-Pass-Streaming) — die zwei
  betroffenen Aufrufstellen (Rohwerte-Ansicht, "Gesamt"-Kachel-Zähler)
  teilen jetzt einen `QueryReadCache` über beide Durchläufe.
- **CSV-Importvorschau (ZP-013, teilweise).** `csv_import.preview()` las
  die komplette Datei als Liste, nur um ein 8-Zeilen-Sample anzuzeigen.
  Liest jetzt streamend — `total_lines`/`column_count` brauchen weiterhin
  einen vollständigen Durchlauf, aber nur die Sample-Zeilen bleiben im
  Speicher.
- **Tabellen-Formatierung (ZP-014, teilweise).** `_entities_table_response()`
  formatierte immer alle Felder unabhängig von den tatsächlich sichtbaren
  Spalten. Formatiert jetzt nur noch, was `visible_columns` auch zeigt.

## 0.80.2 - 2026-09-04

### Behoben

- **`StorageCoordinator` ohne Timeout** (ROADMAP.md, "Neu seit 0.76.1",
  Punkt 1). `entity()`/`entities()`/`exclusive()` warteten bei einem
  hängenden Halter unbegrenzt — ein synchroner HTTP-Request über
  `storage_locked()` (jede Entität-speichern-Route) konnte dadurch für immer
  blockieren. Neuer optionaler `timeout`-Parameter (Default weiterhin `None`
  = unbegrenzt, für Hintergrund-Jobs wie Backup/Retention/Rotation/Purge/
  Import unverändert) wirft nach Ablauf `CoordinatorBusy` (503 "Speicherzugriff
  kurzzeitig ausgelastet"), analog zu `IndexBusy`. `storage_locked()` nutzt
  jetzt standardmäßig 30s.
- **Backup-Hintergrund-Thread ohne Lebenszeichen.** Anders als der
  Wartungsplaner-Loop lief der Backup-Worker immer in einem eigenen,
  losgelösten Thread — ein Hang dort (z. B. an einem Entitäts-Lock während
  `create_source_snapshot()`) blieb komplett unbeobachtet, auch vom
  Scheduler-Heartbeat. Neue Meldung `system.backup_worker_stalled`, analog
  zu `system.storage_reconcile_stalled`.
- **Kein Schema-Schutz gegen einen künftigen Downgrade** (ROADMAP.md, "Neu
  seit 0.76.1", Punkt 3). `energiedashboard_config` trägt jetzt ein
  `schema_version`-Feld (`CONFIG_SCHEMA_VERSION` in
  `energiedashboard_routes.py`); fehlt es, gilt implizit Version 0 und die
  bestehende Migration (speicher: Dict → Liste) greift unverändert weiter.
  Liest eine künftige ältere Version eine Config mit höherer
  `schema_version`, gibt `_load_config()` einen sicheren leeren Stand
  zurück statt zu raten oder zu crashen, und speichert dabei nichts
  zurück — die eigentlichen neueren Daten bleiben in der DB erhalten. Neue
  Meldung `energiedashboard.config_from_newer_version` erklärt den Zustand.
  Der bereits ausgelieferte 0.75.0-Fall selbst bleibt unheilbar (die alte
  Version kennt dieses Feld nicht).
- **`_security_headers`/`_request_logging` als `BaseHTTPMiddleware`**
  (ROADMAP.md, "Neu seit 0.76.1", Punkt 2). Starlettes `BaseHTTPMiddleware`
  führt Routen in einem zweiten, über einen Memory-Stream verbundenen
  `anyio`-Task aus — ein Client-Abbruch mitten im Antwort-Versand erzeugte
  dadurch `CancelledError`/`WouldBlock`-Tracebacks. Beide Handler sind
  jetzt reine ASGI-Middleware-Klassen (`RequestLoggingMiddleware`/
  `SecurityHeadersMiddleware`, über `app.add_middleware()`) — kein zweiter
  Task mehr. `request.state.request_id` bleibt für `api_routes.py`
  erhalten. Neue `tests/test_http_middleware.py` charakterisiert das
  Verhalten (Security-Header, `X-Request-ID`, Access-Log-Korrelation) vor
  und nach dem Umbau.

## 0.80.1 - 2026-09-04

### Behoben

- **Falsche "vs. Vorperiode"-Prozentzahl beim Navigieren in vergangene
  Tage/Wochen/Monate/Jahre im Energiedashboard.** Der Vergleichswert kam aus
  einem rollierenden, an "jetzt" verankerten Zeitfenster statt aus der
  tatsächlich angezeigten Kalenderperiode — konnte z. B. ein "+X %" zeigen,
  obwohl der angezeigte kWh-Wert gegenüber dem Vortag gesunken war. Betraf
  alle KPI-Kacheln sowie den Energiebericht. Die rollierende Berechnung wird
  jetzt nur noch für die aktuell noch laufende Periode verwendet (dort
  nötig, damit "heute bis 8 Uhr" nicht unfair gegen "gestern komplett"
  verglichen wird); für abgeschlossene Perioden zählt jetzt derselbe
  kalendarische Zeitraum wie der angezeigte Wert.

## 0.80.0 - 2026-09-03

### Neu

- **Startseite wählbar (Übersicht/Energiedashboard).** Neue Einstellung unter
  **Einstellungen → Darstellung** legt fest, was beim Öffnen von Zeitarchiv
  über die HA-Sidebar erscheint. Die Übersichtsseite selbst ist dafür jetzt
  unter einer eigenen Adresse erreichbar (`/uebersicht`) statt fest unter
  `/` — der „Übersicht"-Eintrag in der Kopfzeile führt dadurch unabhängig
  von der gewählten Startseite immer dorthin.
- **Housekeeping → Konfiguration.** Neuer Abschnitt listet Entitäten, deren
  Lücken-Erkennung strukturell nie zutreffen kann, weil die gewählte
  Auflösung oder der aktive Wertänderungsfilter selbst schon einen
  größeren Mindestabstand zwischen gespeicherten Werten erzwingt — bislang
  nur über die Glocke als Sammelzahl sichtbar, jetzt mit Auflösung, aktueller
  und empfohlener Lücken-Erkennung je betroffener Entität. Derselbe Guard,
  der die Einstellung beim Ändern automatisch anhebt, erkannte bisher nur
  den Wertänderungsfilter als Ursache — die Auflösung selbst (die genauso
  einen Mindestabstand zwischen Werten erzwingt) blieb unentdeckt.
- Selbstheilungs-Heartbeat für den Speicherindex-Hintergrundabgleich
  (analog zum Wartungsplaner seit 0.76.0): bleibt der Abgleich länger als
  5 Minuten ohne Fortschritt hängen, erscheint eine Meldung, statt
  unbemerkt zu blockieren.

### Geändert

- Entitätenliste und -übersicht lesen `deleted_count` jetzt aus einer
  gepflegten Spalte statt sie bei jedem Aufruf neu über die komplette
  Löschmarkierungs-Tabelle zu aggregieren — bei 1,5 Mio. Markierungen
  spart das ~75-78 ms pro Seitenaufruf/Tastendruck im Such-/Filterfeld.
- In Housekeeping steht „Inaktive Entitäten" jetzt vor „Duplikate".

### Behoben

- Drucken-Button im Energiebericht zeigte in manchen Schriftarten eine
  leere Box statt eines Drucker-Symbols (schlecht unterstütztes
  Unicode-Zeichen) — jetzt ein eingebettetes SVG-Icon.

## 0.79.1 - 2026-09-03

### Geändert

- Hinweistext zur Startseiten-Einstellung ergänzt: Zeitarchiv lässt sich
  auch per Klick auf „Zeitarchiv" öffnen, nicht nur über die HA-Sidebar.

### Behoben

- **Kosten-Festpreise (Netzbezug/Einspeisung) und CO2-Faktor:** Ein
  geänderter fester Preis wurde beim Klick auf „Übernehmen" im Popup nicht
  auf der Kachel sichtbar — der Anzeigewert blieb auf dem zuletzt
  geladenen Stand hängen, obwohl der neue Wert korrekt in die verborgenen
  Formularfelder übernommen wurde. Betraf Netzbezug-Preis,
  Einspeisevergütung und die CO2-Intensität gleichermaßen.
- Platzhaltertext „z. B. …" in den festen Preis-/Kapazitätsfeldern entfernt
  — konnte im leeren Zustand wie ein bereits eingetragener Wert wirken.

## 0.79.0 - 2026-09-03

### Neu

- **Energiebericht im Energiedashboard.** Ein neues Symbol neben der
  Zeitraum-Navigation (Monat/Jahr) öffnet einen druckoptimierten Bericht für
  den gewählten Zeitraum: Kennzahlen samt Vorjahres-/Vormonatsvergleich,
  Kosten- und CO₂-Bilanz (inklusive CO₂-Vergleich als Autofahrt-Strecke),
  Verbraucheranteile mit Kosten je Verbraucher, bei Jahresberichten
  zusätzlich Monatsverlauf und alle Auffälligkeiten des Jahres. Der Button
  „Drucken / Als PDF speichern" ruft nur den Druckdialog des Browsers auf —
  kein neues Dateiformat, kein Versand, rein lokal.

## 0.78.0 - 2026-09-03

### Neu

- **Rückkopplung zur Home-Assistant-Integration.** Neuer Endpunkt
  `GET /api/notices` liefert die aktuellen Meldungen für die Integration —
  Grundlage für Home-Assistant-Repairs und automatisierbare
  `binary_sensor`-Entities bei kritischen Zuständen (Backup/Aufbewahrung/
  Import fehlgeschlagen, lange inaktive Entitäten).
- Die Verbindungs-Einstellungen zeigen jetzt die verbundene
  Integrationsversion, wann sie zuletzt gesehen wurde, sowie einen Hinweis
  bei veralteter oder bei neu verfügbarer Integrationsversion (getrennt
  nach Bugfix/Funktionsupdate).
- Neue Meldung für knappen Speicherplatz auf dem Host-Dateisystem (nicht zu
  verwechseln mit Zeitarchivs eigenem internen Speicherverbrauch) —
  zweistufig (Warnung ab 10 % frei, kritisch ab 5 % frei). Housekeeping →
  Speicherplatz zeigt den aktuellen Füllstand jetzt immer als Kachel mit
  Auslastungsbalken an.

### Geändert

- Neue Design-Variable `--warning` für alle drei Farbschemata (hell/dunkel)
  ergänzt — echtes Gelb/Amber statt einer zweckentfremdeten Akzentfarbe für
  Hinweise, die kein echtes Problem sind.

## 0.77.0 - 2026-09-03

### Verbesserung

- **Rollenzuordnung im Energiedashboard als Kacheln mit Konfig-Popups.**
  Statt eines einzigen langen Formulars zeigt „Rollen bearbeiten" jetzt für
  jede Rolle (Netzbezug, Einspeisung, Erzeuger, Speicher, Verbraucher,
  Kosten, Prognose, CO₂) eine eigene Kachel — Klick öffnet ein Popup mit den
  zugehörigen Feldern, farblich passend zur Kachel umrahmt. Änderungen
  gelten dort erst nach **„Übernehmen"**, nicht mehr sofort — ein
  versehentlich geöffnetes Popup lässt sich wieder schließen, ohne etwas zu
  verändern.
- Entitäts-Auswahlfelder zeigen nur noch Entitäten mit passender Einheit
  bzw. Zähler-Typ für die jeweilige Rolle (z. B. nur kWh-Zähler für
  Netzbezug, keine Schalter-Entitäten).
- Speicher-Kacheln zeigen die aufgelöste Kapazität direkt in kWh — auch bei
  Zuordnung über eine Entität statt eines festen Werts (Wh wird automatisch
  umgerechnet), mit dem Entitätsnamen als zweite Zeile.
- Speicher-KPI-Kachel und aktueller Ladezustand zeigen bei mehreren
  Speichern eine Aufschlüsselung je Speicher im Tooltip, analog zur
  bestehenden Erzeugung-Aufschlüsselung.
- Feldhinweise in der Rollenzuordnung vereinheitlicht: jeder Hinweis sitzt
  jetzt direkt unter seinem Feld statt in einer separaten Legende oder über
  dem Feld.
- Bestätigungsdialoge (z. B. beim Entfernen eines Erzeugers) nutzen jetzt
  ein natives `<dialog>` und liegen dadurch zuverlässig über bereits
  offenen Konfig-Popups statt dahinter.

### Behoben

- **Rollen speichern konnte fehlschlagen.** Beim Öffnen und Schließen eines
  Konfig-Popups verschwanden dessen Felder anschließend wieder aus der
  Seite (technisch bedingt durch die Popup-Neuinitialisierung) und wurden
  beim Klick auf „Speichern" nicht mitgesendet — im ungünstigsten Fall
  bereits ohne gültigen Netzbezug, wodurch das Speichern komplett verweigert
  wurde, in anderen Fällen wurden Kosten-/Prognose-/CO₂-Zuordnungen beim
  Speichern still verworfen.
- Konfig-Popups öffneten sich zeitweise nicht mehr mittig im Fenster
  (verursacht durch die Akzentfarben-Umrandung), und die Seite konnte beim
  Öffnen eines Popups ungewollt etwas verschieben.

## 0.76.2 - 2026-09-03

### Verbesserung

- **Installation und Updates über vorgebaute Images.** Bisher baute jeder
  Home-Assistant-Host das Docker-Image beim Installieren oder Aktualisieren
  selbst, minutenlang und ohne Fortschrittsanzeige („Wird installiert
  (0 %)"). Jetzt liefert ein GitHub-Workflow fertige Images für `amd64` und
  `aarch64` auf `ghcr.io`, der Supervisor lädt sie nur noch herunter und
  zeigt dabei den echten Fortschritt an.
- Der Add-on-Store verlinkt jetzt auf die Projektseite (das `url`-Feld in der
  `config.yaml` fehlte bisher). In den READMEs gibt es Buttons für My Home
  Assistant, die das Repository eintragen bzw. Zeitarchiv direkt öffnen.

## 0.76.1 - 2026-09-03

### Behoben

- **Träge Seiten und Hänger beim Filtern der Entitätenliste.** Das
  Meldungs-Center (Glocken-Icon, seit 0.75.0) wurde bei jeder Antwort neu
  berechnet — auch bei jedem Tastendruck im Suchfeld — und aggregierte dabei
  dreimal die komplette Tabelle der Löschmarkierungen (bei 1,5 Millionen
  Einträgen ~75 ms je Durchgang). Zusätzlich nahm es zum Zählen ausstehender
  Rotationen die Sperren *aller* Entitäten; in Produktion mit laufender
  Home-Assistant-Ingestion blockierte das gegen die Schreibvorgänge und
  fühlte sich beim Tippen wie ein Hänger an. Jetzt lesen die Meldungen ohne
  diese Aggregation, und der Rotations-Zähler kommt aus dem 30-Sekunden-
  Wartungsplaner statt aus dem Seitenaufruf. Gemessen mit den Demo-Daten:
  Entitätenliste 0,31 → 0,002 s, Meldungs-Panel 0,60 → 0,003 s, Such-
  Fragment 0,39 → 0,08 s, Startseite 0,53 → 0,23 s.

### Hinweis

- Ein Downgrade von 0.76.x auf 0.75.0 ist nicht möglich: das Energiedashboard
  speichert mehrere Speicher seit 0.76.0 als Liste, die ältere Version
  startet mit dieser Konfiguration nicht mehr.

## 0.76.0 - 2026-09-03

### Neu

- **Verbraucher-Gruppen im Energiedashboard**: beliebig viele Verbraucher
  lassen sich zu frei benannten Gruppen zusammenfassen (z. B. „Mobilität",
  „Haushaltsgeräte") — im Sankey-Fluss hängt ein gruppierter Verbraucher
  zweistufig am Bus (Bus → Gruppe → Gerät), ein ungruppierter weiterhin
  direkt am Bus. Ersetzt den bisherigen einzelnen „Verbraucher"-Sammelknoten
  für alle Geräte, der bei sehr unterschiedlichen Größenordnungen unruhige,
  sich kreuzende Bänder erzeugte. Gruppen lassen sich direkt beim Zuordnen
  eines Verbrauchers anlegen oder über einen eigenen „Gruppen"-Button
  verwalten (umbenennen, löschen).
- **Mehrere Speicher im Energiedashboard**: Speicher-Rollen (Laden/Entladen/
  Ladezustand) lassen sich jetzt beliebig oft statt nur einmal zuordnen.
  Werte über mehrere Speicher hinweg werden addiert, der Ladezustand
  kapazitätsgewichtet gemittelt (ein leerer und ein voller Speicher zeigen
  dadurch nicht fälschlich „50 %"). Bestehende Konfigurationen mit einem
  einzelnen Speicher werden automatisch migriert.
- **Auffälligkeiten-Erkennung im Energiedashboard**: Verbraucher oder
  Gruppen, die deutlich über ihrem Schnitt der letzten drei Vergleichs-
  perioden liegen, werden im Sankey farblich markiert und im „Status"-Popup
  aufgelistet. Schwelle (25 %/50 %/100 % über dem Schnitt, Standard 50 %)
  im Rollen-Formular unter „Allgemein" einstellbar oder ganz abschaltbar.
- **Docker-Healthcheck**: Home Assistant Supervisor kann jetzt erkennen,
  wenn die App nicht mehr antwortet (z. B. bei einem internen Locking-
  Fehler, siehe „Behoben" unten).
- **2 neue System-Meldungen**: „Wartungsplaner reagiert nicht" (wenn seit
  über 5 Minuten kein Durchlauf des Hintergrund-Wartungsplaners
  abgeschlossen wurde) und „Kurzzeitige Datenbank-Überlastung erkannt"
  (wenn ein Datenbankzugriff wegen interner Auslastung abgebrochen und
  automatisch wiederholt werden musste).

### Verbesserung

- Der Tooltip auf einer Sankey-Verbindung zeigt den Prozentanteil jetzt an
  der tatsächlich aufschlüsselnden Seite (Quelle oder Ziel — je nachdem, wo
  mehrere Linien zusammenlaufen), nicht mehr immer am Ziel.

### Behoben

- Ein interner Locking-Fehler beim Ändern des Zähler-/Wertetyps einer
  Entität (z. B. wenn eine Integration den `state_class` einer Entität
  nachträglich ändert) konnte die App vollständig einfrieren, weil ein
  Hintergrund-Thread endlos auf ein bereits von ihm selbst gehaltenes Lock
  wartete. Datenbank-Zugriffe scheitern jetzt nach spätestens 8 Sekunden
  mit einer klaren Meldung statt unbegrenzt zu blockieren; der Fehler
  behebt sich dadurch von selbst.

## 0.75.0 - 2026-09-02

### Neu

- **Housekeeping-Bereich** (neuer Menüpunkt unter System, unterhalb Statistik):
  sammelt an einer Stelle, was sonst leicht übersehen wird — erkannte
  Duplikate (letzte 30 Tage), inaktive Entitäten (Schwellwert von 1 bis 30
  Tagen wählbar) und ungenutzte Charts/Tabellen (in keinem Dashboard
  eingebunden). Außerdem ziehen die bisher unter Einstellungen verstreuten
  Wartungsbereiche hierher um: Speicherplatz (Indexkonsistenz-Prüfung,
  endgültige Bereinigung markierter Datensätze), Aufbewahrung (inkl. der
  bisher auf der Statistik-Seite gezeigten Fällig/Freigebbar-Übersicht) und
  Rotation.
- **Rotierende Praxis-Tipps** im Meldungs-Center: 30 kurze Tipps zu Funktionen
  der App, wechseln täglich. Über Einstellungen → Meldungen komplett
  abschaltbar oder in einem Dialog mit allen Tipps samt Status einsehbar; der
  gerade aktuelle Tipp lässt sich für den Rest des Tages ausblenden, ohne die
  Rotation zu unterbrechen.
- **9 neue System-Meldungen** ergänzen die bestehenden (Update verfügbar,
  Index-Optimierung, Backup-/Aufbewahrung-Fehlschlag): Speicherindex-Prüfung
  unvollständig, Index-Abweichungen gefunden, kein Backup-Zeitplan aktiv,
  Aufbewahrung konfiguriert aber nicht durchgesetzt, Import fehlgeschlagen
  oder unvollständig, endgültige Bereinigung möglich, Duplikate gefunden,
  Rotation ausstehend, inaktive Entitäten (dreistufig nach Alter: 1/3/7 Tage),
  Wertänderungsfilter im Konflikt mit einer zu kurzen Lücken-Erkennung, sowie
  ein Hinweis, solange das Tageslastprofil im Energiedashboard nach einer
  Konfigurationsänderung noch rückwirkend vervollständigt wird.
- **Standardwerte für neu erkannte Entitäten** erweitert (Einstellungen →
  Archivierung → Standards): neben Auflösung/Aufbewahrung jetzt auch
  Nachkommastellen, Wertänderungsfilter sowie Lücken-/Ausreißer-Erkennung
  voreinstellbar. Neu erkannte Entitäten aktivieren den Wertänderungsfilter
  ab sofort standardmäßig.
- Aktiviert man den Wertänderungsfilter (pro Entität oder als Standard für
  neue Entitäten), wird eine zu kurz eingestellte Lücken-Erkennung
  automatisch auf 6 Stunden angehoben — der Filter überspringt unveränderte
  Werte bis zu 6 Stunden lang, eine kürzere Schwelle hätte sonst laufend
  falsche Lücken-Meldungen erzeugt. Lässt sich danach jederzeit wieder
  manuell verkleinern.

### Verbesserung

- Die Kennzahlen-Tabelle „Bestand und Fälligkeit nach Aufbewahrungsfrist"
  folgt jetzt wie die übrigen Tabellen dem App-Standard: Sortierung per Klick
  auf die Spaltenüberschrift, Seitennavigation ab mehr als 10 Zeilen.

### Behoben

- Die Bereinigungs-Vorschau („Endgültige Bereinigung") konnte bis zu einer
  Stunde veraltet bleiben, nachdem irgendwo Datensätze zur Löschung markiert
  wurden — aktualisiert sich jetzt binnen rund 30 Sekunden.
- Stummschalten oder Zurückholen einer Meldung im Glocken-Menü aktualisierte
  die „Meldungen"-Sektion in den Einstellungen nicht automatisch (und
  umgekehrt) — beide Ansichten zeigten bis zum manuellen Neuladen
  unterschiedliche Stände.
- Der Tooltip des Stummschalten-Buttons im Meldungs-Center wurde am rechten
  Rand des Panels abgeschnitten.
- Sprungmarken auf Seiten mit seitlicher Navigation (Einstellungen,
  Housekeeping) — etwa aus dem Meldungen-Panel — landeten mit der
  Abschnittsüberschrift teilweise hinter der Kopfzeile.

## 0.74.0 - 2026-09-02

### Verbesserung

- **Smartphone-Ansicht** (alle Punkte greifen nur auf schmalen Bildschirmen,
  die Desktop-Darstellung bleibt unverändert):
  - Vergleichstabellen mit der Option „Spalten gleichmäßig" zeigen auf dem
    Handy wieder lesbare Werte: statt alle Spalten auf 15 px zu quetschen,
    behalten sie ihre Inhaltsbreite und scrollen horizontal — in der
    Tabellenansicht wie in der Dashboard-Kachel.
  - Das Meldungen-Panel (Glocke) öffnet sich innerhalb des Bildschirms statt
    links herauszuragen.
  - Energiedashboard: Titel bricht nicht mehr Buchstabe für Buchstabe um; der
    Datenqualität-Chip zeigt nur noch das Symbol (Text als Tooltip), die
    CO2-/Kosten-Badges stehen in eigener Zeile. Zeitraum-Auswahl und
    Perioden-Navigation sind zentriert. Der Verbraucheranteile-Donut ist
    zentriert und so groß wie der Speichernutzungs-Donut in der Statistik;
    der Sankey wird vertikal gezeichnet, mit Namen über den Quellen und unter
    den Verbrauchern statt überlappend neben den Knoten.
  - Statistik: Speichernutzungs-Donut mittig über der Tabelle.
  - Protokoll-Seite: die Karte „Protokollierung" ist einklappbar und startet
    zugeklappt, damit das Log direkt sichtbar ist.
- Übersichten Charts und Tabellen: „★ Favoriten zuerst" steht jetzt direkt
  neben der Suche vor der Sortierung, die Beschriftung „Sortierung" entfällt.

### Behoben

- Der Verbraucheranteile-Donut im Energiedashboard blieb winzig, wenn die
  Kachel beim ersten Zeichnen noch ausgeblendet war (jetzt größenüberwacht
  wie Heatmap und Speichernutzungs-Donut).
- Die mobile Sankey-Erkennung nutzte die Breite des sichtbaren Viewports, die
  bei überbreiter Seite mitwächst; jetzt dieselbe Media-Query wie das CSS.

## 0.73.0 - 2026-09-02

### Neu

- **App-eigener Anzeigename** je Entität (bis 40 Zeichen, unabhängig von
  Home Assistants eigenem `friendly_name`) — ein Tag-Symbol markiert ihn
  überall in der Oberfläche, wo er aktiv ist.
- **Meldungen-Center** (Glocke in der Kopfzeile): Systemmeldungen wie eine
  empfohlene Index-Optimierung, fehlgeschlagene Backup-/Aufbewahrungsläufe
  oder ein verfügbares Update lassen sich für 1 Stunde, 1 Tag, 7 Tage,
  30 Tage oder dauerhaft stummschalten. Fehlermeldungen bleiben immer
  sichtbar.
- **Versionsprüfung**: einmal täglich im Hintergrund gegen das öffentliche
  Repository, zeigt unter „Über Zeitarchiv" einen Hinweis, sobald eine
  neuere Version verfügbar ist.
- **Tageslastprofil** (Energiedashboard) zeigt bei Monat/Jahr jetzt den nach
  Wochentag gemittelten Verbrauch (Mo–So) statt weiterhin nur der letzten
  7 Kalendertage — sichtbar wird, an welchen Wochentagen typischerweise mehr
  verbraucht wird. Zähler-Rollen bekommen dafür zusätzlich eine feinere,
  stündliche Verdichtung, die auch rückwirkend für bereits archivierte
  Monate nachgebaut wird.
- Neuer Abschnitt **Hintergrundprozesse** unter Einstellungen → Diagnose:
  letzter Lauf und Status jeder Wartungsplaner-Aufgabe, bisher nur in den
  Server-Logs sichtbar.
- Dashboard-Kacheln aktualisieren sich automatisch alle 60 Sekunden, solange
  die Seite sichtbar ist.

### Verbesserung

- Fehlgeschlagene Backup-/Aufbewahrungs-Jobs zeigen den Fehlergrund jetzt in
  einem Popup statt in einem systemeigenen Tooltip; die Zeile selbst ist
  dafür klickbar.
- Irreführender Hinweistext zur externen Backup-Sicherung korrigiert.
- Mehrfache Abfragen innerhalb eines Energiedashboard-Seitenaufrufs teilen
  sich jetzt einen gemeinsamen Lesecache statt sich überlappende
  Rohdaten-Dateien wiederholt einzeln einzulesen.

### Dokumentation

- Benutzerhandbuch ergänzt um App-eigenen Anzeigenamen, Meldungen-Center,
  Versionsprüfung, Hintergrundprozesse-Übersicht und das erweiterte
  Tageslastprofil; veralteter Verweis auf eine eigene
  „Protokollierung"-Einstellungssektion entfernt (liegt jetzt direkt auf der
  Protokoll-Seite).

## 0.72.0 - 2026-09-01

### Neu

- Die Logansicht kann zwischen dem lokalen Live-Puffer und der umfassenderen
  Supervisor-Historie wechseln. Lokale Meldungen aktualisieren sich ohne
  Supervisor-Aufruf und ohne selbst neue erfolgreiche Polling-Logs zu
  erzeugen.
- HTTP-Anfragen und Ingest-Batches erhalten eine korrelierbare Request-ID;
  wichtige Betriebsereignisse verwenden stabile `event=`-Codes sowie
  Laufzeiten und Ergebniszähler.
- Ingest-Recovery meldet offene beziehungsweise überalterte Claims,
  wiederhergestellte Events, betroffene Entitäten und Ledger-Bereinigungen.

### Verbesserung

- Zentrale Redaction entfernt Bearer-Token und benannte Secrets nun auch aus
  JSON-Ausgaben sowie aus Uvicorn-, FastAPI- und anderen Fremdloggern, bevor
  sie in der Supervisor-Ausgabe landen.
- Langsame HTTP-Anfragen und auffällige Ingest-Batches werden unabhängig vom
  normalen Access-Log sichtbar; wiederkehrende Warnungen sind zeitlich
  gedrosselt und weisen auf unterdrückte Wiederholungen hin.
- Logzeitstempel verwenden ISO 8601 inklusive Millisekunden und lokaler
  Zeitzone. Backup-, Retention-, Speicherabgleich- und Indexwartungslogs
  enthalten konsistente Event-Codes, Job-IDs und Laufzeiten.
- Der einmalige Write-Capture wird spätestens nach 60 Minuten auch ohne
  weiteren Seitenaufruf automatisch gelöscht. Der Download ist ausdrücklich
  nicht cachebar; der Entity-Trace zeigt zusätzlich das finale
  Ingest-Ergebnis und eine gekürzte Event-ID.

### Dokumentation

- Neue Betriebsdokumentation zu Logquellen, Level-Semantik, Redaction,
  Request-Korrelation, Ingest-Observability und sicherem Diagnoseeinsatz.
- Benutzerhandbuch, Ingest-, Sicherheits- und Testdokumentation wurden an das
  neue Verhalten angepasst.

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
