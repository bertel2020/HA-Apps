"""Zeitraum der Demo-Daten-Generierung: 3 Jahre statt vormals 6 Monate
(Nutzerwunsch). Betrifft ausschließlich den In-App-Demo-Modus
(build_demo_worker() in app/demo_mode.py ruft run_generation() ohne eigenes
months=, übernimmt also diesen Default) — das eigenständige CLI-Skript
scripts/generate_demo_data.py hat seinen eigenen --months-Default (6) und
bleibt bewusst unverändert.
"""

import inspect
import random
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.demo_generation import (
    DEMO_ENTITIES,
    WeatherContext,
    build_appliance_schedules,
    build_rain_schedule,
    run_generation,
    simulate_household,
)


def test_the_app_generates_three_years_of_history_by_default() -> None:
    months_default = inspect.signature(run_generation).parameters["months"].default
    assert months_default == 36


# Sechs weitere Verbraucher (Nutzerwunsch: mehr Testdaten für die
# Verbraucheranteile-8er-Grenze/"weitere anzeigen") — Kühlschrank, Herd,
# Homeoffice, Entertainment, Boiler, zweite Ladeeinheit.
NEUE_VERBRAUCHER = ["kuehlschrank", "herd", "homeoffice", "entertainment", "boiler", "wallbox2"]


def test_neue_verbraucher_sind_als_demo_entitaeten_registriert() -> None:
    ids = {e.entity_id for e in DEMO_ENTITIES}
    for name in NEUE_VERBRAUCHER:
        power_suffix = "wallbox2_leistung" if name == "wallbox2" else name
        assert f"sensor.demo_{power_suffix}" in ids
        assert f"sensor.demo_{name}_energie" in ids


def test_neue_verbraucher_liefern_plausible_leistung_und_monoton_steigende_energie() -> None:
    """Simuliert einen kurzen Zeitraum direkt über simulate_household() (ohne
    Storage-Schicht, siehe test_energiedashboard_flow.py für dasselbe
    Prinzip) und prüft: Leistung nie negativ, Energiezähler nie fallend —
    dieselben Grundeigenschaften, die für die vier bereits vorhandenen
    Verbraucher (Waschmaschine & Co.) gelten müssen, jetzt auch für die
    neuen sechs."""
    tz = ZoneInfo("Europe/Berlin")
    end = datetime(2024, 3, 20, tzinfo=tz)
    start = end - timedelta(days=14)
    rng = random.Random(42)
    weather = WeatherContext(start, end, rng)
    schedules = build_appliance_schedules(start, end, rng)
    rain_schedule = build_rain_schedule(start, end, weather, rng)
    household = simulate_household(start, end, tz, rng, weather, schedules, rain_schedule)

    for name in NEUE_VERBRAUCHER:
        power = household[name]
        energie = household[f"{name}_energie"]
        assert power, name
        assert all(value >= 0 for _, value in power)
        assert energie
        assert all(b[1] >= a[1] - 1e-9 for a, b in zip(energie, energie[1:])), name
