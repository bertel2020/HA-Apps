# Änderungsnotiz: Demo-Daten-Erweiterungen (Entwurf, noch nicht ins CHANGELOG übernommen)

Betrifft: `scripts/generate_demo_data.py`, `docs/demo-data.md`

> Hinweis: Diese Datei war zwischenzeitlich aus dem Arbeitsverzeichnis
> verschwunden (kein Git-Repo hier, daher keine Wiederherstellung möglich)
> und wurde beim Nachtrag 4 aus dem Gesprächsverlauf rekonstruiert. Inhalt
> bis Nachtrag 3 sollte unverändert dem vorherigen Stand entsprechen.

## Was wurde gemacht (Ursprung: Balkonkraftwerk mit Speicher)

`scripts/generate_demo_data.py` um 13 neue Demo-Entitäten für ein
Balkonkraftwerk mit Speicher erweitert — zusätzlich zur bestehenden
Dachanlage, nicht als Ersatz:

- `sensor.demo_balkonkraftwerk_pv_leistung` (W) — eigenes Zweitsystem,
  800 W Wechselrichter-Kappung, eigenes Tagesfenster/Ausrichtung
- `sensor.demo_balkonkraftwerk_ladeleistung` (W)
- `sensor.demo_balkonkraftwerk_entladeleistung` (W)
- `sensor.demo_balkonkraftwerk_speicher_soc` (%)
- `sensor.demo_balkonkraftwerk_speicher_stand` (kWh, 2-kWh-Speicher)
- `sensor.demo_balkonkraftwerk_hausabgabe` (W)
- `sensor.demo_balkonkraftwerk_ertrag_heute` (kWh, `total_increasing`, **Tages-Reset**)
- `sensor.demo_balkonkraftwerk_ertrag_gesamt` (kWh, `total_increasing`)
- `sensor.demo_balkonkraftwerk_geladen_heute` (kWh, `total_increasing`, Tages-Reset)
- `sensor.demo_balkonkraftwerk_geladen_gesamt` (kWh, `total_increasing`)
- `sensor.demo_balkonkraftwerk_entladen_heute` (kWh, `total_increasing`, Tages-Reset)
- `sensor.demo_balkonkraftwerk_entladen_gesamt` (kWh, `total_increasing`)
- `binary_sensor.demo_balkonkraftwerk_online`

## Simulationslogik (Balkonkraftwerk)

- Eigene PV-Kurve (`gen_balkon_pv_power()`): Modul-Rohleistung bis 950 W,
  fest gekappt auf 800 W Wechselrichter-Ausgang; nutzt dieselbe
  `WeatherContext` (Bewölkung) wie die Dachanlage, aber ein morgenärmeres
  Tagesfenster, damit es nicht wie eine Kopie wirkt.
- Speicher (2 kWh) lädt tagsüber priorisiert aus der Balkon-PV bis SoC
  100 %, PV-Überschuss danach geht direkt als Hausabgabe raus. Nachts
  entlädt der Speicher mit ~220 W (± Rauschen) bis zum Entladeschutz bei
  SoC 5 %.
  - Erster Parameterwert war 120 W — damit leerte sich der Speicher in
    keiner Simulation (2 kWh reichen bei 120 W über jede Nacht), wodurch
    `balkonkraftwerk_online` faktisch nie umgeschaltet hätte. Auf 220 W
    erhöht, damit der Speicher realistisch meist gegen 2–3 Uhr leer ist und
    der Online-Sensor sichtbare Übergänge erzeugt (getestet: 345
    Transitionen über 6 simulierte Monate).
- `hausabgabe` (PV-Direktverbrauch bei vollem Speicher + Entladung) wird
  von `load_w` abgezogen, **bevor** `stromzaehler_bezug`/`-einspeisung`
  berechnet werden — mindert also den Netzbezug wie ein reales
  Balkonkraftwerk ohne eigenen Zähler/eigene Einspeisevergütung. Die
  Dachanlage bleibt davon unberührt und wird weiterhin unabhängig
  verrechnet.
- `_heute`-Zähler resetten um Mitternacht (Kalendertagwechsel in der
  Simulationsschleife), `_gesamt`-Zähler laufen unverändert durch.

## `--append`-Fortsetzbarkeit (Balkonkraftwerk)

Neue Seed-Helfer ergänzt, analog zu den bestehenden Mustern:

- `_read_balkon_counter_seed()` — liest `speicher_stand`, `ertrag_gesamt`,
  `geladen_gesamt`, `entladen_gesamt` als Fortsetzungspunkt.
- `_read_balkon_heute_seed()` — liest zusätzlich den Kalendertag des
  letzten `ertrag_heute`-Werts; die `_heute`-Zähler knüpfen nur an, wenn
  der letzte Lauf am selben Tag endete, sonst starten sie bei 0 (wie ein
  echter Tages-Reset).
- Online-Zustand wird wie der Regensensor über `_read_last_value()`
  fortgesetzt.

## Getestet (Balkonkraftwerk)

- Vollerzeugung (`--months 1`, `--months 2`, isoliert `simulate_household()`
  über 5 Tage und 6 Monate) — keine Fehler, alle 38 Entitäten inkl. Reports
  im Indexabgleich ohne verbleibende Abweichungen.
- `--append` auf frisch erzeugtem Datenverzeichnis — meldet korrekt "bereits
  aktuell", wenn `now()` sich seit dem letzten Lauf kaum verändert hat.
- Seed-Funktionen (`_read_balkon_counter_seed`, `_read_balkon_heute_seed`,
  Online-Seed) einzeln gegen ein erzeugtes Datenverzeichnis verifiziert —
  liefern plausible Werte (SoC-Zähler, Tagesdatum, Zähler passend zum
  letzten geschriebenen Wert).

## Nachtrag: `--clear {values,entities}`

Zusätzlich eine eigenständige Lösch-Aktion ergänzt (unabhängig vom
Balkonkraftwerk-Thema, aber im selben Skript):

- `--clear values` — löscht nur die Werte aller vorhandenen `demo_*`-
  Entitäten (`delete_all_values()`, wie der Bereinigungsschritt von
  `--clean`), Entität/Konfiguration bleibt bestehen. Anders als `--clean`
  folgt **keine** anschließende Neuerzeugung — das Skript beendet sich
  danach sofort.
- `--clear entities` — entfernt die `demo_*`-Entitäten vollständig inkl.
  Konfiguration (`delete_entity()`, neu importiert aus
  `app.storage.entity_removal`). Bisher ging das nur einzeln über die
  Oberfläche.
- Schließt sich mit `--clean`/`--append` aus (`parser.error()` bei
  Kombination); ignoriert `--months`/`--seed`, da nichts erzeugt wird.
- Getestet: `--clear values` (Entitäten bleiben mit `last_ts=NULL`
  bestehen), `--clear entities` (Index danach leer), Fehlerfälle
  `--clear ... --clean` und `--clear ... --append` (beide korrekt
  abgelehnt).
- `docs/demo-data.md` um Abschnitt „Demo-Daten löschen (--clear)" ergänzt,
  Optionstabelle und Verweis vom `--clean`-Abschnitt aktualisiert.

### Nachtrag 2: `--clear` ohne Wert defaultet auf `entities`

Auf Wunsch vereinfacht: `--clear` (`nargs="?"`, `const="entities"`) kann
jetzt ohne folgenden Wert aufgerufen werden — dann gilt direkt `entities`
(keine eigene dritte `both`-Option mehr, da sie ohnehin identisch zu
`entities` gewesen wäre — `delete_entity()` entfernt Werte und
Konfiguration schon in einem Aufruf).

- Getestet: bare `--clear` auf einer befüllten Testinstanz → alle
  Demo-Entitäten vollständig entfernt (Index danach leer), identisch zu
  `--clear entities`.
- `docs/demo-data.md` entsprechend vereinfacht (nur noch zwei Werte
  `values`/`entities`, `entities` als Default ohne Wert markiert).

## Nachtrag 3: CO2-Intensität + PV-Prognose

Zwei weitere thematisch verwandte Ergänzungen (auf Wunsch, per Vorschlag
vorab abgestimmt):

- `sensor.demo_co2_intensitaet` (g/kWh, `measurement`) — angelehnt an
  CO2-Signal-/ElectricityMap-Sensoren: Grundlast nachts (~340 g/kWh),
  Einbruch mittags durch PV-Einspeisung ins Netz, leichter Anstieg zur
  Abendspitze; zusätzlich niedriger an sonnigen/windigen Tagen (nutzt
  dieselbe `WeatherContext` wie PV/Wind, damit "grüne" Tage netzseitig
  konsistent sind). Neue Funktion `gen_co2_intensity()`.
- `sensor.demo_pv_prognose_rest_heute` / `sensor.demo_pv_prognose_morgen`
  (kWh, `measurement`) — wie Forecast.Solar-Sensoren, nur für die
  **Dachanlage** (nicht das Balkonkraftwerk, um den Umfang begrenzt zu
  halten). Neue Konstanten `PV_PEAK_W`/`PV_SHAPE_INTEGRAL` (aus dem bisher
  in `gen_pv_power()` inline stehenden `5200` extrahiert) und Funktion
  `pv_forecast_kwh(day_of_year, cloud_estimate)`, die dieselbe
  Glockenkurvenform analytisch über den Tag integriert statt sie
  nachzusimulieren.
  - Prognosen sind bewusst fehlerbehaftet (eigener Zufallsfehler ±15–20 %
    auf die geschätzte Bewölkung) statt exaktes Vorauswissen — sonst wären
    es keine Prognosen.
  - `pv_prognose_morgen` wird einmal pro Kalendertag geschätzt (AR(1)-
    Fortschreibung der heutigen Bewölkung + eigener Fehler) und bleibt
    über den Tag konstant. Am nächsten Tag wird dieser Wert automatisch zur
    Basis von `pv_prognose_rest_heute` — kein zweites Schätzen für
    "heute", die gestrige Morgen-Prognose IST bereits die Schätzung für
    heute.
  - `pv_prognose_rest_heute` sinkt über den Tag: `Tagesschätzung minus
    bisher tatsächlich eingefahrener PV-Ertrag`, floor bei 0.
  - Bewusst NICHT über `--append` hinweg fortgeführt (kein Seed-
    Mechanismus wie bei den `total_increasing`-Zählern) — beide sind reine
    `measurement`-Sensoren ohne Zählerstand, ein "Reset" auf eine frische
    Tagesschätzung beim Fortsetzen mitten am Tag ist unkritisch (anders als
    ein Zählersprung). In `docs/demo-data.md` unter „Grenzen" vermerkt.
- Damit erzeugte das Skript zu diesem Zeitpunkt 41 statt 38 `demo_*`-
  Entitäten.
- Getestet: Vollerzeugung über 2 Monate (41 Entitäten im Indexabgleich
  ohne verbleibende Abweichungen), Stichprobe der Archivwerte für alle drei
  neuen Sensoren (CO2 233–383 g/kWh, PV-Prognose 0–46 kWh/Tag plausibel,
  `pv_prognose_morgen` innerhalb eines Tages konstant wie vorgesehen).
- `docs/demo-data.md` aktualisiert: Entitätentabelle (3 neue Zeilen),
  Entitätenzahl 38→41 an allen Stellen, Absatz zum gemeinsamen
  Simulationsdurchlauf und zur Wetterkopplung erweitert, neuer Absatz zur
  Prognoselogik, neuer Punkt unter „Grenzen".

## Nachtrag 4: Heimspeicher + Balkonkraftwerk-Kapazität in Wh

Zweiter, größerer Speicher ergänzt sowie eine fehlende Kennzahl beim
bestehenden Balkonkraftwerk-Speicher nachgetragen:

- `sensor.demo_balkonkraftwerk_speicher_kapazitaet` (**Wh**, `measurement`,
  konstant 2000) — bewusst in Wh statt kWh wie `speicher_stand`, als
  realistisches Beispiel für unterschiedliche Einheiten zwischen
  thematisch verwandten Sensoren (echte Hersteller-Angaben schwanken hier
  tatsächlich zwischen Wh und kWh).
- Neuer Heimspeicher (10 kWh, 10 neue Entitäten): `sensor.demo_heimspeicher_kapazitaet`
  (kWh, konstant), `_ladeleistung`/`_entladeleistung` (W), `_speicher_soc`
  (%), `_speicher_stand` (kWh), `_geladen_heute`/`_geladen_gesamt`,
  `_entladen_heute`/`_entladen_gesamt` (kWh, `total_increasing`, je mit
  Tages-Reset-Variante wie beim Balkonkraftwerk), `binary_sensor.demo_heimspeicher_online`.
- Neue Konstanten `HEIM_CAPACITY_KWH=10.0`, `HEIM_MAX_CHARGE_W`/
  `HEIM_MAX_DISCHARGE_W=3000.0`, `HEIM_MIN_SOC=0.05`.
- Neue Hilfsfunktion `gen_constant_series()` für reine Kennzahl-Sensoren
  ohne Zeitverlauf (verwendet für beide neuen Kapazitäts-Entitäten) —
  liefert denselben konstanten Wert über den ganzen erzeugten Zeitraum,
  damit auch diese Entitäten wie alle anderen eine durchgehende Historie
  haben.
- **Unterschied zum Balkonkraftwerk:** Der Heimspeicher hat keine eigene
  PV, sondern hängt direkt an der Dachanlage (Hybrid-Wechselrichter-Logik):
  lädt aus deren Überschuss nach Abzug des Hausverbrauchs (netto nach der
  bereits abgezogenen Balkonkraftwerk-Hausabgabe: `net_remaining_load_w =
  load_w - balkon_hausabgabe_w`), entlädt bei PV-Defizit. Dadurch fließen
  Lade-/Entladeleistung mit eigenem Vorzeichen in `grid_flow_w` ein (`+
  heim_charge_w - heim_discharge_w`), statt wie beim Balkonkraftwerk über
  eine einzelne `hausabgabe`-Größe.
- `_heute`-Zähler-Tagesreset wie beim Balkonkraftwerk, eigener Tages-Anker
  `heim_day` (dieselbe `day_key`-Variable pro Schritt wiederverwendet statt
  erneut berechnet).
- `--append`-Seeds analog zum Balkonkraftwerk ergänzt:
  `_read_heim_counter_seed()` (SoC/`_gesamt`-Zähler),
  `_read_heim_heute_seed()` (Tagesanker ist hier
  `sensor.demo_heimspeicher_geladen_heute`, da kein `ertrag_heute`-Äquivalent
  existiert — keine eigene PV).
- Damit erzeugt das Skript jetzt 52 statt 41 `demo_*`-Entitäten.
- Getestet: Vollerzeugung über 2 Monate (52 Entitäten im Indexabgleich
  ohne verbleibende Abweichungen); Stichprobe der Archivwerte (SoC 5–100 %,
  Lade-/Entladeleistung 0–3000 W, beide Kapazitäts-Sensoren korrekt
  konstant bei 10 kWh bzw. 2000 Wh); `_read_heim_counter_seed()`/
  `_read_heim_heute_seed()` gegen eine erzeugte Testinstanz verifiziert
  (plausible SoC-/Zähler-/Tagesdatum-Werte).
- `docs/demo-data.md` aktualisiert: neue Tabellenzeilen (Kapazität
  Balkonkraftwerk in Wh, 10 neue Heimspeicher-Zeilen), Entitätenzahl
  41→52 an allen Stellen, `grid_flow_w`-Formel und Simulationsdurchlauf-
  Absatz erweitert, neuer Absatz zur Heimspeicher-Logik, `--append`-
  Abschnitt um Heimspeicher-Zähler ergänzt.

## Für ein späteres CHANGELOG.md-Update (Vorschlag, noch nicht eingetragen)

> Demo-Daten-Generator: 13 neue Entitäten für ein Balkonkraftwerk mit
> 2-kWh-Speicher ergänzt (PV-Leistung, Lade-/Entladeleistung, SoC,
> Speicherstand, Hausabgabe, Ertrags-/Lade-/Entlade-Zähler mit
> Tages-Reset-Variante, Online-Status). Mindert wie ein reales
> Balkonkraftwerk den simulierten Netzbezug, unabhängig von der
> bestehenden Dachanlage. Außerdem neue eigenständige Aktion
> `--clear {values,entities}` zum Löschen aller Demo-Entitäten (Werte oder
> komplett inkl. Konfiguration), ohne anschließend neu zu erzeugen. Dazu
> zwei weitere Entitäten: `co2_intensitaet` (Netz-CO2-Intensität) und eine
> PV-Ertragsprognose für die Dachanlage (`pv_prognose_rest_heute`/`_morgen`,
> nach Vorbild von Forecast.Solar). Außerdem ein zweiter, größerer
> Heimspeicher (10 kWh) direkt an der Dachanlage sowie eine
> Kapazitäts-Kennzahl für beide Speichersysteme (Heimspeicher in kWh,
> Balkonkraftwerk in Wh).
