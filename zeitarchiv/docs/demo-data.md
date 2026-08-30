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

25 Entitäten, thematisch ein einzelner Haushalt mit PV-Anlage und Wallbox,
alle mit dem Präfix `demo_` in der Entity-ID (leicht wiederzuerkennen und
gezielt löschbar):

| Entität | Typ | Einheit | Muster |
| --- | --- | --- | --- |
| `sensor.demo_wohnzimmer_temperatur` | Standard | °C | Tageszyklus um 21 °C, leichtes Rauschen |
| `sensor.demo_aussentemperatur` | Standard | °C | Jahreszeitlich (Mitteleuropa-Profil) + Tageszyklus + mehrtägig korreliertes „Wetter" |
| `sensor.demo_luftfeuchte` | Standard | % | Außen, an Außentemperatur/Bewölkung gekoppelt |
| `sensor.demo_wohnzimmer_luftfeuchte` | Standard | % | Innen, gedämpfter als draußen, lose an die Außenluftfeuchte gekoppelt |
| `sensor.demo_wind` | Standard | km/h | Tageswert (lose an Bewölkung gekoppelt: windiger bei mehr Wolken) + Tagesgang + gelegentliche Böen |
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
| `sensor.demo_stromzaehler_bezug` | Zähler (`total_increasing`) | kWh | Monoton steigend, nur wenn Gesamtwirkleistung > PV-Leistung |
| `sensor.demo_stromzaehler_einspeisung` | Zähler (`total_increasing`) | kWh | Monoton steigend, nur wenn PV-Leistung > Gesamtwirkleistung |
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
Vorzeichen von Gesamtwirkleistung minus PV-Leistung zu jedem Zeitpunkt.

Gesamtwirkleistung, alle Verbraucher (inkl. Wallbox), PV-Leistung/-Ertrag,
beide Stromzähler, Innenluftfeuchte, Wind und der Regensensor entstehen in
einem einzigen gemeinsamen Simulationsdurchlauf (`simulate_household()`)
statt unabhängig voneinander — die Zählerstände sind daher die exakte
Integration derselben Leistungswerte, die auch als Sensoren geschrieben
werden, nicht separat gewürfelte Näherungen; die An/Aus-Schalter sind reine
Zustandsübergänge derselben Leistungswerte statt eigener Zufallslogik.

Bewölkung, Temperaturabweichung und ein Wind-Tageswert werden pro
Kalendertag einmal berechnet und über einen einfachen AR(1)-Prozess an den
Vortag gekoppelt, damit Außentemperatur, Luftfeuchte, Wind und PV-Leistung
wie zusammenhängendes Wetter wirken statt unabhängig-zufällig zu schwanken;
die Regenwahrscheinlichkeit eines Tages hängt an derselben Bewölkung.

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

| Option | Standard | Bedeutung |
| --- | --- | --- |
| `--data-dir` | *(erforderlich)* | Zielverzeichnis; wird bei Bedarf angelegt |
| `--months` | `6` | Wie viele Monate Historie erzeugt werden (Rechnung: Monate × 30 Tage zurück von jetzt) |
| `--tz` | `Europe/Berlin` | IANA-Zeitzone für Monats-/Tagesgrenzen |
| `--seed` | `42` | Zufalls-Seed — gleicher Seed erzeugt reproduzierbar dieselben Werte |
| `--clean` | *(aus)* | Vorhandene `demo_*`-Entitäten im Zielverzeichnis vor dem Erzeugen sauber entfernen (für wiederholte Läufe) |
| `--append` | *(aus)* | Statt der kompletten Historie nur die Werte seit dem letzten Lauf ergänzen (`--months` wird dabei ignoriert) — siehe [Lebende Demo-Instanz](#lebende-demo-instanz-append). Schließt sich mit `--clean` gegenseitig aus |

Am Ende zeigt das Skript eine kurze Zusammenfassung (Anzahl geschriebener
Werte je Entität, Ergebnis des anschließenden Indexabgleichs).

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

`--clean` bereinigt vor dem Neuschreiben die Werte aller 25 `demo_*`-
Entitäten — wie **Einstellungen → Speicherplatz** in der App, nur für alle
Demo-Entitäten auf einmal, ohne die App zu öffnen. Bewusst
`delete_all_values()` statt `delete_entity()`: die Entitäten selbst bleiben
während des ganzen Laufs durchgehend im Index bestehen (nur die Werte werden
kurz geleert und direkt danach neu befüllt) — Dashboards, Charts und
Vergleichstabellen, die du auf diesen Entity-IDs aufgebaut hast, bleiben
dadurch unangetastet und zeigen nach dem Lauf einfach die neuen Werte, statt
zwischenzeitlich eine unbekannte Entität zu referenzieren.

Eine einzelne Demo-Entität komplett entfernen (inkl. Konfiguration, nicht
nur Werte) geht weiterhin nur über die Oberfläche: Entität öffnen →
Zahnrad-Symbol → **Entität entfernen** (siehe
[user-guide.md](user-guide.md#entität-konfigurieren)).

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
- Zähler (`_energie`, `_ertrag`, Stromzähler, Wasserzähler) und Schalter-
  Zustände knüpfen dabei an ihren zuletzt tatsächlich gespeicherten Wert an
  — kein Zählersprung/-rücksetzer an der Anschlussstelle. Überlappende
  Zeitstempel wären ohnehin unkritisch: `import_rows()` dedupliziert danach.
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
