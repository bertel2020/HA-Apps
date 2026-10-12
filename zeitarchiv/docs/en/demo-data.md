# Demo data

*[Deutsche Version](../demo-data.md)*

`scripts/generate_demo_data.py` fills a Zeitarchiv data directory with
realistic-looking, synthetic sample data — for screenshots, documentation, a
presentable demo instance or simply for local development without a real Home
Assistant connection.

**Relationship to the app's demo mode (from 0.91.0):** Since the in-app "demo
mode" ([User guide → Demo mode](user-guide.md#demo-mode), add-on option, its own
`<DATA_DIR>/demo`), both share the same simulation core
(`app/demo_generation.py::run_generation()`, moved there — this script has
since been a thin CLI wrapper around it, see
[Prerequisites](#prerequisites)). The difference is the occasion: demo mode is
the way for end users and needs no terminal access for it, only an add-on
restart — this script remains for everything that demo mode does not cover:
targeted generation into any host directory outside the add-on (screenshots,
local development without a running server, see
[development.md](development.md)) as well as the actions `--clear`/`--seed`,
which do not exist in the app interface.

The script writes **no format of its own** but uses the same generic write core
that the app's own CSV and Symcon import use too
(`app/storage/symcon_import.py::import_rows()`, see
[ingestion.md](ingestion.md)/[data-model.md](data-model.md)) — the generated
Parquet archives, rollups, the hot buffer and the SQLite index are thus exactly
the files that a real instance would create too.

## What is generated

The tables below list the German `entity_id`s; with `--language en` they follow the English name (`sensor.demo_gesamtwirkleistung` → `sensor.demo_total_active_power`).

69 entities, thematically a single household with rooftop PV system (including
yield forecast), wallbox, an additional balcony power plant with its own
storage, a larger home battery on the rooftop system and the grid CO2
intensity, all with the prefix `demo_` in the entity ID (easy to recognize and
selectively deletable):

| Entity | Type | Unit | Pattern |
| --- | --- | --- | --- |
| `sensor.demo_wohnzimmer_temperatur` | Standard | °C | Daily cycle around 21 °C, slight noise |
| `sensor.demo_aussentemperatur` | Standard | °C | Seasonal (central European profile) + daily cycle + multi-day correlated "weather" |
| `sensor.demo_luftfeuchte` | Standard | % | Outdoor, coupled to outdoor temperature/cloud cover |
| `sensor.demo_wohnzimmer_luftfeuchte` | Standard | % | Indoor, more damped than outdoors, loosely coupled to outdoor humidity |
| `sensor.demo_wind` | Standard | km/h | Daily value (loosely coupled to cloud cover: windier with more clouds) + daily pattern + occasional gusts |
| `sensor.demo_co2_intensitaet` | Standard | g/kWh | Like a CO2 Signal/ElectricityMap sensor: base load at night, dip at midday due to PV feed-in to the grid, slight rise toward the evening peak; additionally lower on sunny/windy days (same cloud cover/wind base as PV/wind) |
| `sensor.demo_gesamtwirkleistung` | Standard | W | Sum of base load, heating and all individual consumers below (incl. wallbox) |
| `sensor.demo_heizung` | Standard | W | Thermostat cycling, share "on" per 30-minute window rises with cold (0 in summer) |
| `sensor.demo_waschmaschine` | Standard | W | Individual cycles (~every 3 days), multi-phase power profile (filling/heating/washing/spinning) |
| `sensor.demo_waschmaschine_energie` | Counter (`total_increasing`) | kWh | Monotonically rising, exact integration of `demo_waschmaschine` |
| `binary_sensor.demo_waschmaschine_an` | Switch | — | On/off, congruent with the cycles of `demo_waschmaschine` |
| `sensor.demo_spuelmaschine` | Standard | W | Individual cycles (mostly in the evening), multi-phase power profile |
| `sensor.demo_spuelmaschine_energie` | Counter (`total_increasing`) | kWh | Monotonically rising, exact integration of `demo_spuelmaschine` |
| `binary_sensor.demo_spuelmaschine_an` | Switch | — | On/off, congruent with the cycles of `demo_spuelmaschine` |
| `sensor.demo_trockner` | Standard | W | Runs only on days with a washing machine cycle, with a delay afterwards |
| `sensor.demo_trockner_energie` | Counter (`total_increasing`) | kWh | Monotonically rising, exact integration of `demo_trockner` |
| `binary_sensor.demo_trockner_an` | Switch | — | On/off, congruent with the cycles of `demo_trockner` |
| `sensor.demo_wallbox_leistung` | Standard | W | Individual charging sessions (~every 3 days in the evening), ramp-up/plateau/taper |
| `sensor.demo_wallbox_energie` | Counter (`total_increasing`) | kWh | Monotonically rising, exact integration of `demo_wallbox_leistung` |
| `sensor.demo_kuehlschrank` | Standard | W | Compressor cycling, constantly recurring independent of time of day/weather (no binary_sensor "_an", see `demo_wallbox_leistung`) |
| `sensor.demo_kuehlschrank_energie` | Counter (`total_increasing`) | kWh | Monotonically rising, exact integration of `demo_kuehlschrank` |
| `sensor.demo_herd` | Standard | W | Single evening block (~85 % daily chance), three-phase profile (heating up/cooking/residual heat) |
| `sensor.demo_herd_energie` | Counter (`total_increasing`) | kWh | Monotonically rising, exact integration of `demo_herd` |
| `sensor.demo_homeoffice` | Standard | W | Weekday-dependent (Mon–Fri, 9 am–5 pm) instead of random chance — the only consumer with this pattern |
| `sensor.demo_homeoffice_energie` | Counter (`total_increasing`) | kWh | Monotonically rising, exact integration of `demo_homeoffice` |
| `sensor.demo_entertainment` | Standard | W | Evening block (~7–11 pm, ~90 % daily chance) |
| `sensor.demo_entertainment_energie` | Counter (`total_increasing`) | kWh | Monotonically rising, exact integration of `demo_entertainment` |
| `sensor.demo_boiler` | Standard | W | Two fixed cycling windows (morning/evening), duty cycle like `demo_heizung`, but coupled to time of day instead of temperature |
| `sensor.demo_boiler_energie` | Counter (`total_increasing`) | kWh | Monotonically rising, exact integration of `demo_boiler` |
| `sensor.demo_wallbox2_leistung` | Standard | W | Second, smaller charging unit (e-bike/second car), rarer than `demo_wallbox_leistung` (~18 % daily chance) |
| `sensor.demo_wallbox2_energie` | Counter (`total_increasing`) | kWh | Monotonically rising, exact integration of `demo_wallbox2_leistung` |
| `sensor.demo_pv_leistung` | Standard | W | Bell curve during the day, 0 at night; daylight length by season, damped by the same cloud cover as outdoor temperature/humidity |
| `sensor.demo_pv_ertrag` | Counter (`total_increasing`) | kWh | Monotonically rising, exact integration of the PV power over time |
| `sensor.demo_pv_prognose_rest_heute` | Standard | kWh | Like a Forecast.Solar sensor: estimated remaining yield of the rooftop system until sunset, roughly estimated once in the morning (with estimation error, not exact foreknowledge) and falls over the day toward 0 with the yield actually collected |
| `sensor.demo_pv_prognose_morgen` | Standard | kWh | Daily forecast for tomorrow, re-estimated once per calendar day (continuation of cloud cover + own uncertainty), stays constant over the day — becomes the basis of `pv_prognose_rest_heute` the next day |
| `sensor.demo_stromzaehler_bezug` | Counter (`total_increasing`) | kWh | Monotonically rising, only when total active power > PV power |
| `sensor.demo_stromzaehler_einspeisung` | Counter (`total_increasing`) | kWh | Monotonically rising, only when PV power > total active power |
| `sensor.demo_balkonkraftwerk_pv_leistung` | Standard | W | Own, smaller secondary system (plug-in solar device): raw module power up to 950 W, hard-capped at 800 W inverter output; own daily window (weaker mornings, acting south-westerly) instead of a copy of the rooftop system |
| `sensor.demo_balkonkraftwerk_ladeleistung` | Standard | W | >0 only while the storage charges (during the day, as long as SoC < 100 %) |
| `sensor.demo_balkonkraftwerk_entladeleistung` | Standard | W | >0 only while the storage discharges (at night, until SoC reaches the discharge protection) |
| `sensor.demo_balkonkraftwerk_speicher_soc` | Standard | % | State of charge 0–100 %, rising during the day/falling at night |
| `sensor.demo_balkonkraftwerk_speicher_stand` | Standard | kWh | Absolute energy content of the 2 kWh storage (`SoC × capacity`), not a counter |
| `sensor.demo_balkonkraftwerk_speicher_kapazitaet` | Standard | **Wh** | Constant 2000 Wh — deliberately in Wh instead of kWh like `speicher_stand`, as a realistic example of different units between thematically related sensors |
| `sensor.demo_balkonkraftwerk_hausabgabe` | Standard | W | Power actually delivered toward the house socket (PV surplus with full storage + storage discharge); reduces `load_power` before import/feed-in are calculated — like a real balcony power plant without its own meter/own feed-in tariff |
| `sensor.demo_balkonkraftwerk_ertrag_heute` | Counter (`total_increasing`) | kWh | PV yield of the balcony power plant, **resets daily at midnight to 0** (as with most micro-inverter apps) |
| `sensor.demo_balkonkraftwerk_ertrag_gesamt` | Counter (`total_increasing`) | kWh | The same PV yield, monotonically rising since "commissioning", no reset |
| `sensor.demo_balkonkraftwerk_geladen_heute` | Counter (`total_increasing`) | kWh | Charged amount of the storage, daily reset like `ertrag_heute` |
| `sensor.demo_balkonkraftwerk_geladen_gesamt` | Counter (`total_increasing`) | kWh | Charged amount of the storage, monotonically rising, no reset |
| `sensor.demo_balkonkraftwerk_entladen_heute` | Counter (`total_increasing`) | kWh | Discharged amount of the storage, daily reset like `ertrag_heute` |
| `sensor.demo_balkonkraftwerk_entladen_gesamt` | Counter (`total_increasing`) | kWh | Discharged amount of the storage, monotonically rising, no reset |
| `binary_sensor.demo_balkonkraftwerk_online` | Switch | — | On as long as PV delivers or the storage discharges; off as soon as the storage reaches the discharge protection at night (until the next solar irradiation) |
| `sensor.demo_heimspeicher_kapazitaet` | Standard | kWh | Constant 10 kWh — considerably larger than the balcony power plant storage, coupled directly to the rooftop system instead of standalone |
| `sensor.demo_heimspeicher_ladeleistung` | Standard | W | >0 only when the rooftop system delivers more than the house currently consumes (after deducting the balcony power plant's house delivery) — charges from the PV surplus that would otherwise be fed in |
| `sensor.demo_heimspeicher_entladeleistung` | Standard | W | >0 only when the rooftop system does not cover the house consumption — covers part of the grid import that would otherwise be needed |
| `sensor.demo_heimspeicher_speicher_soc` | Standard | % | State of charge 0–100 %, follows the PV surplus/deficit of the rooftop system |
| `sensor.demo_heimspeicher_speicher_stand` | Standard | kWh | Absolute energy content of the 10 kWh storage (`SoC × capacity`), not a counter |
| `sensor.demo_heimspeicher_geladen_heute` | Counter (`total_increasing`) | kWh | Charged amount, **daily reset** at midnight |
| `sensor.demo_heimspeicher_geladen_gesamt` | Counter (`total_increasing`) | kWh | Charged amount, monotonically rising, no reset |
| `sensor.demo_heimspeicher_entladen_heute` | Counter (`total_increasing`) | kWh | Discharged amount, daily reset like `geladen_heute` |
| `sensor.demo_heimspeicher_entladen_gesamt` | Counter (`total_increasing`) | kWh | Discharged amount, monotonically rising, no reset |
| `binary_sensor.demo_heimspeicher_online` | Switch | — | On as long as charging or discharging takes place; off when idle (PV covers the house consumption exactly, or the storage is full/empty and not needed at the moment) |
| `sensor.demo_wasserzaehler` | Counter (`total_increasing`) | m³ | Monotonically rising, sparse random increments during the day |
| `binary_sensor.demo_praesenz_wohnzimmer` | Switch | — | Home/away with time-of-day-dependent probability and inertia (no switching every few minutes) |
| `device_tracker.demo_smartphone` | Switch | — | Own, independent home/away simulation (`gen_presence()`) — example of the presence domains `device_tracker`/`person`, which are archived like switches (`home` → on, anything else → off) |
| `binary_sensor.demo_regensensor` | Switch | — | One or several rain windows on some days, probability rises with that day's cloud cover |

Washing machine, dishwasher, dryer and wallbox each have two or three entities
— instantaneous power (W), energy counter (kWh) and, for the three household
appliances, additionally a pure on/off switch —, as real socket/device meters
typically deliver too. The wallbox power also flows into the total active power
and thus also into import/feed-in, just like at the real grid connection.

Two counters instead of one: real bidirectional meters keep import and feed-in
separately (each taken on its own monotonically rising, never negative) — which
of the two is currently growing follows from the sign of total active power
minus PV power **minus the balcony power plant house delivery, plus home
battery charging power, minus home battery discharging power** at any point in
time.

The balcony power plant is a standalone secondary system next to the large
rooftop system, not a replacement for it: smaller module power with a fixed
inverter cap (800 W) and a 2 kWh storage in front of it, which during the day
charges with priority from the balcony PV (not from the rooftop system — both
systems are independent devices without common control) and at night discharges
with fluctuating but on average constant power. The rooftop system
(`pv_leistung`/`pv_ertrag`) remains untouched by this and continues to be
accounted for separately and unchanged. If the storage does not last through the
whole night (in the simulation usually the case around 2–3 am),
`balkonkraftwerk_online` switches to off until PV power is present again the
next morning — a deliberately realistic effect of the chosen storage size, not
an error state.

Both storage units (balcony power plant and home battery) calculate with a
round-trip efficiency of ~92 % (97 % charging × 95 % discharging,
`BALKON_CHARGE_EFFICIENCY`/`BALKON_DISCHARGE_EFFICIENCY` and the `HEIM_*`
counterparts) instead of passing through lossless 1:1: `geladen_*`/`entladen_*`
reflect the measured charge/discharge power (as a real device reports it), the
internal fill level changes by the amount reduced by the efficiency. Without
this loss, `entladen_gesamt` could have caught up with or even overtaken
`geladen_gesamt` over a long simulated history — a computed efficiency above
100 % is physically impossible, however. For the same reason, the initial values
of `geladen_gesamt`/`entladen_gesamt` are no longer rolled independently on the
very first run, but `entladen_gesamt` is derived from `geladen_gesamt`, the
efficiency and the rolled initial fill level.

The home battery is a third, considerably larger storage unit (10 kWh) — unlike
the independent balcony power plant, however, coupled directly to the rooftop
system, as usual in a hybrid inverter setup: it has no PV of its own but charges
from the rooftop system's surplus after deducting the house consumption (net
after the balcony power plant house delivery already deducted) and discharges at
a PV deficit to cover part of the grid import that would otherwise be needed.
Charging/discharging power is limited to 3,000 W (typical for home battery
inverters). Since it hangs directly on the main meter, its charge/discharge
values — unlike with the balcony power plant, which knows only a single
`hausabgabe` — flow with their own sign into import/feed-in: charging
effectively raises the import (or lowers the feed-in), discharging lowers the
import.

Total active power, all consumers (incl. wallbox), PV power/yield, both
electricity meters, indoor humidity, wind, rain sensor, CO2 intensity, the PV
forecast sensors as well as all balcony power plant and home battery entities
arise in a single shared simulation pass (`simulate_household()`) instead of
independently of each other — the counter readings are thus the exact
integration of the same power values that are also written as sensors, not
separately rolled approximations; the on/off switches are pure state
transitions of the same power values instead of random logic of their own.

Cloud cover, temperature deviation and a daily wind value are computed once per
calendar day and coupled to the previous day via a simple AR(1) process, so that
outdoor temperature, humidity, wind, PV power and CO2 intensity look like
connected weather instead of fluctuating independently at random; the rain
probability of a day hangs on the same cloud cover.

The PV forecast sensors deliberately do not use the actual, later cloud cover
but estimate it with their own random error per calendar day
(`pv_prognose_rest_heute` for the current day based on today's cloud cover +
estimation error; `pv_prognose_morgen` additionally via an AR(1) continuation
of today's cloud cover + own estimation error) — a forecast that hit exactly
would no longer be a forecast. The daily total estimated yesterday for
"tomorrow" automatically becomes the basis of `pv_prognose_rest_heute` the next
morning, without being estimated again.

The current, still running month lands — as with real data — in the hot buffer
instead of in a monthly archive; the entity thus shows plausible, current values
"today" as well.

Additionally, the script **sporadically** scatters timestamp duplicates and
short series of "frozen" (equal after rounding) subsequent values into
individual `sensor.*` entities — otherwise "Remove duplicates"/"Condense
repeats" in the "Edit values" → "Clean up" tab would have next to nothing to do
on a fresh demo instance (the simulation itself produces hardly any exact
duplications). Per run this affects only a handful of entities, determined
reproducibly by random seed, no `binary_sensor`/`device_tracker` entities (which
write only on state change anyway, see the table above). Timestamp duplicates
deliberately land only in already completed months, never in the current one — a
duplicate within the current month would be merged by `import_rows()` back to
one occurrence when writing to the hot buffer anyway (the same deduplication
that makes overlapping `--append` runs harmless), so it would never actually
arrive there.

## Prerequisites

- Python environment of the app (`addon/.venv`, see
  [development.md](development.md)) — the script imports `app.storage.*`
  directly, a running server is not needed for it.
- An **empty or own** target data directory. Do not run against the data
  directory of a simultaneously running instance — SQLite access from two
  processes in parallel is not intended.

## Usage

```bash
cd addon
.venv/bin/python3 scripts/generate_demo_data.py --data-dir /path/to/data-directory
```

With the default values, generates 6 months of history for all 69 demo entities
in a fresh (or empty) target directory.

More examples:

```bash
# Only 3 months of history instead of the default 6
.venv/bin/python3 scripts/generate_demo_data.py --data-dir /path/to/data-directory --months 3

# Different random seed for different but still reproducible values
.venv/bin/python3 scripts/generate_demo_data.py --data-dir /path/to/data-directory --seed 7

# Clean an existing demo instance first and regenerate right away
.venv/bin/python3 scripts/generate_demo_data.py --data-dir /path/to/data-directory --clean --months 12
```

| Option | Default | Meaning |
| --- | --- | --- |
| `--data-dir` | *(required)* | Target directory; created if needed |
| `--months` | `6` | How many months of history are generated (calculation: months × 30 days back from now) |
| `--tz` | `Europe/Berlin` | IANA time zone for month/day boundaries |
| `--seed` | `42` | Random seed — the same seed reproducibly generates the same values |
| `--clean` | *(off)* | Cleanly remove existing `demo_*` entities in the target directory before generating (for repeated runs) |
| `--append` | *(off)* | Instead of the complete history, only add the values since the last run (`--months` is ignored) — see [Living demo instance](#living-demo-instance---append). Mutually exclusive with `--clean` |
| `--language {de,en}` | `de` | Language of the demo entities — names **and** `entity_id`s (`en`: `sensor.demo_living_room_temperature` instead of `sensor.demo_wohnzimmer_temperatur`). A run in the other language replaces the existing dataset (entities of the other language are removed, dashboards referencing them point to nothing). In the app's demo mode the interface language applies. The names live in the translation catalog (`app/i18n/en.json`) |
| `--clear {values,entities}` | *(off)* | Standalone action instead of generating — see [Deleting demo data](#deleting-demo-data---clear) |

At the end, the script shows a short summary (number of values written per
entity, result of the subsequent index reconciliation) — except with `--clear`,
which only reports the number of affected entities and then exits immediately.

## Integrating demo data

### Locally, without Docker (venv)

If the server already points to a directory, you can generate directly into it —
then only (re)start the server unless it was already running:

```bash
cd addon
.venv/bin/python3 scripts/generate_demo_data.py --data-dir /tmp/zeitarchiv-data
ZEITARCHIV_DATA_DIR=/tmp/zeitarchiv-data ZEITARCHIV_API_TOKEN=devtoken \
  .venv/bin/uvicorn app.main:app --port 8127
```

If an instance is already running on exactly this directory: stop it first, run
the script, only then start it again.

### Locally with Docker Compose

`docker-compose.dev.yml` binds `ZEITARCHIV_DATA_DIR=/data` to a named volume, not
a host directory — but the script runs natively on the host and cannot write to
this volume directly. Simplest solution: mount a host directory instead for the
demo run.

1. Generate demo data locally: `.venv/bin/python3 scripts/generate_demo_data.py --data-dir ./demo-data`
2. In `docker-compose.dev.yml` (or a local copy/an override), enter
   `volumes: - ./demo-data:/data` instead of the named volume.
3. `docker compose -f docker-compose.dev.yml up --build`

### Real Home Assistant/Supervisor installation

Not the intended way — the data directory of a productive add-on should not be
written to externally. If it is nevertheless desired for a demo/showcase
purpose: stop the add-on beforehand, let the script point with `--data-dir`
directly at the add-on data path, then start the add-on again. A prior backup
(**System → Backup / Restore**) is strongly recommended in this case.

## Regenerating demo data (`--clean`)

Before rewriting, `--clean` cleans the values of all 69 `demo_*` entities — like
**Housekeeping → Storage** in the app, just for all demo entities at once,
without opening the app. Deliberately `delete_all_values()` instead of
`delete_entity()`: the entities themselves remain in the index continuously
throughout the run (only the values are emptied briefly and refilled right
after) — dashboards, charts and comparison tables that you built on these
entity IDs thereby remain untouched and simply show the new values after the
run, instead of referencing an unknown entity in the meantime.

Removing a single demo entity completely (including configuration, not just
values) is possible only via the interface: open the entity → gear icon →
**Remove entity** (see
[user-guide.md](user-guide.md#configuring-an-entity)). For all demo entities at
once see [Deleting demo data](#deleting-demo-data---clear).

## Deleting demo data (`--clear`)

`--clear` is a standalone action instead of a preparation for generating: the
script deletes and then exits immediately, without rolling new history
afterwards — unlike `--clean`, which carries out the same cleanup only as the
first step before an immediate regeneration. Mutually exclusive with `--clean`,
`--append` and `--months`.

```bash
# Delete only values, entities/configuration remain
.venv/bin/python3 scripts/generate_demo_data.py --data-dir /path/to/data-directory --clear values

# Remove demo entities completely (incl. configuration)
.venv/bin/python3 scripts/generate_demo_data.py --data-dir /path/to/data-directory --clear entities

# Without a value: identical to --clear entities
.venv/bin/python3 scripts/generate_demo_data.py --data-dir /path/to/data-directory --clear
```

| Value | Effect |
| --- | --- |
| `values` | Removes only the values of all existing `demo_*` entities (`delete_all_values()`, the same mechanism as the cleanup step of `--clean`) — entity and configuration remain in the index, dashboards/references to these entity IDs stay valid but show no values afterwards. |
| `entities` *(also the default without a value)* | Removes the `demo_*` entities completely including configuration (`delete_entity()`) — dashboards/references to these entity IDs point to nothing afterwards, as with manual removal via the interface, just for all demo entities at once. |

## Living demo instance (`--append`)

`--append` supplements an existing demo instance with the values since the last
run instead of rolling the complete history anew — run regularly (e.g. via
cron), a demo instance thus remains a "living" system that never falls behind
the current date, without recalculating months of data on every run:

```bash
# e.g. every 15 minutes via cron
.venv/bin/python3 scripts/generate_demo_data.py --data-dir /path/to/data-directory --append
```

- The anchor "up to where it was last simulated" is the last timestamp of
  `sensor.demo_gesamtwirkleistung` (written at every simulation step, unlike
  switches/counters with deliberately sporadic writes). If the instance is
  already current, the script exits without writing anything.
- Counters (`_energie`, `_ertrag`, electricity meters, water meter, balcony
  power plant/home battery `_gesamt` counters and both storage levels) and
  switch states pick up from their last actually stored value — no counter jump/
  reset at the connection point. Overlapping timestamps would be harmless
  anyway: `import_rows()` deduplicates afterwards. The balcony power plant/home
  battery `_heute` counters pick up only if the last run ended on the same
  calendar day — otherwise the new day starts at 0 anyway, as with a real daily
  reset.
- If `--append` finds no existing `sensor.demo_gesamtwirkleistung` (empty target
  directory), it automatically falls back to a normal full generation with
  `--months`.
- Do not run it in parallel on the same data directory as an already started
  instance (see [Prerequisites](#prerequisites)) — a cron job thus belongs on a
  data directory whose server is stopped meanwhile, or must plan its restart
  itself.

## Limits

- The values are plausible but not physically exact (rough daylight length, no
  real weather history, simplified device profiles).
- Intended for demo/test data directories, not as a supplement to real Home
  Assistant data in the same instance — the `demo_` entities stand on equal
  footing next to real entities and are not distinguished automatically, except
  by name/prefix.
- The PV forecast sensors (`pv_prognose_rest_heute`/`_morgen`) and
  `co2_intensitaet` are pure `measurement` sensors without a counter reading and
  are deliberately not continued with `--append` (unlike the `total_increasing`
  counters) — with a continuation run in the middle of the day,
  `pv_prognose_rest_heute` starts with a fresh daily estimate instead of
  continuing exactly where the last run left off. Cosmetic jump, not a counter
  jump.
