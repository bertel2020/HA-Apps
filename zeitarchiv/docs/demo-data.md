# Demo-Daten

`scripts/generate_demo_data.py` füllt ein Zeitarchiv-Datenverzeichnis mit
realistisch aussehenden, synthetischen Beispieldaten — für Screenshots,
Doku, eine vorzeigbare Demo-Instanz oder einfach zum lokalen Entwickeln ohne
echte Home-Assistant-Anbindung.

Das Skript schreibt dabei **kein eigenes Format**, sondern nutzt denselben
generischen Schreibkern, den auch der CSV- und Symcon-Import der App selbst
verwenden (`app/storage/symcon_import.py::import_rows()`, siehe
[ingestion.md](ingestion.md)/[data-model.md](data-model.md)) — die
erzeugten Parquet-Archive, Rollups, der Hot Buffer und der SQLite-Index sind
also genau die Dateien, die auch eine echte Instanz anlegen würde.

## Was wird erzeugt

41 Entitäten, thematisch ein einzelner Haushalt mit Dach-PV-Anlage
(inkl. Ertrags-Prognose), Wallbox, einem zusätzlichen Balkonkraftwerk mit
Speicher und der Netz-CO2-Intensität, alle mit dem Präfix `demo_` in der
Entity-ID (leicht wiederzuerkennen und gezielt löschbar):

| Entität | Typ | Einheit | Muster |
| --- | --- | --- | --- |
| `sensor.demo_wohnzimmer_temperatur` | Standard | °C | Tageszyklus um 21 °C, leichtes Rauschen |
| `sensor.demo_aussentemperatur` | Standard | °C | Jahreszeitlich (Mitteleuropa-Profil) + Tageszyklus + mehrtägig korreliertes „Wetter" |
| `sensor.demo_luftfeuchte` | Standard | % | Außen, an Außentemperatur/Bewölkung gekoppelt |
| `sensor.demo_wohnzimmer_luftfeuchte` | Standard | % | Innen, gedämpfter als draußen, lose an die Außenluftfeuchte gekoppelt |
| `sensor.demo_wind` | Standard | km/h | Tageswert (lose an Bewölkung gekoppelt: windiger bei mehr Wolken) + Tagesgang + gelegentliche Böen |
| `sensor.demo_co2_intensitaet` | Standard | g/kWh | Wie ein CO2-Signal-/ElectricityMap-Sensor: Grundlast nachts, Einbruch mittags durch PV-Einspeisung ins Netz, leichter Anstieg zur Abendspitze; an sonnigen/windigen Tagen (dieselbe Bewölkung/Windbasis wie PV/Wind) zusätzlich niedriger |
| `sensor.demo_gesamtwirkleistung` | Standard | W | Summe aus Grundlast, Heizung und allen Einzelverbrauchern unten (inkl. Wallbox) |
| `sensor.demo_heizung` | Standard | W | Thermostat-Takten, Anteil „an" je 30-Minuten-Fenster steigt mit Kälte (0 im Sommer) |
| `sensor.demo_waschmaschine` | Standard | W | Einzelne Zyklen (~alle 3 Tage), mehrphasiges Leistungsprofil (Füllen/Heizen/Waschen/Schleudern) |
| `sensor.demo_waschmaschine_energie` | Zähler (`total_increasing`) | kWh | Monoton steigend, exakte Integration von `demo_waschmaschine` |
| `binary_sensor.demo_waschmaschine_an` | Schalter | — | An/Aus, deckungsgleich mit den Zyklen von `demo_waschmaschine` |
| `sensor.demo_spuelmaschine` | Standard | W | Einzelne Zyklen (meist abends), mehrphasiges Leistungsprofil |
| `sensor.demo_spuelmaschine_energie` | Zähler (`total_increasing`) | kWh | Monoton steigend, exakte Integration von `demo_spuelmaschine` |
| `binary_sensor.demo_spuelmaschine_an` | Schalter | — | An/Aus, deckungsgleich mit den Zyklen von `demo_spuelmaschine` |
| `sensor.demo_trockner` | Standard | W | Läuft nur an Tagen mit Waschmaschinen-Zyklus, mit Verzögerung danach |
| `sensor.demo_trockner_energie` | Zähler (`total_increasing`) | kWh | Monoton steigend, exakte Integration von `demo_trockner` |
| `binary_sensor.demo_trockner_an` | Schalter | — | An/Aus, deckungsgleich mit den Zyklen von `demo_trockner` |
| `sensor.demo_wallbox_leistung` | Standard | W | Einzelne Ladevorgänge (~alle 3 Tage abends), Anlaufen/Plateau/Taper |
| `sensor.demo_wallbox_energie` | Zähler (`total_increasing`) | kWh | Monoton steigend, exakte Integration von `demo_wallbox_leistung` |
| `sensor.demo_pv_leistung` | Standard | W | Glockenkurve tagsüber, 0 nachts; Tageslichtlänge nach Jahreszeit, gedämpft durch dieselbe Bewölkung wie Außentemperatur/Luftfeuchte |
| `sensor.demo_pv_ertrag` | Zähler (`total_increasing`) | kWh | Monoton steigend, exakte Integration der PV-Leistung über die Zeit |
| `sensor.demo_pv_prognose_rest_heute` | Standard | kWh | Wie ein Forecast.Solar-Sensor: geschätzter Rest-Ertrag der Dachanlage bis Sonnenuntergang, einmal morgens grob geschätzt (mit Schätzfehler, nicht exaktem Vorauswissen) und sinkt über den Tag mit dem tatsächlich eingefahrenen Ertrag Richtung 0 |
| `sensor.demo_pv_prognose_morgen` | Standard | kWh | Tagesprognose für morgen, einmal pro Kalendertag neu geschätzt (Fortschreibung der Bewölkung + eigene Unsicherheit), bleibt über den Tag konstant — wird am nächsten Tag zur Basis von `pv_prognose_rest_heute` |
| `sensor.demo_stromzaehler_bezug` | Zähler (`total_increasing`) | kWh | Monoton steigend, nur wenn Gesamtwirkleistung > PV-Leistung |
| `sensor.demo_stromzaehler_einspeisung` | Zähler (`total_increasing`) | kWh | Monoton steigend, nur wenn PV-Leistung > Gesamtwirkleistung |
| `sensor.demo_balkonkraftwerk_pv_leistung` | Standard | W | Eigenes, kleineres Zweitsystem (Steckersolargerät): Modul-Rohleistung bis 950 W, fest gekappt auf 800 W Wechselrichter-Ausgang; eigenes Tagesfenster (morgenärmer, südwestlich wirkend) statt Kopie der Dachanlage |
| `sensor.demo_balkonkraftwerk_ladeleistung` | Standard | W | >0 nur während der Speicher lädt (tagsüber, solange SoC < 100 %) |
| `sensor.demo_balkonkraftwerk_entladeleistung` | Standard | W | >0 nur während der Speicher entlädt (nachts, bis SoC den Entladeschutz erreicht) |
| `sensor.demo_balkonkraftwerk_speicher_soc` | Standard | % | Ladezustand 0–100 %, tagsüber steigend/nachts fallend |
| `sensor.demo_balkonkraftwerk_speicher_stand` | Standard | kWh | Absoluter Energieinhalt des 2-kWh-Speichers (`SoC × Kapazität`), kein Zähler |
| `sensor.demo_balkonkraftwerk_hausabgabe` | Standard | W | Tatsächlich Richtung Haussteckdose abgegebene Leistung (PV-Überschuss bei vollem Speicher + Speicher-Entladung); mindert `load_power`, bevor Bezug/Einspeisung berechnet werden — wie ein reales Balkonkraftwerk ohne eigenen Zähler/eigene Einspeisevergütung |
| `sensor.demo_balkonkraftwerk_ertrag_heute` | Zähler (`total_increasing`) | kWh | PV-Ertrag des Balkonkraftwerks, **resettet täglich um Mitternacht auf 0** (wie bei den meisten Mikrowechselrichter-Apps) |
| `sensor.demo_balkonkraftwerk_ertrag_gesamt` | Zähler (`total_increasing`) | kWh | Derselbe PV-Ertrag, monoton steigend seit „Inbetriebnahme", kein Reset |
| `sensor.demo_balkonkraftwerk_geladen_heute` | Zähler (`total_increasing`) | kWh | Lademenge des Speichers, Tages-Reset wie `ertrag_heute` |
| `sensor.demo_balkonkraftwerk_geladen_gesamt` | Zähler (`total_increasing`) | kWh | Lademenge des Speichers, monoton steigend, kein Reset |
| `sensor.demo_balkonkraftwerk_entladen_heute` | Zähler (`total_increasing`) | kWh | Entlademenge des Speichers, Tages-Reset wie `ertrag_heute` |
| `sensor.demo_balkonkraftwerk_entladen_gesamt` | Zähler (`total_increasing`) | kWh | Entlademenge des Speichers, monoton steigend, kein Reset |
| `binary_sensor.demo_balkonkraftwerk_online` | Schalter | — | An, solange PV liefert oder der Speicher entlädt; aus, sobald der Speicher nachts den Entladeschutz erreicht (bis zur nächsten Sonneneinstrahlung) |
| `sensor.demo_wasserzaehler` | Zähler (`total_increasing`) | m³ | Monoton steigend, sparsame zufällige Zuwächse tagsüber |
| `binary_sensor.demo_praesenz_wohnzimmer` | Schalter | — | Heim/Weg mit tageszeitabhängiger Wahrscheinlichkeit und Trägheit (kein Umschalten alle paar Minuten) |
| `binary_sensor.demo_regensensor` | Schalter | — | Ein-/mehrmalige Regenfenster an manchen Tagen, Wahrscheinlichkeit steigt mit der Bewölkung dieses Tages |

Waschmaschine, Spülmaschine, Trockner und Wallbox haben je zwei bzw. drei
Entitäten — Momentanleistung (W), Energiezähler (kWh) und bei den drei
Haushaltsgeräten zusätzlich ein reiner An/Aus-Schalter —, wie es reale
Steckdosen-/Gerätemesser typischerweise auch liefern. Die Wallbox-Leistung
fließt zusätzlich in die Gesamtwirkleistung und damit auch in Bezug/
Einspeisung ein, genau wie beim echten Netzanschluss.

Zwei Zähler statt eines: reale bidirektionale Zähler führen Bezug und
Einspeisung getrennt (beide für sich genommen monoton steigend, nie
negativ) — welcher der beiden gerade wächst, ergibt sich aus dem
Vorzeichen von Gesamtwirkleistung minus PV-Leistung **minus der
Balkonkraftwerk-Hausabgabe** zu jedem Zeitpunkt.

Das Balkonkraftwerk ist ein eigenständiges Zweitsystem neben der großen
Dachanlage, kein Ersatz dafür: kleinere Modulleistung mit fester
Wechselrichter-Kappung (800 W) und ein 2-kWh-Speicher davor, der tagsüber
priorisiert aus der Balkon-PV lädt (nicht aus der Dachanlage — beide Systeme
sind unabhängige Geräte ohne gemeinsame Steuerung) und nachts mit
schwankender, aber im Schnitt konstanter Leistung entlädt. Die Dachanlage
(`pv_leistung`/`pv_ertrag`) bleibt davon unberührt und wird weiterhin
unverändert separat verrechnet. Reicht der Speicher nicht über die ganze
Nacht (in der Simulation meist gegen 2–3 Uhr der Fall), schaltet
`balkonkraftwerk_online` auf Aus, bis am nächsten Morgen wieder PV-Leistung
anliegt — ein bewusst realistischer Effekt der gewählten Speichergröße,
kein Fehlerzustand.

Gesamtwirkleistung, alle Verbraucher (inkl. Wallbox), PV-Leistung/-Ertrag,
beide Stromzähler, Innenluftfeuchte, Wind, Regensensor, CO2-Intensität, die
PV-Prognose-Sensoren und alle Balkonkraftwerk-Entitäten entstehen in einem
einzigen gemeinsamen Simulationsdurchlauf (`simulate_household()`) statt
unabhängig voneinander — die Zählerstände sind daher die exakte Integration
derselben Leistungswerte, die auch als Sensoren geschrieben werden, nicht
separat gewürfelte Näherungen; die An/Aus-Schalter sind reine
Zustandsübergänge derselben Leistungswerte statt eigener Zufallslogik.

Bewölkung, Temperaturabweichung und ein Wind-Tageswert werden pro
Kalendertag einmal berechnet und über einen einfachen AR(1)-Prozess an den
Vortag gekoppelt, damit Außentemperatur, Luftfeuchte, Wind, PV-Leistung und
CO2-Intensität wie zusammenhängendes Wetter wirken statt unabhängig-zufällig
zu schwanken; die Regenwahrscheinlichkeit eines Tages hängt an derselben
Bewölkung.

Die PV-Prognose-Sensoren nutzen bewusst nicht die tatsächliche, spätere
Bewölkung, sondern schätzen sie mit einem eigenen Zufallsfehler pro
Kalendertag (`pv_prognose_rest_heute` für den laufenden Tag anhand der
heutigen Bewölkung + Schätzfehler; `pv_prognose_morgen` zusätzlich über
eine AR(1)-Fortschreibung der heutigen Bewölkung + eigenem Schätzfehler) —
eine Prognose, die exakt träfe, wäre keine Prognose mehr. Die gestern für
"morgen" geschätzte Tagessumme wird am nächsten Morgen automatisch zur
Basis von `pv_prognose_rest_heute`, ohne erneut geschätzt zu werden.

Der aktuelle, noch laufende Monat landet — wie bei echten Daten — im Hot
Buffer statt in einem Monatsarchiv; die Entität zeigt also auch „heute"
plausible, aktuelle Werte.

## Voraussetzungen

- Python-Umgebung der App (`addon/.venv`, siehe
  [development.md](development.md)) — das Skript importiert `app.storage.*`
  direkt, ein laufender Server ist dafür nicht nötig.
- Ein **leeres oder eigenes** Zieldatenverzeichnis. Nicht gegen das
  Datenverzeichnis einer gleichzeitig laufenden Instanz ausführen — SQLite-
  Zugriffe aus zwei Prozessen parallel sind nicht vorgesehen.

## Verwendung

```bash
cd addon
.venv/bin/python3 scripts/generate_demo_data.py --data-dir /pfad/zum/datenverzeichnis
```

Erzeugt mit den Standardwerten 6 Monate Historie für alle 41 Demo-
Entitäten in einem frischen (oder leeren) Zielverzeichnis.

Weitere Beispiele:

```bash
# Nur 3 Monate Historie statt der Standard-6
.venv/bin/python3 scripts/generate_demo_data.py --data-dir /pfad/zum/datenverzeichnis --months 3

# Anderer Zufalls-Seed für andere, aber weiterhin reproduzierbare Werte
.venv/bin/python3 scripts/generate_demo_data.py --data-dir /pfad/zum/datenverzeichnis --seed 7

# Bestehende Demo-Instanz zuerst bereinigen und direkt neu erzeugen
.venv/bin/python3 scripts/generate_demo_data.py --data-dir /pfad/zum/datenverzeichnis --clean --months 12
```

| Option | Standard | Bedeutung |
| --- | --- | --- |
| `--data-dir` | *(erforderlich)* | Zielverzeichnis; wird bei Bedarf angelegt |
| `--months` | `6` | Wie viele Monate Historie erzeugt werden (Rechnung: Monate × 30 Tage zurück von jetzt) |
| `--tz` | `Europe/Berlin` | IANA-Zeitzone für Monats-/Tagesgrenzen |
| `--seed` | `42` | Zufalls-Seed — gleicher Seed erzeugt reproduzierbar dieselben Werte |
| `--clean` | *(aus)* | Vorhandene `demo_*`-Entitäten im Zielverzeichnis vor dem Erzeugen sauber entfernen (für wiederholte Läufe) |
| `--append` | *(aus)* | Statt der kompletten Historie nur die Werte seit dem letzten Lauf ergänzen (`--months` wird dabei ignoriert) — siehe [Lebende Demo-Instanz](#lebende-demo-instanz-append). Schließt sich mit `--clean` gegenseitig aus |
| `--clear {values,entities}` | *(aus)* | Eigenständige Aktion statt Erzeugen — siehe [Demo-Daten löschen](#demo-daten-löschen---clear) |

Am Ende zeigt das Skript eine kurze Zusammenfassung (Anzahl geschriebener
Werte je Entität, Ergebnis des anschließenden Indexabgleichs) — außer bei
`--clear`, das nur die Anzahl der betroffenen Entitäten meldet und sich
danach sofort beendet.

## Demo-Daten einbinden

### Lokal, ohne Docker (venv)

Zeigt der Server bereits auf ein Verzeichnis, kann direkt dorthin erzeugt
werden — dann nur den Server (neu) starten, sofern er nicht schon lief:

```bash
cd addon
.venv/bin/python3 scripts/generate_demo_data.py --data-dir /tmp/zeitarchiv-data
ZEITARCHIV_DATA_DIR=/tmp/zeitarchiv-data ZEITARCHIV_API_TOKEN=devtoken \
  .venv/bin/uvicorn app.main:app --port 8127
```

Läuft dort bereits eine Instanz auf genau diesem Verzeichnis: zuerst
stoppen, Skript ausführen, danach erst wieder starten.

### Lokal mit Docker Compose

`docker-compose.dev.yml` bindet `ZEITARCHIV_DATA_DIR=/data` an ein benanntes
Volume, kein Host-Verzeichnis — das Skript läuft aber nativ auf dem Host und
kann dieses Volume nicht direkt beschreiben. Einfachste Lösung: für den
Demo-Lauf stattdessen ein Host-Verzeichnis mounten.

1. Demo-Daten lokal erzeugen: `.venv/bin/python3 scripts/generate_demo_data.py --data-dir ./demo-data`
2. In `docker-compose.dev.yml` (oder einer lokalen Kopie/einem Override)
   `volumes: - ./demo-data:/data` statt des benannten Volumes eintragen.
3. `docker compose -f docker-compose.dev.yml up --build`

### Reale Home-Assistant-/Supervisor-Installation

Nicht der vorgesehene Weg — das Datenverzeichnis eines produktiven Add-ons
sollte nicht extern beschrieben werden. Falls für einen Demo-/Vorführzweck
trotzdem gewünscht: Add-on vorher stoppen, das Skript mit `--data-dir` direkt
auf den Add-on-Datenpfad zeigen lassen, anschließend das Add-on wieder
starten. Ein vorheriges Backup (**System → Backup / Restore**) ist in diesem
Fall dringend empfohlen.

## Demo-Daten neu erzeugen (`--clean`)

`--clean` bereinigt vor dem Neuschreiben die Werte aller 41 `demo_*`-
Entitäten — wie **Housekeeping → Speicherplatz** in der App, nur für alle
Demo-Entitäten auf einmal, ohne die App zu öffnen. Bewusst
`delete_all_values()` statt `delete_entity()`: die Entitäten selbst bleiben
während des ganzen Laufs durchgehend im Index bestehen (nur die Werte werden
kurz geleert und direkt danach neu befüllt) — Dashboards, Charts und
Vergleichstabellen, die du auf diesen Entity-IDs aufgebaut hast, bleiben
dadurch unangetastet und zeigen nach dem Lauf einfach die neuen Werte, statt
zwischenzeitlich eine unbekannte Entität zu referenzieren.

Eine einzelne Demo-Entität komplett entfernen (inkl. Konfiguration, nicht
nur Werte) geht nur über die Oberfläche: Entität öffnen → Zahnrad-Symbol →
**Entität entfernen** (siehe
[user-guide.md](user-guide.md#entität-konfigurieren)). Für alle Demo-
Entitäten auf einmal siehe [Demo-Daten löschen](#demo-daten-löschen---clear).

## Demo-Daten löschen (`--clear`)

`--clear` ist eine eigenständige Aktion statt einer Vorbereitung zum
Erzeugen: Das Skript löscht und beendet sich danach sofort, ohne im
Anschluss neue Historie zu würfeln — anders als `--clean`, das dieselbe
Bereinigung nur als ersten Schritt vor einer sofortigen Neuerzeugung
durchführt. Schließt sich mit `--clean`, `--append` und `--months` aus.

```bash
# Nur Werte löschen, Entitäten/Konfiguration bleiben bestehen
.venv/bin/python3 scripts/generate_demo_data.py --data-dir /pfad/zum/datenverzeichnis --clear values

# Demo-Entitäten vollständig entfernen (inkl. Konfiguration)
.venv/bin/python3 scripts/generate_demo_data.py --data-dir /pfad/zum/datenverzeichnis --clear entities

# Ohne Wert: identisch zu --clear entities
.venv/bin/python3 scripts/generate_demo_data.py --data-dir /pfad/zum/datenverzeichnis --clear
```

| Wert | Wirkung |
| --- | --- |
| `values` | Entfernt nur die Werte aller vorhandenen `demo_*`-Entitäten (`delete_all_values()`, derselbe Mechanismus wie der Bereinigungsschritt von `--clean`) — Entität und Konfiguration bleiben im Index bestehen, Dashboards/Referenzen auf diese Entity-IDs bleiben gültig, zeigen danach aber keine Werte mehr. |
| `entities` *(auch Default ohne Wert)* | Entfernt die `demo_*`-Entitäten vollständig inkl. Konfiguration (`delete_entity()`) — Dashboards/Referenzen auf diese Entity-IDs zeigen danach ins Leere, wie beim manuellen Entfernen über die Oberfläche, nur für alle Demo-Entitäten auf einmal. |

## Lebende Demo-Instanz (`--append`)

`--append` ergänzt eine bereits vorhandene Demo-Instanz um die Werte seit dem
letzten Lauf, statt die komplette Historie neu zu würfeln — regelmäßig
ausgeführt (z. B. per Cron) bleibt eine Demo-Instanz so ein "lebendes"
System, das nie hinter das aktuelle Datum zurückfällt, ohne bei jedem Lauf
Monate an Daten neu zu berechnen:

```bash
# z. B. alle 15 Minuten per Cron
.venv/bin/python3 scripts/generate_demo_data.py --data-dir /pfad/zum/datenverzeichnis --append
```

- Der Anker "bis wohin wurde zuletzt simuliert" ist der letzte Zeitstempel
  von `sensor.demo_gesamtwirkleistung` (bei jedem Simulationsschritt
  geschrieben, anders als Schalter/Zähler mit absichtlich sporadischen
  Schreibvorgängen). Ist die Instanz bereits aktuell, beendet sich das
  Skript ohne etwas zu schreiben.
- Zähler (`_energie`, `_ertrag`, Stromzähler, Wasserzähler,
  Balkonkraftwerk-`_gesamt`-Zähler und Speicherstand) und Schalter-Zustände
  knüpfen dabei an ihren zuletzt tatsächlich gespeicherten Wert an — kein
  Zählersprung/-rücksetzer an der Anschlussstelle. Überlappende Zeitstempel
  wären ohnehin unkritisch: `import_rows()` dedupliziert danach. Die
  Balkonkraftwerk-`_heute`-Zähler knüpfen nur an, wenn der letzte Lauf am
  selben Kalendertag endete — sonst beginnt der neue Tag ohnehin bei 0, wie
  bei einem echten Tages-Reset.
- Findet `--append` kein vorhandenes `sensor.demo_gesamtwirkleistung` (leeres
  Zielverzeichnis), fällt es automatisch auf eine normale Vollerzeugung mit
  `--months` zurück.
- Läuft auf demselben Datenverzeichnis wie eine bereits gestartete Instanz
  nicht parallel dazu ausführen (siehe [Voraussetzungen](#voraussetzungen))
  — ein Cron-Job gehört also auf ein Datenverzeichnis, dessen Server währenddessen
  gestoppt ist, oder muss dessen Neustart selbst mit einplanen.

## Grenzen

- Die Werte sind plausibel, aber nicht physikalisch exakt (grobe
  Tageslichtlänge, keine echte Wetterhistorie, vereinfachte
  Geräteprofile).
- Gedacht für Demo-/Test-Datenverzeichnisse, nicht als Ergänzung zu echten
  Home-Assistant-Daten in derselben Instanz — die `demo_`-Entitäten stehen
  gleichberechtigt neben echten Entitäten und werden nicht automatisch
  unterschieden, außer am Namen/Präfix.
- Die PV-Prognose-Sensoren (`pv_prognose_rest_heute`/`_morgen`) und
  `co2_intensitaet` sind reine `measurement`-Sensoren ohne Zählerstand und
  werden bei `--append` bewusst nicht fortgesetzt (anders als die
  `total_increasing`-Zähler) — bei einem Fortsetzungslauf mitten am Tag
  startet `pv_prognose_rest_heute` mit einer frischen Tagesschätzung statt
  exakt dort weiterzumachen, wo der letzte Lauf aufgehört hat. Kosmetischer
  Sprung, kein Zählersprung.
