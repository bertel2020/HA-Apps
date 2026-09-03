#!/usr/bin/env python3
"""Erzeugt realistische Demo-Daten für eine leere/neue Zeitarchiv-Instanz.

Legt eine Handvoll typischer Haushalts-Entitäten (Innen-/Außentemperatur,
Luftfeuchte, Wind, Regensensor, Gesamtwirkleistung, einzelne Verbraucher
inkl. Wallbox, PV, Balkonkraftwerk mit Speicher, Strom-/Wasserzähler,
Präsenz) direkt im Zeitarchiv-
Speicherformat an — über denselben Import-Kern, den
auch der CSV-/Symcon-Import in der App selbst verwendet
(app/storage/symcon_import.py), nicht über eigene Datei-Formate. Kein
laufender Server nötig; einfach danach ZEITARCHIV_DATA_DIR auf das
Zielverzeichnis zeigen lassen (siehe docs/development.md) bzw. eine bereits
laufende Instanz neu starten, damit sie die neuen Dateien einliest.

Nicht gegen ein Datenverzeichnis laufen lassen, dessen Server GLEICHZEITIG
läuft — SQLite-Zugriffe aus zwei Prozessen parallel sind nicht vorgesehen.

Beispiele:
    python3 scripts/generate_demo_data.py --data-dir /tmp/zeitarchiv-demo
    python3 scripts/generate_demo_data.py --data-dir /tmp/zeitarchiv-demo --months 12 --clean
    python3 scripts/generate_demo_data.py --data-dir /tmp/zeitarchiv-demo --append

--append ergänzt eine bereits vorhandene Demo-Instanz um die Werte seit dem
letzten Lauf statt die komplette Historie neu zu würfeln — z. B. per Cron
regelmäßig ausgeführt, bleibt eine Demo-Instanz so ein "lebendes" System, das
nie hinter das aktuelle Datum zurückfällt. Zähler- und Schalter-Entitäten
knüpfen dabei an ihren zuletzt gespeicherten Wert an (kein Zählersprung/
-rücksetzer beim Fortsetzen); überlappende Zeitstempel werden von
import_rows() ohnehin dedupliziert, ein Sicherheitsabstand ist also
unkritisch. Ohne vorhandene Demo-Daten fällt --append automatisch auf eine
normale Vollerzeugung (--months) zurück.
"""

from __future__ import annotations

import argparse
import math
import random
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pyarrow.parquet as pq  # noqa: E402

from app.storage import hotbuffer, reconcile  # noqa: E402
from app.storage.entity_removal import delete_all_values, delete_entity  # noqa: E402
from app.storage.index import Index  # noqa: E402
from app.storage.paths import entity_dir, storage_area_dir  # noqa: E402
from app.storage.symcon_import import import_rows  # noqa: E402

CONTINUOUS_STEP_MINUTES = 5
COUNTER_STEP_MINUTES = 30
PRESENCE_STEP_MINUTES = 15
# Wie viele Kontinuierlich-Schritte zwischen zwei Zählerstands-Zeilen liegen
# — Zähler werden aus derselben Simulation abgeleitet statt neu gewürfelt,
# nur seltener aufgezeichnet als die Leistungswerte selbst.
COUNTER_EVERY_N = COUNTER_STEP_MINUTES // CONTINUOUS_STEP_MINUTES

Row = tuple[float, float]


@dataclass
class DemoEntity:
    entity_id: str
    friendly_name: str
    domain: str
    state_class: str | None
    unit: str | None


DEMO_ENTITIES = [
    DemoEntity("sensor.demo_wohnzimmer_temperatur", "Demo Wohnzimmer Temperatur",
               "sensor", "measurement", "°C"),
    DemoEntity("sensor.demo_aussentemperatur", "Demo Außentemperatur",
               "sensor", "measurement", "°C"),
    DemoEntity("sensor.demo_luftfeuchte", "Demo Luftfeuchte Außen",
               "sensor", "measurement", "%"),
    DemoEntity("sensor.demo_wohnzimmer_luftfeuchte", "Demo Wohnzimmer Luftfeuchte",
               "sensor", "measurement", "%"),
    DemoEntity("sensor.demo_wind", "Demo Wind",
               "sensor", "measurement", "km/h"),
    DemoEntity("sensor.demo_gesamtwirkleistung", "Demo Gesamtwirkleistung",
               "sensor", "measurement", "W"),
    DemoEntity("sensor.demo_heizung", "Demo Heizung",
               "sensor", "measurement", "W"),
    DemoEntity("sensor.demo_waschmaschine", "Demo Waschmaschine",
               "sensor", "measurement", "W"),
    DemoEntity("sensor.demo_waschmaschine_energie", "Demo Waschmaschine Energie",
               "sensor", "total_increasing", "kWh"),
    DemoEntity("binary_sensor.demo_waschmaschine_an", "Demo Waschmaschine An",
               "binary_sensor", None, None),
    DemoEntity("sensor.demo_spuelmaschine", "Demo Spülmaschine",
               "sensor", "measurement", "W"),
    DemoEntity("sensor.demo_spuelmaschine_energie", "Demo Spülmaschine Energie",
               "sensor", "total_increasing", "kWh"),
    DemoEntity("binary_sensor.demo_spuelmaschine_an", "Demo Spülmaschine An",
               "binary_sensor", None, None),
    DemoEntity("sensor.demo_trockner", "Demo Trockner",
               "sensor", "measurement", "W"),
    DemoEntity("sensor.demo_trockner_energie", "Demo Trockner Energie",
               "sensor", "total_increasing", "kWh"),
    DemoEntity("binary_sensor.demo_trockner_an", "Demo Trockner An",
               "binary_sensor", None, None),
    DemoEntity("sensor.demo_wallbox_leistung", "Demo Wallbox Leistung",
               "sensor", "measurement", "W"),
    DemoEntity("sensor.demo_wallbox_energie", "Demo Wallbox Energie",
               "sensor", "total_increasing", "kWh"),
    DemoEntity("sensor.demo_pv_leistung", "Demo PV-Leistung",
               "sensor", "measurement", "W"),
    DemoEntity("sensor.demo_pv_ertrag", "Demo PV-Ertrag",
               "sensor", "total_increasing", "kWh"),
    DemoEntity("sensor.demo_pv_prognose_rest_heute", "Demo PV-Prognose Rest Heute",
               "sensor", "measurement", "kWh"),
    DemoEntity("sensor.demo_pv_prognose_morgen", "Demo PV-Prognose Morgen",
               "sensor", "measurement", "kWh"),
    DemoEntity("sensor.demo_co2_intensitaet", "Demo CO2-Intensität Netz",
               "sensor", "measurement", "g/kWh"),
    DemoEntity("sensor.demo_stromzaehler_bezug", "Demo Stromzähler Bezug",
               "sensor", "total_increasing", "kWh"),
    DemoEntity("sensor.demo_stromzaehler_einspeisung", "Demo Stromzähler Einspeisung",
               "sensor", "total_increasing", "kWh"),
    DemoEntity("sensor.demo_balkonkraftwerk_pv_leistung", "Demo Balkonkraftwerk PV-Leistung",
               "sensor", "measurement", "W"),
    DemoEntity("sensor.demo_balkonkraftwerk_ladeleistung", "Demo Balkonkraftwerk Ladeleistung",
               "sensor", "measurement", "W"),
    DemoEntity("sensor.demo_balkonkraftwerk_entladeleistung", "Demo Balkonkraftwerk Entladeleistung",
               "sensor", "measurement", "W"),
    DemoEntity("sensor.demo_balkonkraftwerk_speicher_soc", "Demo Balkonkraftwerk Speicher SoC",
               "sensor", "measurement", "%"),
    DemoEntity("sensor.demo_balkonkraftwerk_speicher_stand", "Demo Balkonkraftwerk Speicherstand",
               "sensor", "measurement", "kWh"),
    DemoEntity("sensor.demo_balkonkraftwerk_speicher_kapazitaet", "Demo Balkonkraftwerk Speicherkapazität",
               "sensor", "measurement", "Wh"),
    DemoEntity("sensor.demo_balkonkraftwerk_hausabgabe", "Demo Balkonkraftwerk Hausabgabe",
               "sensor", "measurement", "W"),
    DemoEntity("sensor.demo_balkonkraftwerk_ertrag_heute", "Demo Balkonkraftwerk Ertrag Heute",
               "sensor", "total_increasing", "kWh"),
    DemoEntity("sensor.demo_balkonkraftwerk_ertrag_gesamt", "Demo Balkonkraftwerk Ertrag Gesamt",
               "sensor", "total_increasing", "kWh"),
    DemoEntity("sensor.demo_balkonkraftwerk_geladen_heute", "Demo Balkonkraftwerk Geladen Heute",
               "sensor", "total_increasing", "kWh"),
    DemoEntity("sensor.demo_balkonkraftwerk_geladen_gesamt", "Demo Balkonkraftwerk Geladen Gesamt",
               "sensor", "total_increasing", "kWh"),
    DemoEntity("sensor.demo_balkonkraftwerk_entladen_heute", "Demo Balkonkraftwerk Entladen Heute",
               "sensor", "total_increasing", "kWh"),
    DemoEntity("sensor.demo_balkonkraftwerk_entladen_gesamt", "Demo Balkonkraftwerk Entladen Gesamt",
               "sensor", "total_increasing", "kWh"),
    DemoEntity("binary_sensor.demo_balkonkraftwerk_online", "Demo Balkonkraftwerk Online",
               "binary_sensor", None, None),
    DemoEntity("sensor.demo_heimspeicher_kapazitaet", "Demo Heimspeicher Kapazität",
               "sensor", "measurement", "kWh"),
    DemoEntity("sensor.demo_heimspeicher_ladeleistung", "Demo Heimspeicher Ladeleistung",
               "sensor", "measurement", "W"),
    DemoEntity("sensor.demo_heimspeicher_entladeleistung", "Demo Heimspeicher Entladeleistung",
               "sensor", "measurement", "W"),
    DemoEntity("sensor.demo_heimspeicher_speicher_soc", "Demo Heimspeicher Speicher SoC",
               "sensor", "measurement", "%"),
    DemoEntity("sensor.demo_heimspeicher_speicher_stand", "Demo Heimspeicher Speicherstand",
               "sensor", "measurement", "kWh"),
    DemoEntity("sensor.demo_heimspeicher_geladen_heute", "Demo Heimspeicher Geladen Heute",
               "sensor", "total_increasing", "kWh"),
    DemoEntity("sensor.demo_heimspeicher_geladen_gesamt", "Demo Heimspeicher Geladen Gesamt",
               "sensor", "total_increasing", "kWh"),
    DemoEntity("sensor.demo_heimspeicher_entladen_heute", "Demo Heimspeicher Entladen Heute",
               "sensor", "total_increasing", "kWh"),
    DemoEntity("sensor.demo_heimspeicher_entladen_gesamt", "Demo Heimspeicher Entladen Gesamt",
               "sensor", "total_increasing", "kWh"),
    DemoEntity("binary_sensor.demo_heimspeicher_online", "Demo Heimspeicher Online",
               "binary_sensor", None, None),
    DemoEntity("sensor.demo_wasserzaehler", "Demo Wasserzähler",
               "sensor", "total_increasing", "m³"),
    DemoEntity("binary_sensor.demo_praesenz_wohnzimmer", "Demo Präsenz Wohnzimmer",
               "binary_sensor", None, None),
    DemoEntity("binary_sensor.demo_regensensor", "Demo Regensensor",
               "binary_sensor", None, None),
]


# --- Wetter-Kontext ---------------------------------------------------------
#
# Ein Tageswert für Bewölkung/Temperaturabweichung wird EINMAL pro Kalendertag
# berechnet und über einen einfachen AR(1)-Prozess an den Vortag gekoppelt
# (heute hängt zu 70% vom Vortag ab) — sonst sähen PV-Leistung, Außentemperatur
# und Luftfeuchte an aufeinanderfolgenden Tagen unabhängig-zufällig statt wie
# zusammenhängendes Wetter aus.
class WeatherContext:
    def __init__(self, start: datetime, end: datetime, rng: random.Random) -> None:
        self._cloud: dict[str, float] = {}
        self._temp_offset: dict[str, float] = {}
        self._wind_base: dict[str, float] = {}
        cloud = 0.7
        offset = 0.0
        wind = 10.0
        day = start.date()
        while day <= end.date():
            key = day.isoformat()
            cloud = max(0.15, min(1.0, 0.7 * cloud + 0.3 * rng.uniform(0.15, 1.0)))
            offset = max(-6.0, min(6.0, 0.75 * offset + rng.uniform(-2.5, 2.5)))
            # Windigere Tage korrelieren lose mit mehr Bewölkung (stürmisches
            # statt strahlend klares Wetter), plus eigenes AR(1)-Rauschen.
            wind = max(2.0, min(45.0, 0.6 * wind + 0.4 * (rng.uniform(4, 22) + (1.0 - cloud) * 10)))
            self._cloud[key] = cloud
            self._temp_offset[key] = offset
            self._wind_base[key] = wind
            day += timedelta(days=1)

    def cloud_factor(self, dt: datetime) -> float:
        return self._cloud[dt.date().isoformat()]

    def temp_offset(self, dt: datetime) -> float:
        return self._temp_offset[dt.date().isoformat()]

    def wind_base(self, dt: datetime) -> float:
        return self._wind_base[dt.date().isoformat()]


def _seasonal_outdoor_base(day_of_year: int) -> float:
    # Grobes Mitteleuropa-Profil: ~ -1°C im Winter, ~19°C im Sommer. Peak
    # bewusst auf Tag 200 (~19. Juli) statt der astronomischen Sonnwende
    # (Tag 172) — realer Temperaturhöhepunkt hängt der Tageslichtlänge
    # üblicherweise einige Wochen hinterher.
    frac = 2 * math.pi * (day_of_year - 200) / 365
    return 9.0 + 10.0 * math.cos(frac)


def _outdoor_temp_at(dt: datetime, weather: WeatherContext) -> float:
    """Ohne Anzeige-Rauschen — von der Heizungssteuerung UND dem angezeigten
    Außentemperatur-Sensor genutzt, damit beide auf demselben Wert basieren."""
    hour = dt.hour + dt.minute / 60
    base = _seasonal_outdoor_base(dt.timetuple().tm_yday)
    daily_cycle = 4.5 * math.sin(2 * math.pi * (hour - 15) / 24)
    return base + daily_cycle + weather.temp_offset(dt)


def _daylight_hours(day_of_year: int) -> float:
    frac = 2 * math.pi * (day_of_year - 172) / 365
    return 12.0 + 4.0 * math.cos(frac)


def _time_range(start: datetime, end: datetime, step_minutes: int):
    # Absichtlich in UTC-Sekunden statt per timedelta-Addition auf dem
    # tz-aware datetime selbst gezählt: Letzteres läuft an der DST-Umstellung
    # (in Europe/Berlin z. B. Ende März) auf eine lokal nicht existierende
    # Stunde und erzeugt dabei doppelte/rückwärts laufende Zeitstempel — bei
    # Zählern sichtbar als "Zählerrückgang" und Duplikat zugleich. Schritte
    # in echten verstrichenen Sekunden sind dagegen immer streng monoton;
    # datetime.fromtimestamp() liefert trotzdem die korrekte lokale Uhrzeit
    # für die Tagesmuster (dt.hour usw.).
    step_seconds = step_minutes * 60
    ts = start.timestamp()
    end_ts = end.timestamp()
    tz = start.tzinfo
    while ts <= end_ts:
        yield datetime.fromtimestamp(ts, tz)
        ts += step_seconds


# --- Verbraucher-Zeitpläne ---------------------------------------------------
#
# Waschmaschine/Spülmaschine/Trockner laufen nicht dauerhaft, sondern in
# einzelnen Zyklen — Start-/Endzeiten werden EINMAL für den ganzen Zeitraum
# gewürfelt (statt bei jedem Zeitschritt neu zu entscheiden), pro Kalendertag
# in einem Dict abgelegt, damit die Zuordnung "läuft dieses Gerät gerade"
# beim eigentlichen Erzeugen der Werte nur die paar Zyklen DIESES Tages
# durchsuchen muss statt aller Zyklen im gesamten Zeitraum.
@dataclass
class ApplianceCycle:
    start: datetime
    end: datetime


def build_appliance_schedules(
    start: datetime, end: datetime, rng: random.Random
) -> dict[str, dict[str, list[ApplianceCycle]]]:
    schedules: dict[str, dict[str, list[ApplianceCycle]]] = {
        "waschmaschine": {}, "spuelmaschine": {}, "trockner": {}, "wallbox": {},
    }
    day = start.date()
    while day <= end.date():
        key = day.isoformat()
        base = datetime(day.year, day.month, day.day, tzinfo=start.tzinfo)

        washer_cycle = None
        if rng.random() < 0.35:
            begin = base + timedelta(hours=rng.uniform(8, 20))
            washer_cycle = ApplianceCycle(begin, begin + timedelta(minutes=rng.uniform(95, 120)))
            schedules["waschmaschine"].setdefault(key, []).append(washer_cycle)

        if rng.random() < 0.55:
            begin = base + timedelta(hours=rng.uniform(18, 22.5))
            schedules["spuelmaschine"].setdefault(key, []).append(
                ApplianceCycle(begin, begin + timedelta(minutes=rng.uniform(115, 140)))
            )

        if washer_cycle is not None and rng.random() < 0.65:
            begin = washer_cycle.end + timedelta(minutes=rng.uniform(15, 90))
            schedules["trockner"].setdefault(key, []).append(
                ApplianceCycle(begin, begin + timedelta(minutes=rng.uniform(75, 95)))
            )

        if rng.random() < 0.3:
            begin = base + timedelta(hours=rng.uniform(17, 23))
            schedules["wallbox"].setdefault(key, []).append(
                ApplianceCycle(begin, begin + timedelta(minutes=rng.uniform(120, 300)))
            )

        day += timedelta(days=1)
    return schedules


def build_rain_schedule(
    start: datetime, end: datetime, weather: WeatherContext, rng: random.Random
) -> dict[str, list[ApplianceCycle]]:
    """Regenfenster pro Kalendertag — Wahrscheinlichkeit steigt mit sinkendem
    cloud_factor (mehr Bewölkung an diesem Tag, siehe WeatherContext)."""
    schedule: dict[str, list[ApplianceCycle]] = {}
    day = start.date()
    while day <= end.date():
        key = day.isoformat()
        base = datetime(day.year, day.month, day.day, tzinfo=start.tzinfo)
        rain_chance = max(0.0, min(0.55, (1.0 - weather.cloud_factor(base)) * 0.65))
        if rng.random() < rain_chance:
            windows = []
            for _ in range(1 if rng.random() < 0.7 else 2):
                begin = base + timedelta(hours=rng.uniform(0, 22))
                windows.append(ApplianceCycle(begin, begin + timedelta(minutes=rng.uniform(20, 180))))
            schedule[key] = windows
        day += timedelta(days=1)
    return schedule


def _cycle_power(cycle: ApplianceCycle, dt: datetime, profile: list[tuple[float, float]]) -> float:
    """profile: [(Anteil des Zyklus bis wohin, Leistung in W), ...], aufsteigend sortiert."""
    duration = (cycle.end - cycle.start).total_seconds()
    if duration <= 0:
        return 0.0
    position = (dt - cycle.start).total_seconds() / duration
    if not (0.0 <= position <= 1.0):
        return 0.0
    for threshold, watts in profile:
        if position <= threshold:
            return watts
    return 0.0


WASCHMASCHINE_PROFILE = [(0.18, 350.0), (0.32, 2000.0), (0.82, 300.0), (0.92, 550.0), (1.0, 700.0)]
SPUELMASCHINE_PROFILE = [(0.12, 100.0), (0.28, 1900.0), (0.85, 150.0), (1.0, 90.0)]
TROCKNER_PROFILE = [(0.78, 2400.0), (1.0, 300.0)]
WALLBOX_PROFILE = [(0.06, 4000.0), (0.85, 7400.0), (1.0, 2500.0)]  # Anlaufen, Laden, Taper


def _rain_active(schedule_for_day: list[ApplianceCycle], dt: datetime) -> bool:
    return any(cycle.start <= dt <= cycle.end for cycle in schedule_for_day)


def _appliance_power_at(
    schedules: dict[str, list[ApplianceCycle]], profile: list[tuple[float, float]], dt: datetime, rng: random.Random
) -> float:
    for cycle in schedules.get(dt.date().isoformat(), ()):
        power = _cycle_power(cycle, dt, profile)
        if power > 0:
            return power + rng.uniform(-20, 20)
    return 0.0


def _heating_power_at(dt: datetime, weather: WeatherContext, rng: random.Random) -> float:
    outdoor = _outdoor_temp_at(dt, weather)
    # 18 °C als Referenz, ab der nicht mehr geheizt wird; je kälter, desto
    # größer der Anteil eines 30-Minuten-Fensters, in dem die Heizung läuft.
    duty = max(0.0, min(0.85, (18.0 - outdoor) / 22.0))
    if duty <= 0:
        return 0.0
    window_steps = COUNTER_STEP_MINUTES // CONTINUOUS_STEP_MINUTES
    step_index = (dt.hour * 60 + dt.minute) // CONTINUOUS_STEP_MINUTES % window_steps
    if step_index < round(duty * window_steps):
        return 1800.0 + rng.uniform(-150, 250)
    return 0.0


def _baseline_load_at(dt: datetime, rng: random.Random) -> float:
    hour = dt.hour + dt.minute / 60
    morning = 180 * math.exp(-((hour - 7.3) ** 2) / (2 * 1.1 ** 2))
    evening = 350 * math.exp(-((hour - 19.5) ** 2) / (2 * 1.8 ** 2))
    value = 130 + morning + evening + rng.uniform(-20, 35)
    if rng.random() < 0.015:  # Wasserkocher, Toaster & Co.
        value += rng.uniform(600, 1600)
    return max(45.0, value)


def gen_indoor_temp(dt: datetime, rng: random.Random) -> float:
    hour = dt.hour + dt.minute / 60
    return round(21.0 + 1.1 * math.sin(2 * math.pi * (hour - 15) / 24) + rng.uniform(-0.25, 0.25), 2)


def gen_humidity(dt: datetime, weather: WeatherContext, rng: random.Random) -> float:
    hour = dt.hour + dt.minute / 60
    outdoor = _outdoor_temp_at(dt, weather)
    value = 68.0 - 0.9 * (outdoor - 9.0) + 7.0 * math.cos(2 * math.pi * (hour - 5) / 24)
    value += (1.0 - weather.cloud_factor(dt)) * 8.0 + rng.uniform(-2.5, 2.5)
    return round(max(28.0, min(97.0, value)), 1)


def gen_indoor_humidity(dt: datetime, outdoor_humidity: float, rng: random.Random) -> float:
    # Innenraumluft ist gedämpfter als draußen (Heizung/Lüftungsverhalten),
    # bleibt aber lose an die Außenluftfeuchte gekoppelt.
    value = 44.0 + 0.18 * (outdoor_humidity - 60.0) + rng.uniform(-2.0, 2.0)
    return round(max(28.0, min(68.0, value)), 1)


def gen_wind(dt: datetime, weather: WeatherContext, rng: random.Random) -> float:
    hour = dt.hour + dt.minute / 60
    daily_cycle = 1.0 + 0.4 * math.sin(2 * math.pi * (hour - 14) / 24)  # nachmittags etwas böiger
    value = weather.wind_base(dt) * daily_cycle + rng.uniform(-2.5, 2.5)
    if rng.random() < 0.03:  # kurze Böen
        value += rng.uniform(5, 18)
    return round(max(0.0, value), 1)


PV_PEAK_W = 5200.0
# ∫₀¹ sin(πx) dx — die analytische Fläche unter der Glockenkurvenform aus
# gen_pv_power(), zur Tagesertrags-SCHÄTZUNG in pv_forecast_kwh() genutzt,
# ohne die Kurve minutenweise nachsimulieren zu müssen.
PV_SHAPE_INTEGRAL = 2 / math.pi


def gen_pv_power(dt: datetime, weather: WeatherContext, rng: random.Random) -> float:
    day_of_year = dt.timetuple().tm_yday
    daylight = _daylight_hours(day_of_year)
    sunrise, sunset = 12.0 - daylight / 2, 12.0 + daylight / 2
    hour = dt.hour + dt.minute / 60
    if hour <= sunrise or hour >= sunset:
        return 0.0
    position = (hour - sunrise) / (sunset - sunrise)
    shape = math.sin(math.pi * position)
    value = PV_PEAK_W * shape * weather.cloud_factor(dt) + rng.uniform(-40, 40)
    return round(max(0.0, value), 1)


def pv_forecast_kwh(day_of_year: int, cloud_estimate: float) -> float:
    """Grobe Tagesertrags-Schätzung der Dachanlage aus einer (ggf.
    fehlerbehafteten) Bewölkungs-Schätzung — dieselbe Glockenkurvenform wie
    gen_pv_power(), aber über den ganzen Tag integriert statt an einem
    einzelnen Zeitpunkt ausgewertet, für die Prognose-Sensoren."""
    daylight = _daylight_hours(day_of_year)
    return round(max(0.0, PV_PEAK_W * PV_SHAPE_INTEGRAL * cloud_estimate * daylight / 1000), 3)


def gen_co2_intensity(dt: datetime, weather: WeatherContext, rng: random.Random) -> float:
    """Angelehnt an reale CO2-Signal-/ElectricityMap-Sensoren: Grundlast-
    Niveau nachts, Einbruch mittags durch PV-Einspeisung ins Netz, leichter
    Anstieg zur Abendspitze; an sonnigen/windigen Tagen (mehr Erneuerbare im
    Netzmix, dieselbe WeatherContext wie bei den PV-/Wind-Sensoren) zusätzlich
    niedriger als an trüben, windstillen Tagen."""
    hour = dt.hour + dt.minute / 60
    base = 340.0
    midday_dip = -70.0 * math.exp(-((hour - 13) ** 2) / (2 * 3.0 ** 2))
    evening_peak = 45.0 * math.exp(-((hour - 19) ** 2) / (2 * 2.0 ** 2))
    renewables_effect = -55.0 * (weather.cloud_factor(dt) - 0.5) - 1.3 * (weather.wind_base(dt) - 10.0)
    value = base + midday_dip + evening_peak + renewables_effect + rng.uniform(-12, 12)
    return round(max(80.0, min(650.0, value)), 1)


# --- Balkonkraftwerk mit Speicher --------------------------------------------
#
# Eigenständiges Zweitsystem neben der großen Dachanlage (sensor.demo_pv_*):
# kleinere Modulleistung mit fester Wechselrichter-Kappung (reale Steckersolar-
# Geräte drosseln auf einen festen Ausgangswert, unabhängig davon wie viel die
# Module bei voller Sonne tatsächlich liefern könnten) und ein kleiner Speicher
# davor, der tagsüber priorisiert geladen wird und nachts mit konstanter
# Leistung entlädt statt den Übertag erzeugten Strom ungenutzt einzuspeisen.
BALKON_CAPACITY_KWH = 2.0
BALKON_MODULE_PEAK_W = 950.0  # vor Wechselrichter-Kappung, daher > Nennleistung
BALKON_INVERTER_CAP_W = 800.0
BALKON_DISCHARGE_TARGET_W = 220.0
BALKON_MIN_SOC = 0.05


def gen_balkon_pv_power(dt: datetime, weather: WeatherContext, rng: random.Random) -> float:
    """Wie gen_pv_power(), aber mit eigenem (etwas morgenärmerem, auf Südwest-
    Ausrichtung hindeutendem) Tagesfenster und fester Wechselrichter-Kappung
    bei BALKON_INVERTER_CAP_W statt der Modul-Rohleistung."""
    day_of_year = dt.timetuple().tm_yday
    daylight = _daylight_hours(day_of_year)
    sunrise, sunset = 12.0 - daylight / 2 + 0.6, 12.0 + daylight / 2
    hour = dt.hour + dt.minute / 60
    if hour <= sunrise or hour >= sunset:
        return 0.0
    position = (hour - sunrise) / (sunset - sunrise)
    shape = math.sin(math.pi * position) ** 1.2
    raw = BALKON_MODULE_PEAK_W * shape * weather.cloud_factor(dt) + rng.uniform(-15, 15)
    return round(max(0.0, min(BALKON_INVERTER_CAP_W, raw)), 1)


# --- Heimspeicher --------------------------------------------------------
#
# Zweiter, deutlich größerer Speicher — anders als das unabhängige
# Balkonkraftwerk direkt an die Dachanlage gekoppelt (typisch für ein
# Hybrid-Wechselrichter-Setup): lädt aus dem PV-Überschuss, der sonst
# eingespeist würde, und deckt bei PV-Defizit einen Teil des Netzbezugs,
# statt eigene PV-Leistung zu erzeugen.
HEIM_CAPACITY_KWH = 10.0
HEIM_MAX_CHARGE_W = 3000.0
HEIM_MAX_DISCHARGE_W = 3000.0
HEIM_MIN_SOC = 0.05


def simulate_household(
    start: datetime, end: datetime, tz: ZoneInfo, rng: random.Random,
    weather: WeatherContext, schedules: dict[str, dict[str, list[ApplianceCycle]]],
    rain_schedule: dict[str, list[ApplianceCycle]],
    counter_seed: dict[str, float] | None = None,
    appliance_seed: dict[str, float] | None = None,
    rain_seed: float = 0.0,
    balkon_seed: dict[str, float] | None = None,
    balkon_heute_seed: tuple[str | None, dict[str, float]] | None = None,
    balkon_online_seed: float = 0.0,
    heim_seed: dict[str, float] | None = None,
    heim_heute_seed: tuple[str | None, dict[str, float]] | None = None,
    heim_online_seed: float = 0.0,
) -> dict[str, list[Row]]:
    """Ein einziger Durchlauf durch den gesamten Zeitraum, der alle
    Leistungs-/Zähler-Entitäten konsistent zueinander erzeugt: die
    Zählerstände integrieren exakt dieselben Werte, die auch als
    Leistungs-Sensoren geschrieben werden, statt unabhängig neu gewürfelt zu
    werden.

    counter_seed/appliance_seed/rain_seed knüpfen beim Fortsetzen einer
    bestehenden Demo-Instanz (--append) an die zuletzt gespeicherten Werte
    an, statt bei jedem Lauf neue Zufalls-Startwerte zu würfeln — sonst
    sähe man beim Fortsetzen einen Zählersprung/-rücksetzer an der
    Anschlussstelle. Fehlt ein Schlüssel (Erstlauf ohne vorhandene Demo-
    Daten), gilt weiterhin der bisherige Zufalls-Startwert."""
    series: dict[str, list[Row]] = {
        "indoor_temp": [], "outdoor_temp": [], "humidity": [], "indoor_humidity": [], "wind": [],
        "load_power": [], "heizung": [], "waschmaschine": [], "spuelmaschine": [], "trockner": [], "wallbox": [],
        "waschmaschine_energie": [], "spuelmaschine_energie": [], "trockner_energie": [], "wallbox_energie": [],
        "waschmaschine_an": [], "spuelmaschine_an": [], "trockner_an": [], "regensensor": [],
        "pv_power": [], "pv_ertrag": [], "stromzaehler_bezug": [], "stromzaehler_einspeisung": [],
        "pv_prognose_rest_heute": [], "pv_prognose_morgen": [], "co2_intensitaet": [],
        "balkon_pv": [], "balkon_ladeleistung": [], "balkon_entladeleistung": [],
        "balkon_soc_pct": [], "balkon_speicher_stand": [], "balkon_hausabgabe": [],
        "balkon_ertrag_heute": [], "balkon_ertrag_gesamt": [],
        "balkon_geladen_heute": [], "balkon_geladen_gesamt": [],
        "balkon_entladen_heute": [], "balkon_entladen_gesamt": [], "balkon_online": [],
        "heim_ladeleistung": [], "heim_entladeleistung": [], "heim_soc_pct": [], "heim_speicher_stand": [],
        "heim_geladen_heute": [], "heim_geladen_gesamt": [],
        "heim_entladen_heute": [], "heim_entladen_gesamt": [], "heim_online": [],
    }
    counter_seed = counter_seed or {}
    appliance_seed = appliance_seed or {}
    balkon_seed = balkon_seed or {}
    balkon_heute_seed_day, balkon_heute_seed_values = balkon_heute_seed or (None, {})
    heim_seed = heim_seed or {}
    heim_heute_seed_day, heim_heute_seed_values = heim_heute_seed or (None, {})
    step_hours = CONTINUOUS_STEP_MINUTES / 60
    pv_ertrag_total = counter_seed.get("pv_ertrag", rng.uniform(3000, 15000))
    bezug_total = counter_seed.get("bezug", rng.uniform(6000, 20000))
    einspeisung_total = counter_seed.get("einspeisung", rng.uniform(500, 6000))
    waschmaschine_energie_total = counter_seed.get("waschmaschine_energie", rng.uniform(50, 400))
    spuelmaschine_energie_total = counter_seed.get("spuelmaschine_energie", rng.uniform(50, 400))
    trockner_energie_total = counter_seed.get("trockner_energie", rng.uniform(50, 400))
    wallbox_energie_total = counter_seed.get("wallbox_energie", rng.uniform(200, 2500))
    on_state = {
        "waschmaschine": appliance_seed.get("waschmaschine", 0.0),
        "spuelmaschine": appliance_seed.get("spuelmaschine", 0.0),
        "trockner": appliance_seed.get("trockner", 0.0),
    }
    rain_state = rain_seed

    balkon_soc_kwh = balkon_seed.get("soc_kwh", rng.uniform(0.3, 1.6))
    balkon_ertrag_gesamt_total = balkon_seed.get("ertrag_gesamt", rng.uniform(50, 800))
    balkon_geladen_gesamt_total = balkon_seed.get("geladen_gesamt", rng.uniform(60, 900))
    balkon_entladen_gesamt_total = balkon_seed.get("entladen_gesamt", rng.uniform(50, 850))
    # Tages-Reset-Zähler knüpfen nur an, wenn der letzte Lauf am selben
    # Kalendertag endete — sonst startet der neue Tag ohnehin bei 0.
    balkon_day = start.date().isoformat()
    if balkon_heute_seed_day == balkon_day:
        balkon_ertrag_heute_total = balkon_heute_seed_values.get("ertrag_heute", 0.0)
        balkon_geladen_heute_total = balkon_heute_seed_values.get("geladen_heute", 0.0)
        balkon_entladen_heute_total = balkon_heute_seed_values.get("entladen_heute", 0.0)
    else:
        balkon_ertrag_heute_total = 0.0
        balkon_geladen_heute_total = 0.0
        balkon_entladen_heute_total = 0.0
    balkon_online_state = balkon_online_seed

    heim_soc_kwh = heim_seed.get("soc_kwh", rng.uniform(2.0, 8.0))
    heim_geladen_gesamt_total = heim_seed.get("geladen_gesamt", rng.uniform(200, 3000))
    heim_entladen_gesamt_total = heim_seed.get("entladen_gesamt", rng.uniform(150, 2800))
    heim_day = start.date().isoformat()
    if heim_heute_seed_day == heim_day:
        heim_geladen_heute_total = heim_heute_seed_values.get("geladen_heute", 0.0)
        heim_entladen_heute_total = heim_heute_seed_values.get("entladen_heute", 0.0)
    else:
        heim_geladen_heute_total = 0.0
        heim_entladen_heute_total = 0.0
    heim_online_state = heim_online_seed

    # PV-Prognose: pro Kalendertag einmal geschätzt statt bei jedem Schritt
    # neu — reale Forecast-Sensoren aktualisieren sich auch nur wenige Male
    # am Tag. pv_forecast_next_total (am Vortag für "morgen" geschätzt) wird
    # beim Tageswechsel zu pv_forecast_today_total — die Prognose von gestern
    # für heute ist ja bereits die Prognose, die heute gilt, ohne dass "heute
    # Morgen" nochmal neu geschätzt werden müsste. Bewusst nicht über
    # --append hinweg fortgeführt (siehe docs/demo-data.md, Grenzen): anders
    # als bei Zählern ist ein "Reset" auf einen frischen Tageswert für einen
    # reinen measurement-Sensor unkritisch.
    pv_forecast_today_total = None
    pv_forecast_next_total = None
    pv_forecast_day = None
    pv_actual_today_kwh = 0.0

    for index, dt in enumerate(_time_range(start, end, CONTINUOUS_STEP_MINUTES)):
        ts = dt.timestamp()
        series["indoor_temp"].append((ts, gen_indoor_temp(dt, rng)))
        outdoor_display = round(_outdoor_temp_at(dt, weather) + rng.uniform(-0.4, 0.4), 2)
        series["outdoor_temp"].append((ts, outdoor_display))
        outdoor_humidity = gen_humidity(dt, weather, rng)
        series["humidity"].append((ts, outdoor_humidity))
        series["indoor_humidity"].append((ts, gen_indoor_humidity(dt, outdoor_humidity, rng)))
        series["wind"].append((ts, gen_wind(dt, weather, rng)))

        heizung_w = _heating_power_at(dt, weather, rng)
        waschmaschine_w = _appliance_power_at(schedules["waschmaschine"], WASCHMASCHINE_PROFILE, dt, rng)
        spuelmaschine_w = _appliance_power_at(schedules["spuelmaschine"], SPUELMASCHINE_PROFILE, dt, rng)
        trockner_w = _appliance_power_at(schedules["trockner"], TROCKNER_PROFILE, dt, rng)
        wallbox_w = _appliance_power_at(schedules["wallbox"], WALLBOX_PROFILE, dt, rng)
        baseline_w = _baseline_load_at(dt, rng)
        load_w = baseline_w + heizung_w + waschmaschine_w + spuelmaschine_w + trockner_w + wallbox_w
        pv_w = gen_pv_power(dt, weather, rng)

        series["heizung"].append((ts, round(heizung_w, 1)))
        series["waschmaschine"].append((ts, round(waschmaschine_w, 1)))
        series["spuelmaschine"].append((ts, round(spuelmaschine_w, 1)))
        series["trockner"].append((ts, round(trockner_w, 1)))
        series["wallbox"].append((ts, round(wallbox_w, 1)))
        series["load_power"].append((ts, round(load_w, 1)))
        series["pv_power"].append((ts, pv_w))

        series["co2_intensitaet"].append((ts, gen_co2_intensity(dt, weather, rng)))

        day_key = dt.date().isoformat()
        if day_key != pv_forecast_day:
            pv_forecast_day = day_key
            pv_actual_today_kwh = 0.0
            today_cloud = weather.cloud_factor(dt)
            if pv_forecast_next_total is not None:
                pv_forecast_today_total = pv_forecast_next_total
            else:
                today_estimate = max(0.1, min(1.0, today_cloud * rng.uniform(0.85, 1.15)))
                pv_forecast_today_total = pv_forecast_kwh(dt.timetuple().tm_yday, today_estimate)
            tomorrow_ar = max(0.15, min(1.0, 0.7 * today_cloud + 0.3 * rng.uniform(0.15, 1.0)))
            tomorrow_estimate = max(0.1, min(1.0, tomorrow_ar * rng.uniform(0.8, 1.2)))
            tomorrow_doy = (dt + timedelta(days=1)).timetuple().tm_yday
            pv_forecast_next_total = pv_forecast_kwh(tomorrow_doy, tomorrow_estimate)
        pv_actual_today_kwh += (pv_w / 1000) * step_hours
        pv_rest_heute = round(max(0.0, pv_forecast_today_total - pv_actual_today_kwh), 3)

        balkon_pv_w = gen_balkon_pv_power(dt, weather, rng)
        if day_key != balkon_day:
            balkon_day = day_key
            balkon_ertrag_heute_total = 0.0
            balkon_geladen_heute_total = 0.0
            balkon_entladen_heute_total = 0.0

        charge_kwh = 0.0
        discharge_kwh = 0.0
        if balkon_pv_w > 0:
            capacity_left_kwh = max(0.0, BALKON_CAPACITY_KWH - balkon_soc_kwh)
            charge_kwh = min((balkon_pv_w / 1000) * step_hours, capacity_left_kwh)
            balkon_charge_w = charge_kwh / step_hours
            balkon_soc_kwh += charge_kwh
            balkon_discharge_w = 0.0
            # Überschuss, sobald der Speicher voll ist, geht direkt raus statt
            # verworfen zu werden — wie bei einem realen Gerät ohne Abregelung.
            balkon_hausabgabe_w = balkon_pv_w - balkon_charge_w
        else:
            balkon_charge_w = 0.0
            min_kwh = BALKON_CAPACITY_KWH * BALKON_MIN_SOC
            if balkon_soc_kwh > min_kwh:
                target_w = max(0.0, BALKON_DISCHARGE_TARGET_W + rng.uniform(-8, 8))
                max_w_from_capacity = (balkon_soc_kwh - min_kwh) / step_hours * 1000
                balkon_discharge_w = min(target_w, max_w_from_capacity)
                discharge_kwh = (balkon_discharge_w / 1000) * step_hours
                balkon_soc_kwh -= discharge_kwh
            else:
                balkon_discharge_w = 0.0
            balkon_hausabgabe_w = balkon_discharge_w

        balkon_ertrag_kwh = (balkon_pv_w / 1000) * step_hours
        balkon_ertrag_gesamt_total += balkon_ertrag_kwh
        balkon_ertrag_heute_total += balkon_ertrag_kwh
        balkon_geladen_gesamt_total += charge_kwh
        balkon_geladen_heute_total += charge_kwh
        balkon_entladen_gesamt_total += discharge_kwh
        balkon_entladen_heute_total += discharge_kwh
        balkon_soc_pct = round(balkon_soc_kwh / BALKON_CAPACITY_KWH * 100, 1)

        series["balkon_pv"].append((ts, round(balkon_pv_w, 1)))
        series["balkon_ladeleistung"].append((ts, round(balkon_charge_w, 1)))
        series["balkon_entladeleistung"].append((ts, round(balkon_discharge_w, 1)))
        series["balkon_hausabgabe"].append((ts, round(balkon_hausabgabe_w, 1)))
        series["balkon_soc_pct"].append((ts, balkon_soc_pct))
        series["balkon_speicher_stand"].append((ts, round(balkon_soc_kwh, 3)))

        balkon_online_now = 1.0 if (balkon_pv_w > 0 or balkon_discharge_w > 0) else 0.0
        if balkon_online_now != balkon_online_state:
            balkon_online_state = balkon_online_now
            series["balkon_online"].append((ts, balkon_online_now))

        if day_key != heim_day:
            heim_day = day_key
            heim_geladen_heute_total = 0.0
            heim_entladen_heute_total = 0.0

        # Anders als das Balkonkraftwerk hat der Heimspeicher keine eigene
        # PV — er hängt am Hybrid-Wechselrichter der Dachanlage und lädt aus
        # deren Überschuss (Restlast nach Abzug der Balkonkraftwerk-
        # Hausabgabe), statt den Rest sofort einzuspeisen.
        net_remaining_load_w = load_w - balkon_hausabgabe_w
        heim_geladen_kwh = 0.0
        heim_entladen_kwh = 0.0
        if pv_w > net_remaining_load_w:
            surplus_w = pv_w - net_remaining_load_w
            heim_capacity_left_kwh = max(0.0, HEIM_CAPACITY_KWH - heim_soc_kwh)
            heim_charge_w = min(surplus_w, HEIM_MAX_CHARGE_W, heim_capacity_left_kwh / step_hours * 1000)
            heim_geladen_kwh = (heim_charge_w / 1000) * step_hours
            heim_soc_kwh += heim_geladen_kwh
            heim_discharge_w = 0.0
        else:
            deficit_w = net_remaining_load_w - pv_w
            heim_charge_w = 0.0
            heim_min_kwh = HEIM_CAPACITY_KWH * HEIM_MIN_SOC
            heim_available_kwh = max(0.0, heim_soc_kwh - heim_min_kwh)
            heim_discharge_w = min(deficit_w, HEIM_MAX_DISCHARGE_W, heim_available_kwh / step_hours * 1000)
            heim_entladen_kwh = (heim_discharge_w / 1000) * step_hours
            heim_soc_kwh -= heim_entladen_kwh

        heim_geladen_gesamt_total += heim_geladen_kwh
        heim_geladen_heute_total += heim_geladen_kwh
        heim_entladen_gesamt_total += heim_entladen_kwh
        heim_entladen_heute_total += heim_entladen_kwh
        heim_soc_pct = round(heim_soc_kwh / HEIM_CAPACITY_KWH * 100, 1)

        series["heim_ladeleistung"].append((ts, round(heim_charge_w, 1)))
        series["heim_entladeleistung"].append((ts, round(heim_discharge_w, 1)))
        series["heim_soc_pct"].append((ts, heim_soc_pct))
        series["heim_speicher_stand"].append((ts, round(heim_soc_kwh, 3)))

        heim_online_now = 1.0 if (heim_charge_w > 0 or heim_discharge_w > 0) else 0.0
        if heim_online_now != heim_online_state:
            heim_online_state = heim_online_now
            series["heim_online"].append((ts, heim_online_now))

        # Bool-Sensoren als reine Übergänge (wie ein reales binary_sensor,
        # das nur bei Zustandswechsel meldet) statt eines Werts pro Schritt.
        for key, watts in (("waschmaschine", waschmaschine_w), ("spuelmaschine", spuelmaschine_w), ("trockner", trockner_w)):
            new_state = 1.0 if watts > 0 else 0.0
            if new_state != on_state[key]:
                on_state[key] = new_state
                series[f"{key}_an"].append((ts, new_state))
        rain_now = 1.0 if _rain_active(rain_schedule.get(dt.date().isoformat(), ()), dt) else 0.0
        if rain_now != rain_state:
            rain_state = rain_now
            series["regensensor"].append((ts, rain_now))

        pv_ertrag_total += (pv_w / 1000) * step_hours
        # Balkonkraftwerk-Hausabgabe mindert wie im echten Netzanschluss den
        # Bezug, ohne eigenen Zähler/eigene Einspeisevergütung zu haben. Der
        # Heimspeicher hängt dagegen direkt am Hauptzähler: Laden entzieht
        # dem Netz Überschuss, der sonst eingespeist würde (erhöht grid_flow
        # Richtung Bezug/weniger Einspeisung), Entladen deckt Bezug ab
        # (senkt grid_flow) — beides bereits in heim_charge_w/heim_discharge_w
        # berücksichtigt, siehe deren Herleitung aus net_remaining_load_w oben.
        grid_flow_w = load_w - pv_w - balkon_hausabgabe_w + heim_charge_w - heim_discharge_w
        if grid_flow_w > 0:
            bezug_total += (grid_flow_w / 1000) * step_hours
        else:
            einspeisung_total += (-grid_flow_w / 1000) * step_hours
        waschmaschine_energie_total += (waschmaschine_w / 1000) * step_hours
        spuelmaschine_energie_total += (spuelmaschine_w / 1000) * step_hours
        trockner_energie_total += (trockner_w / 1000) * step_hours
        wallbox_energie_total += (wallbox_w / 1000) * step_hours

        if index % COUNTER_EVERY_N == 0:
            series["pv_ertrag"].append((ts, round(pv_ertrag_total, 3)))
            series["stromzaehler_bezug"].append((ts, round(bezug_total, 3)))
            series["stromzaehler_einspeisung"].append((ts, round(einspeisung_total, 3)))
            series["waschmaschine_energie"].append((ts, round(waschmaschine_energie_total, 3)))
            series["spuelmaschine_energie"].append((ts, round(spuelmaschine_energie_total, 3)))
            series["trockner_energie"].append((ts, round(trockner_energie_total, 3)))
            series["wallbox_energie"].append((ts, round(wallbox_energie_total, 3)))
            series["balkon_ertrag_gesamt"].append((ts, round(balkon_ertrag_gesamt_total, 3)))
            series["balkon_ertrag_heute"].append((ts, round(balkon_ertrag_heute_total, 3)))
            series["balkon_geladen_gesamt"].append((ts, round(balkon_geladen_gesamt_total, 3)))
            series["balkon_geladen_heute"].append((ts, round(balkon_geladen_heute_total, 3)))
            series["balkon_entladen_gesamt"].append((ts, round(balkon_entladen_gesamt_total, 3)))
            series["balkon_entladen_heute"].append((ts, round(balkon_entladen_heute_total, 3)))
            series["pv_prognose_rest_heute"].append((ts, pv_rest_heute))
            series["pv_prognose_morgen"].append((ts, round(pv_forecast_next_total, 3)))
            series["heim_geladen_gesamt"].append((ts, round(heim_geladen_gesamt_total, 3)))
            series["heim_geladen_heute"].append((ts, round(heim_geladen_heute_total, 3)))
            series["heim_entladen_gesamt"].append((ts, round(heim_entladen_gesamt_total, 3)))
            series["heim_entladen_heute"].append((ts, round(heim_entladen_heute_total, 3)))

    return series


def gen_constant_series(start: datetime, end: datetime, step_minutes: int, value: float) -> list[Row]:
    """Für reine Konfigurations-/Kennzahl-Sensoren wie Speicherkapazitäten,
    die sich nicht über die Zeit ändern, aber wie alle anderen Demo-
    Entitäten trotzdem eine durchgehende Historie erhalten sollen."""
    return [(dt.timestamp(), value) for dt in _time_range(start, end, step_minutes)]


def gen_water_counter(start: datetime, end: datetime, rng: random.Random, start_total: float | None = None) -> list[Row]:
    rows: list[Row] = []
    total = start_total if start_total is not None else rng.uniform(120, 600)
    for dt in _time_range(start, end, COUNTER_STEP_MINUTES):
        hour = dt.hour
        active_hours = 6 <= hour <= 23
        if active_hours and rng.random() < 0.35:
            total += rng.uniform(0.004, 0.045)
        rows.append((dt.timestamp(), round(total, 3)))
    return rows


def gen_presence(start: datetime, end: datetime, rng: random.Random, start_state: float | None = None) -> list[Row]:
    def prob_home(hour: int) -> float:
        if 0 <= hour < 7:
            return 0.95
        if 7 <= hour < 8:
            return 0.6
        if 8 <= hour < 16:
            return 0.15
        if 16 <= hour < 17:
            return 0.5
        return 0.9

    rows: list[Row] = []
    state = start_state if start_state is not None else 1.0
    recheck_prob = 0.12  # pro Schritt geprüft, nicht bei jedem Schritt neu gewürfelt
    for dt in _time_range(start, end, PRESENCE_STEP_MINUTES):
        if rng.random() < recheck_prob:
            state = 1.0 if rng.random() < prob_home(dt.hour) else 0.0
        if not rows or rows[-1][1] != state:
            rows.append((dt.timestamp(), state))
    return rows


def month_start(dt: datetime) -> datetime:
    return dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def write_entity(data_dir: Path, index: Index, tz: ZoneInfo, entity: DemoEntity, rows: list[Row]) -> None:
    index.get_or_create_entity(
        entity.entity_id, entity.domain, entity.state_class, entity.unit,
        friendly_name=entity.friendly_name,
    )
    current_month_start = month_start(datetime.now(tz)).timestamp()
    past_rows = [r for r in rows if r[0] < current_month_start]
    current_rows = [r for r in rows if r[0] >= current_month_start]

    # Zwei Aufrufe statt einem: import_rows() legt den laufenden Monat nur
    # dann im Hot Buffer statt als Archivdatei an, wenn die Entität zu diesem
    # Zeitpunkt bereits einen ersten Wert (first_ts) hat (siehe
    # symcon_import._classify_months). Für eine brandneue Entität stimmt das
    # erst NACH dem ersten Aufruf — deshalb erst die abgeschlossenen Monate,
    # dann getrennt der laufende.
    if past_rows:
        import_rows(data_dir, index, past_rows, entity.entity_id, tz, source_label="demo")
    if current_rows:
        import_rows(data_dir, index, current_rows, entity.entity_id, tz, source_label="demo")


# Entity-ID -> Schlüssel in simulate_household()s counter_seed-Dict (siehe
# dortiger Kommentar) — beim Fortsetzen (--append) wird hierüber der aktuell
# gespeicherte Zählerstand jeder Entität als neuer Startwert übernommen.
COUNTER_SEED_ENTITY_KEYS = {
    "sensor.demo_pv_ertrag": "pv_ertrag",
    "sensor.demo_stromzaehler_bezug": "bezug",
    "sensor.demo_stromzaehler_einspeisung": "einspeisung",
    "sensor.demo_waschmaschine_energie": "waschmaschine_energie",
    "sensor.demo_spuelmaschine_energie": "spuelmaschine_energie",
    "sensor.demo_trockner_energie": "trockner_energie",
    "sensor.demo_wallbox_energie": "wallbox_energie",
}
APPLIANCE_SEED_ENTITY_KEYS = {
    "binary_sensor.demo_waschmaschine_an": "waschmaschine",
    "binary_sensor.demo_spuelmaschine_an": "spuelmaschine",
    "binary_sensor.demo_trockner_an": "trockner",
}
# Repräsentative, bei JEDEM Simulationsschritt geschriebene Entität — ihr
# last_ts dient als Anker dafür, "bis wohin wurde die Demo-Instanz zuletzt
# simuliert" (anders als Schalter/Zähler, die absichtlich nur sporadisch
# schreiben und deshalb kein verlässliches Lückenmaß wären).
APPEND_ANCHOR_ENTITY_ID = "sensor.demo_gesamtwirkleistung"


def _last_actual_point(data_dir: Path, entity_id: str) -> Row | None:
    """Letzter tatsächlich gespeicherter (ts, value)-Punkt einer Entität,
    direkt aus Hot Buffer/Archiv gelesen statt über entities.last_value —
    Letzteres wird nur vom echten Ingestion-Pfad gepflegt (complete_ingest_event())
    und bleibt für per import_rows() geschriebene Daten (Demo, CSV-/Symcon-
    Import) dauerhaft NULL, siehe derselbe Bug/Workaround in
    _dashboard_tiles_context() (main.py)/dashboard-tiles.js. Dieselbe Datei-
    Fundlogik wie reconcile._entity_storage_stats(), hier zusätzlich mit dem
    Wert statt nur dem Zeitstempel."""
    best: Row | None = None
    hot_dir = storage_area_dir(data_dir, "hot")
    for path in sorted(hot_dir.glob(f"{entity_id}-*.csv")) if hot_dir.exists() else []:
        for ts, value, _event_id in hotbuffer.iter_records(path):
            if best is None or ts > best[0]:
                best = (ts, value)
    if best is not None:
        return best
    archive_dir = entity_dir(data_dir, "archive", entity_id)
    archive_files = sorted(archive_dir.glob("*.parquet")) if archive_dir.exists() else []
    if not archive_files:
        return None
    table = pq.read_table(archive_files[-1])
    if table.num_rows == 0:
        return None
    ts_col = table.column("ts").to_pylist()
    value_col = table.column("value").to_pylist()
    last_index = max(range(len(ts_col)), key=lambda i: ts_col[i])
    return (ts_col[last_index], value_col[last_index])


def _read_counter_seed(data_dir: Path) -> dict[str, float]:
    seed: dict[str, float] = {}
    for entity_id, key in COUNTER_SEED_ENTITY_KEYS.items():
        point = _last_actual_point(data_dir, entity_id)
        if point is not None:
            seed[key] = point[1]
    return seed


def _read_appliance_seed(data_dir: Path) -> dict[str, float]:
    seed: dict[str, float] = {}
    for entity_id, key in APPLIANCE_SEED_ENTITY_KEYS.items():
        point = _last_actual_point(data_dir, entity_id)
        if point is not None:
            seed[key] = point[1]
    return seed


def _read_last_value(data_dir: Path, entity_id: str) -> float | None:
    point = _last_actual_point(data_dir, entity_id)
    return point[1] if point is not None else None


BALKON_COUNTER_SEED_ENTITY_KEYS = {
    "sensor.demo_balkonkraftwerk_speicher_stand": "soc_kwh",
    "sensor.demo_balkonkraftwerk_ertrag_gesamt": "ertrag_gesamt",
    "sensor.demo_balkonkraftwerk_geladen_gesamt": "geladen_gesamt",
    "sensor.demo_balkonkraftwerk_entladen_gesamt": "entladen_gesamt",
}


def _read_balkon_counter_seed(data_dir: Path) -> dict[str, float]:
    seed: dict[str, float] = {}
    for entity_id, key in BALKON_COUNTER_SEED_ENTITY_KEYS.items():
        point = _last_actual_point(data_dir, entity_id)
        if point is not None:
            seed[key] = point[1]
    return seed


def _read_balkon_heute_seed(data_dir: Path, tz: ZoneInfo) -> tuple[str | None, dict[str, float]]:
    """Tag der letzten Aufzeichnung plus die drei "_heute"-Zählerstände zu
    diesem Zeitpunkt — nur bei gleichem Kalendertag beim Fortsetzen relevant,
    siehe Kommentar zu balkon_day in simulate_household()."""
    anchor = _last_actual_point(data_dir, "sensor.demo_balkonkraftwerk_ertrag_heute")
    if anchor is None:
        return None, {}
    day = datetime.fromtimestamp(anchor[0], tz).date().isoformat()
    values: dict[str, float] = {"ertrag_heute": anchor[1]}
    for entity_id, key in (
        ("sensor.demo_balkonkraftwerk_geladen_heute", "geladen_heute"),
        ("sensor.demo_balkonkraftwerk_entladen_heute", "entladen_heute"),
    ):
        point = _last_actual_point(data_dir, entity_id)
        if point is not None:
            values[key] = point[1]
    return day, values


HEIM_COUNTER_SEED_ENTITY_KEYS = {
    "sensor.demo_heimspeicher_speicher_stand": "soc_kwh",
    "sensor.demo_heimspeicher_geladen_gesamt": "geladen_gesamt",
    "sensor.demo_heimspeicher_entladen_gesamt": "entladen_gesamt",
}


def _read_heim_counter_seed(data_dir: Path) -> dict[str, float]:
    seed: dict[str, float] = {}
    for entity_id, key in HEIM_COUNTER_SEED_ENTITY_KEYS.items():
        point = _last_actual_point(data_dir, entity_id)
        if point is not None:
            seed[key] = point[1]
    return seed


def _read_heim_heute_seed(data_dir: Path, tz: ZoneInfo) -> tuple[str | None, dict[str, float]]:
    """Analog zu _read_balkon_heute_seed(), nur ohne "ertrag_heute" — der
    Heimspeicher hat keine eigene PV, daher dient hier
    sensor.demo_heimspeicher_geladen_heute als Tages-Anker."""
    anchor = _last_actual_point(data_dir, "sensor.demo_heimspeicher_geladen_heute")
    if anchor is None:
        return None, {}
    day = datetime.fromtimestamp(anchor[0], tz).date().isoformat()
    values: dict[str, float] = {"geladen_heute": anchor[1]}
    point = _last_actual_point(data_dir, "sensor.demo_heimspeicher_entladen_heute")
    if point is not None:
        values["entladen_heute"] = point[1]
    return day, values


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-dir", type=Path, required=True, help="Zeitarchiv-Datenverzeichnis (wird bei Bedarf angelegt)")
    parser.add_argument("--months", type=int, default=6, help="Wie viele Monate Historie erzeugt werden (Standard: 6)")
    parser.add_argument("--tz", default="Europe/Berlin", help="IANA-Zeitzone (Standard: Europe/Berlin)")
    parser.add_argument("--seed", type=int, default=42, help="Zufalls-Seed für reproduzierbare Läufe (Standard: 42)")
    parser.add_argument("--clean", action="store_true", help="Werte vorhandener demo_*-Entitäten im Zielverzeichnis zuerst bereinigen (Entitäten selbst bleiben bestehen)")
    parser.add_argument("--append", action="store_true", help="Statt die komplette Historie neu zu würfeln: nur die Werte seit dem letzten Lauf ergänzen (--months wird dabei ignoriert) — macht aus der Demo-Instanz ein 'lebendes' System, z. B. per Cron. Ohne vorhandene Demo-Daten wird automatisch auf eine normale Vollerzeugung zurückgefallen")
    parser.add_argument(
        "--clear", nargs="?", choices=["values", "entities"], const="entities", default=None,
        help=(
            "Eigenständige Aktion statt Erzeugen: löscht vorhandene demo_*-"
            "Entitäten und beendet sich danach, OHNE etwas neu zu erzeugen "
            "(anders als --clean, das Werte bereinigt UND direkt anschließend "
            "die Historie neu würfelt). 'values' entfernt nur die Werte "
            "(Entität/Konfiguration bleibt bestehen, wie der Bereinigungs-"
            "schritt von --clean für sich allein). 'entities' entfernt die "
            "demo_*-Entitäten vollständig inkl. Konfiguration — Dashboards/"
            "Referenzen auf diese Entity-IDs zeigen danach ins Leere. Ohne "
            "Wert (nur '--clear') gilt ebenfalls 'entities'. Schließt sich "
            "mit --clean/--append/--months aus."
        ),
    )
    args = parser.parse_args()
    if args.append and args.clean:
        parser.error("--append und --clean schließen sich gegenseitig aus.")
    if args.clear and (args.clean or args.append):
        parser.error("--clear schließt sich mit --clean und --append gegenseitig aus.")

    tz = ZoneInfo(args.tz)
    index = Index(args.data_dir / "index.sqlite")

    if args.clear:
        existing = {e["entity_id"] for e in index.list_entities()}
        wanted = {e.entity_id for e in DEMO_ENTITIES}
        targets = existing & wanted
        if args.clear == "values":
            for entity_id in targets:
                delete_all_values(args.data_dir, index, entity_id)
            print(f"{len(targets)} vorhandene Demo-Entität(en) geleert (nur Werte entfernt, "
                  "Konfiguration bleibt bestehen).")
        else:
            for entity_id in targets:
                delete_entity(args.data_dir, index, entity_id)
            print(f"{len(targets)} vorhandene Demo-Entität(en) vollständig entfernt (inkl. Konfiguration).")
        return

    rng = random.Random(args.seed)
    now = datetime.now(tz)

    if args.clean:
        existing = {e["entity_id"] for e in index.list_entities()}
        wanted = {e.entity_id for e in DEMO_ENTITIES}
        # delete_all_values() statt delete_entity(): entfernt nur die Werte,
        # behält die Entität (Konfiguration, first_ts=NULL für den
        # anschließenden Neuaufbau) durchgehend im Index — Dashboards/Charts/
        # Tabellen, die auf diese Entity-IDs verweisen, verlieren dadurch nie
        # kurzzeitig ihre Referenz.
        for entity_id in existing & wanted:
            delete_all_values(args.data_dir, index, entity_id)
        print(f"{len(existing & wanted)} vorhandene Demo-Entität(en) bereinigt.")

    counter_seed: dict[str, float] = {}
    appliance_seed: dict[str, float] = {}
    rain_seed = 0.0
    water_seed: float | None = None
    presence_seed: float | None = None
    balkon_seed: dict[str, float] = {}
    balkon_heute_seed: tuple[str | None, dict[str, float]] = (None, {})
    balkon_online_seed = 0.0
    heim_seed: dict[str, float] = {}
    heim_heute_seed: tuple[str | None, dict[str, float]] = (None, {})
    heim_online_seed = 0.0

    if args.append:
        anchor = index.get_entity(APPEND_ANCHOR_ENTITY_ID)
        if anchor is None or anchor["last_ts"] is None:
            print("Keine vorhandenen Demo-Daten gefunden — erzeuge stattdessen die volle "
                  f"Historie ({args.months} Monate).")
            start = now - timedelta(days=args.months * 30)
        else:
            # Einen Schritt nach dem letzten bekannten Wert statt genau darauf —
            # Überlappung wäre ohnehin unkritisch (import_rows() dedupliziert
            # nach Zeitstempel), so entsteht aber gar nicht erst unnötig viel
            # verworfene Redundanz.
            start = datetime.fromtimestamp(anchor["last_ts"], tz) + timedelta(minutes=CONTINUOUS_STEP_MINUTES)
            if start >= now:
                print("Demo-Daten sind bereits aktuell — nichts zu ergänzen.")
                return
            counter_seed = _read_counter_seed(args.data_dir)
            appliance_seed = _read_appliance_seed(args.data_dir)
            rain_seed = _read_last_value(args.data_dir, "binary_sensor.demo_regensensor") or 0.0
            water_seed = _read_last_value(args.data_dir, "sensor.demo_wasserzaehler")
            presence_seed = _read_last_value(args.data_dir, "binary_sensor.demo_praesenz_wohnzimmer")
            balkon_seed = _read_balkon_counter_seed(args.data_dir)
            balkon_heute_seed = _read_balkon_heute_seed(args.data_dir, tz)
            balkon_online_seed = _read_last_value(args.data_dir, "binary_sensor.demo_balkonkraftwerk_online") or 0.0
            heim_seed = _read_heim_counter_seed(args.data_dir)
            heim_heute_seed = _read_heim_heute_seed(args.data_dir, tz)
            heim_online_seed = _read_last_value(args.data_dir, "binary_sensor.demo_heimspeicher_online") or 0.0
            print(f"Ergänze Demo-Daten ab {start.isoformat()} bis {now.isoformat()}.")
    else:
        start = now - timedelta(days=args.months * 30)

    weather = WeatherContext(start, now, rng)
    schedules = build_appliance_schedules(start, now, rng)
    rain_schedule = build_rain_schedule(start, now, weather, rng)
    household = simulate_household(
        start, now, tz, rng, weather, schedules, rain_schedule,
        counter_seed=counter_seed, appliance_seed=appliance_seed, rain_seed=rain_seed,
        balkon_seed=balkon_seed, balkon_heute_seed=balkon_heute_seed,
        balkon_online_seed=balkon_online_seed,
        heim_seed=heim_seed, heim_heute_seed=heim_heute_seed,
        heim_online_seed=heim_online_seed,
    )

    rows_by_key = {
        "sensor.demo_wohnzimmer_temperatur": household["indoor_temp"],
        "sensor.demo_aussentemperatur": household["outdoor_temp"],
        "sensor.demo_luftfeuchte": household["humidity"],
        "sensor.demo_wohnzimmer_luftfeuchte": household["indoor_humidity"],
        "sensor.demo_wind": household["wind"],
        "sensor.demo_gesamtwirkleistung": household["load_power"],
        "sensor.demo_heizung": household["heizung"],
        "sensor.demo_waschmaschine": household["waschmaschine"],
        "sensor.demo_waschmaschine_energie": household["waschmaschine_energie"],
        "binary_sensor.demo_waschmaschine_an": household["waschmaschine_an"],
        "sensor.demo_spuelmaschine": household["spuelmaschine"],
        "sensor.demo_spuelmaschine_energie": household["spuelmaschine_energie"],
        "binary_sensor.demo_spuelmaschine_an": household["spuelmaschine_an"],
        "sensor.demo_trockner": household["trockner"],
        "sensor.demo_trockner_energie": household["trockner_energie"],
        "binary_sensor.demo_trockner_an": household["trockner_an"],
        "sensor.demo_wallbox_leistung": household["wallbox"],
        "sensor.demo_wallbox_energie": household["wallbox_energie"],
        "sensor.demo_pv_leistung": household["pv_power"],
        "sensor.demo_pv_ertrag": household["pv_ertrag"],
        "sensor.demo_pv_prognose_rest_heute": household["pv_prognose_rest_heute"],
        "sensor.demo_pv_prognose_morgen": household["pv_prognose_morgen"],
        "sensor.demo_co2_intensitaet": household["co2_intensitaet"],
        "sensor.demo_stromzaehler_bezug": household["stromzaehler_bezug"],
        "sensor.demo_stromzaehler_einspeisung": household["stromzaehler_einspeisung"],
        "sensor.demo_balkonkraftwerk_pv_leistung": household["balkon_pv"],
        "sensor.demo_balkonkraftwerk_ladeleistung": household["balkon_ladeleistung"],
        "sensor.demo_balkonkraftwerk_entladeleistung": household["balkon_entladeleistung"],
        "sensor.demo_balkonkraftwerk_speicher_soc": household["balkon_soc_pct"],
        "sensor.demo_balkonkraftwerk_speicher_stand": household["balkon_speicher_stand"],
        "sensor.demo_balkonkraftwerk_speicher_kapazitaet": gen_constant_series(
            start, now, COUNTER_STEP_MINUTES, BALKON_CAPACITY_KWH * 1000,
        ),
        "sensor.demo_balkonkraftwerk_hausabgabe": household["balkon_hausabgabe"],
        "sensor.demo_balkonkraftwerk_ertrag_heute": household["balkon_ertrag_heute"],
        "sensor.demo_balkonkraftwerk_ertrag_gesamt": household["balkon_ertrag_gesamt"],
        "sensor.demo_balkonkraftwerk_geladen_heute": household["balkon_geladen_heute"],
        "sensor.demo_balkonkraftwerk_geladen_gesamt": household["balkon_geladen_gesamt"],
        "sensor.demo_balkonkraftwerk_entladen_heute": household["balkon_entladen_heute"],
        "sensor.demo_balkonkraftwerk_entladen_gesamt": household["balkon_entladen_gesamt"],
        "binary_sensor.demo_balkonkraftwerk_online": household["balkon_online"],
        "sensor.demo_heimspeicher_kapazitaet": gen_constant_series(start, now, COUNTER_STEP_MINUTES, HEIM_CAPACITY_KWH),
        "sensor.demo_heimspeicher_ladeleistung": household["heim_ladeleistung"],
        "sensor.demo_heimspeicher_entladeleistung": household["heim_entladeleistung"],
        "sensor.demo_heimspeicher_speicher_soc": household["heim_soc_pct"],
        "sensor.demo_heimspeicher_speicher_stand": household["heim_speicher_stand"],
        "sensor.demo_heimspeicher_geladen_heute": household["heim_geladen_heute"],
        "sensor.demo_heimspeicher_geladen_gesamt": household["heim_geladen_gesamt"],
        "sensor.demo_heimspeicher_entladen_heute": household["heim_entladen_heute"],
        "sensor.demo_heimspeicher_entladen_gesamt": household["heim_entladen_gesamt"],
        "binary_sensor.demo_heimspeicher_online": household["heim_online"],
        "sensor.demo_wasserzaehler": gen_water_counter(start, now, rng, start_total=water_seed),
        "binary_sensor.demo_praesenz_wohnzimmer": gen_presence(start, now, rng, start_state=presence_seed),
        "binary_sensor.demo_regensensor": household["regensensor"],
    }

    for entity in DEMO_ENTITIES:
        rows = rows_by_key[entity.entity_id]
        write_entity(args.data_dir, index, tz, entity, rows)
        print(f"{entity.entity_id}: {len(rows)} Werte geschrieben")

    report = reconcile.audit_storage_metadata(
        args.data_dir, index, tz,
        entity_ids=[e.entity_id for e in DEMO_ENTITIES],
        repair=True,
    )
    print(f"Indexabgleich: {report['entities_checked']} Entität(en) geprüft, "
          f"{len(report['mismatches'])} Abweichung(en) repariert.")
    print(f"\nFertig. ZEITARCHIV_DATA_DIR={args.data_dir} beim Start der App verwenden "
          f"(siehe docs/development.md) bzw. eine bereits laufende Instanz auf diesem "
          f"Datenverzeichnis neu starten.")


if __name__ == "__main__":
    main()
