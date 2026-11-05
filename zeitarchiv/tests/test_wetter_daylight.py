"""Wetterdashboard: Tageslichtkurve aus dem Dämmerungssensor (ROADMAP 1.43)."""

from __future__ import annotations

import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app import wetter_autofill, wetter_daylight as dl, wetter_routes
from app.storage import hotbuffer
from app.storage import rollup as rollup_mod
from app.storage.index import Index
from app.wetter_routes import WetterDependencies, WetterService

TZ = ZoneInfo("Europe/Berlin")
NOW = datetime(2026, 8, 20, 12, 0, tzinfo=TZ)


def _ts(day: int, hour: int, minute: int = 0) -> float:
    return datetime(2026, 8, day, hour, minute, tzinfo=TZ).timestamp()


@pytest.fixture()
def service():
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-daylight-test-"))
    index = Index(tmp / "index.sqlite")
    yield WetterService(WetterDependencies(
        data_dir=tmp, index=index, tz=TZ, templates=None, app_root_context=lambda request: {},
    ))
    index.close()
    shutil.rmtree(tmp, ignore_errors=True)


def _add(service, entity_id, unit, points):
    deps = service.deps
    deps.index.get_or_create_entity(entity_id, "sensor", "measurement", unit)
    for ts, value in points:
        hotbuffer.append(deps.data_dir, entity_id, ts, value, TZ)
        deps.index.record_write(entity_id, ts, value)


def _configure(service, **roles):
    wetter_routes._save_config(service.deps.index, {**wetter_routes._empty_config(), **roles})


#: Ein klarer Sommertag: Rampe morgens, danach hält der Sensor 1.000 lx (keine Zeilen), Rampe abends.
RAMP = [
    (_ts(19, 5, 0), 0.0), (_ts(19, 5, 30), 10.0), (_ts(19, 6, 0), 200.0), (_ts(19, 6, 20), 500.0),
    (_ts(19, 6, 40), 1000.0), (_ts(19, 19, 30), 900.0), (_ts(19, 19, 50), 300.0), (_ts(19, 20, 10), 10.0),
    (_ts(19, 20, 30), 0.0),
]


def test_classify_uses_both_thresholds_inclusively() -> None:
    assert dl.classify(4.9, 5, 400) == dl.NIGHT
    assert dl.classify(5, 5, 400) == dl.DUSK
    assert dl.classify(399.9, 5, 400) == dl.DUSK
    assert dl.classify(400, 5, 400) == dl.DAY


def test_hold_points_fill_gaps_and_extend_to_the_end() -> None:
    points = [(1000.0, 0.0), (1300.0, 1000.0), (50000.0, 800.0)]
    held = dl.hold_points(points, day_start=0.0, until=60000.0, night_lx=5.0)
    assert held[0] == (0.0, 0.0)  # die Nacht vor der ersten Zeile hält ihren Wert
    assert (49999.0, 1000.0) in held  # der gehaltene Mittagswert gilt bis kurz vor der nächsten Zeile
    assert held[-1] == (60000.0, 800.0)


def test_hold_points_do_not_invent_a_bright_morning() -> None:
    held = dl.hold_points([(40000.0, 900.0)], day_start=0.0, until=40000.0, night_lx=5.0)
    assert held == [(40000.0, 900.0)]  # ein heller erster Wert sagt nichts über die Stunden davor
    assert dl.hold_points([], 0.0, 10.0, 5.0) == []


def test_crossings_interpolate_between_points() -> None:
    points = [(0.0, 0.0), (100.0, 1000.0), (200.0, 1000.0), (300.0, 0.0)]
    up, down = dl.crossings(points, 400.0)
    assert up == pytest.approx(40.0)
    assert down == pytest.approx(260.0)
    assert dl.crossings([(0.0, 1.0), (10.0, 2.0)], 400.0) == (None, None)


def test_bands_split_at_both_thresholds() -> None:
    bands = dl.bands_from_points([(0.0, 0.0), (100.0, 1000.0)], 5.0, 400.0)
    assert [b[2] for b in bands] == [dl.NIGHT, dl.DUSK, dl.DAY]
    assert bands[0][1] == pytest.approx(0.5) and bands[1][1] == pytest.approx(40.0)
    assert bands[0][1] == bands[1][0] and bands[2][1] == 100.0


def test_summarize_fine_reports_times_lengths_and_twilight() -> None:
    points = [(0.0, 0.0), (1000.0, 0.0), (2000.0, 1000.0), (5000.0, 1000.0), (6000.0, 0.0), (7000.0, 0.0)]
    result = dl.summarize_fine(points, 5.0, 400.0, until=7000.0, live=False)
    assert result["from"] == pytest.approx(1400.0) and result["to"] == pytest.approx(5600.0)
    assert result["length"] == pytest.approx(4200.0)
    assert result["morning"] == pytest.approx(1400.0 - result["dawn"])
    assert result["evening"] == pytest.approx(result["dusk"] - 5600.0)
    assert result["approx"] is False and not result["ongoing"]


def test_summarize_fine_of_a_running_day_has_no_end_yet() -> None:
    points = [(0.0, 0.0), (1000.0, 1000.0), (3000.0, 1000.0)]
    result = dl.summarize_fine(points, 5.0, 400.0, until=3000.0, live=True)
    assert result["ongoing"] and result["to"] is None
    assert result["length"] == pytest.approx(3000.0 - result["from"])
    assert dl.summarize_fine(points, 5.0, 400.0, until=3000.0, live=False)["ongoing"] is False


def test_summarize_fine_never_reaching_day_has_no_times() -> None:
    result = dl.summarize_fine([(0.0, 0.0), (100.0, 300.0), (200.0, 0.0)], 5.0, 400.0, until=200.0, live=False)
    assert result["from"] is None and result["length"] is None


def test_hour_classes_bridge_held_hours_and_leave_the_edges_open() -> None:
    hours = [3600.0 * i for i in range(8)]
    rows = {
        hours[1]: (0.0, 0.0), hours[2]: (100.0, 300.0), hours[3]: (1000.0, 1000.0),
        hours[6]: (1000.0, 1000.0), hours[7]: (0.0, 0.0),
    }
    classes = dl.hour_classes(hours, rows, 5.0, 400.0)
    assert classes == [None, dl.NIGHT, dl.DUSK, dl.DAY, dl.DAY, dl.DAY, dl.DAY, dl.NIGHT]
    assert dl.hour_classes(hours, {hours[0]: (0.0, 0.0), hours[2]: (1000.0, 1000.0)}, 5.0, 400.0)[1] == dl.DUSK


def test_summarize_hours_counts_whole_hours() -> None:
    hours = [3600.0 * i for i in range(6)]
    classes = [dl.NIGHT, dl.DUSK, dl.DAY, dl.DAY, dl.DUSK, dl.NIGHT]
    result = dl.summarize_hours(hours, classes)
    assert result["from"] == hours[2] and result["to"] == hours[3] + 3600.0
    assert result["length"] == 7200.0 and result["morning"] == 3600.0 and result["evening"] == 3600.0
    assert result["approx"] is True
    assert dl.summarize_hours(hours, [dl.NIGHT] * 6)["from"] is None


def test_compute_daylight_from_the_raw_values_of_one_day(service) -> None:
    _add(service, "sensor.t", "°C", [(_ts(19, 8), 10.0)])
    _add(service, "sensor.daemm", "lx", RAMP)
    _configure(service, temperature="sensor.t", illuminance_dawn="sensor.daemm")
    data = service.compute_daylight("2026-08-19", NOW)
    day = data["day"]
    assert day["resolution"] == "fine" and day["date"] == "2026-08-19"
    assert _ts(19, 6, 0) < day["from"] < _ts(19, 6, 30)
    assert _ts(19, 19, 30) < day["to"] < _ts(19, 20, 0)
    assert 13.0 * 3600 < day["length"] < 14.0 * 3600  # der gehaltene Mittag zählt mit
    assert day["morning"] > 0 and day["evening"] > 0
    assert {band[2] for band in day["bands"]} == {"night", "dusk", "day"}
    assert [row["date"] for row in data["compare"]][:2] == ["2026-08-19", "2026-08-18"]
    assert len(data["compare"]) == wetter_routes.DAYLIGHT_COMPARE_DAYS
    assert data["compare"][1]["resolution"] is None  # kein Wert, keine erfundene Länge


def test_compute_daylight_without_role_or_entity_is_none(service) -> None:
    _configure(service, temperature="sensor.t")
    assert service.compute_daylight(None, NOW) is None
    _configure(service, temperature="sensor.t", illuminance_dawn="sensor.fehlt")
    assert service.compute_daylight(None, NOW) is None


def test_compute_daylight_falls_back_to_hourly_rows(service, monkeypatch) -> None:
    _add(service, "sensor.daemm", "lx", [(_ts(19, 12), 1000.0)])
    _configure(service, temperature="sensor.t", illuminance_dawn="sensor.daemm")
    monkeypatch.setattr(wetter_routes.query_mod, "_query_live_fine", lambda *args, **kwargs: [])
    mean = {7: 40.0, 8: 900.0, 9: 1000.0, 12: 1000.0, 16: 700.0, 17: 20.0, 18: 0.0}
    rows = [
        rollup_mod.FineRow(bucket_start=_ts(19, hour), value=value, max_value=min(1000.0, value * 1.2))
        for hour, value in mean.items()
    ]
    monkeypatch.setattr(service, "_rows", lambda *args, **kwargs: rows)
    day = service.compute_daylight("2026-08-19", NOW)["day"]
    assert day["resolution"] == "hour" and day["approx"] is True
    assert day["from"] == _ts(19, 8) and day["to"] == _ts(19, 17)
    assert day["length"] == 9 * 3600.0  # 10 und 11 Uhr ohne Zeile: davor und danach Tag
    assert day["hours"][0][0] == round(_ts(19, 7))


def test_daylight_thresholds_round_trip_and_bounds(service) -> None:
    index = service.deps.index
    config = wetter_routes._load_config(index)
    assert (config["daylight_night_lx"], config["daylight_day_lx"]) == (5.0, 400.0)
    _configure(service, temperature="sensor.t", daylight_night_lx=10.0, daylight_day_lx=300.0)
    config = wetter_routes._load_config(index)
    assert (config["daylight_night_lx"], config["daylight_day_lx"]) == (10.0, 300.0)
    _configure(service, temperature="sensor.t", daylight_night_lx=500.0, daylight_day_lx=300.0)  # Nacht über Tag: Standard
    config = wetter_routes._load_config(index)
    assert (config["daylight_night_lx"], config["daylight_day_lx"]) == (5.0, 400.0)


def test_daylight_setup_through_the_app(client) -> None:
    from app.main import index

    index.set_setting(wetter_routes.SETTING_CONFIG, "")
    index.get_or_create_entity("sensor.licht_test_temp", "sensor", "measurement", "°C")
    index.get_or_create_entity("sensor.licht_test_dawn", "sensor", "measurement", "lx")
    form = client.get("/wetterdashboard/setup").text
    assert 'name="daylight_night_lx"' in form and 'name="daylight_day_lx"' in form and 'data-role="illuminance_dawn"' in form
    base = {"temperature": "sensor.licht_test_temp", "illuminance_dawn": "sensor.licht_test_dawn"}
    assert client.post("/wetterdashboard/setup", data={**base, "daylight_night_lx": "8", "daylight_day_lx": "350"}).status_code == 200
    config = wetter_routes._load_config(index)
    assert config["illuminance_dawn"] == "sensor.licht_test_dawn"
    assert (config["daylight_night_lx"], config["daylight_day_lx"]) == (8.0, 350.0)
    assert client.get("/wetterdashboard").text.count('x-ref="daylightChart"') == 1
    for night, day in (("abc", "400"), ("0", "400"), ("5", "5000"), ("300", "100")):
        response = client.post("/wetterdashboard/setup", data={**base, "daylight_night_lx": night, "daylight_day_lx": day})
        assert response.status_code == 400, (night, day)
    assert client.post("/wetterdashboard/setup", data=base).status_code == 200
    assert wetter_routes._load_config(index)["daylight_night_lx"] == 5.0
    # Ohne Rolle gibt es weder Karte noch Daten.
    assert client.post("/wetterdashboard/setup", data={"temperature": "sensor.licht_test_temp"}).status_code == 200
    assert 'x-ref="daylightChart"' not in client.get("/wetterdashboard").text
    assert client.get("/wetterdashboard/tageslicht").status_code == 409
    index.set_setting(wetter_routes.SETTING_CONFIG, "")


def test_autofill_assigns_the_dawn_sensor_to_its_own_role() -> None:
    def entity(entity_id: str) -> dict:
        return {
            "entity_id": entity_id, "domain": "sensor", "aggregation_type": "standard", "unit": "lx",
            "state_class": "measurement", "friendly_name": None, "custom_name": None, "points": 100,
        }

    archived = [entity(f"sensor.station_illuminance_{name}") for name in ("dawn", "central", "east")]
    labels = {row["entity_id"]: row["entity_id"] for row in archived}
    roles = wetter_autofill.suggest(archived, labels, None)["roles"]
    assert roles["illuminance_dawn"]["entity_id"] == "sensor.station_illuminance_dawn"
    assert roles["illuminance"]["entity_id"] != "sensor.station_illuminance_dawn"
    assert all(c["entity_id"] != "sensor.station_illuminance_dawn" for c in roles["illuminance"]["candidates"])


def test_daylight_chart_exists_in_the_script() -> None:
    from _paths import APP

    script = (APP / "static/js/wetter.js").read_text(encoding="utf-8")
    assert "wetterdashboard/tageslicht" in script and "drawDaylight" in script and "markArea" in script
    assert "'dashboard-stichtag'" in script and "this.hasDaylight" in script


def _hourly_day(day: int, bright_from: int, bright_to: int) -> list[tuple[float, float]]:
    """Stundenwerte eines Tages: hell (1000 lx) von ``bright_from`` bis ``bright_to`` (einschließlich), sonst 0."""
    return [(_ts(day, hour, 30), 1000.0 if bright_from <= hour <= bright_to else 0.0) for hour in range(24)]


def test_daylength_interpolates_the_threshold_between_two_hours(service) -> None:
    _add(service, "sensor.daemm", "lx", _hourly_day(17, 7, 17) + _hourly_day(18, 7, 18) + _hourly_day(19, 7, 19))
    _configure(service, temperature="sensor.t", illuminance_dawn="sensor.daemm")
    with service.deps.index._conn:  # noqa: SLF001 — die Testdaten sind absteigend geschrieben
        service.deps.index._conn.execute("UPDATE entities SET first_ts = ? WHERE entity_id = 'sensor.daemm'", (_ts(17, 0, 30),))
    data = service.compute_daylength(NOW)
    # heute zählt nicht; statt voller Stunden (11, 12, 13) liegt der Übergang zwischen zwei Stundenwerten
    assert data["days"] == [["2026-08-17", 11.2], ["2026-08-18", 12.2], ["2026-08-19", 13.2]]


def test_daylength_falls_back_to_bright_hours_without_a_crossing(service) -> None:
    """Beginnt der Sensor mitten am hellen Tag, fehlt der Anstieg: dann zählen die hellen Stunden."""
    _add(service, "sensor.daemm", "lx", _hourly_day(19, 0, 17))  # hell von Mitternacht an, dunkel ab 18 Uhr
    _configure(service, temperature="sensor.t", illuminance_dawn="sensor.daemm")
    with service.deps.index._conn:  # noqa: SLF001
        service.deps.index._conn.execute("UPDATE entities SET first_ts = ? WHERE entity_id = 'sensor.daemm'", (_ts(19, 0, 30),))
    assert service.compute_daylength(NOW)["days"] == [["2026-08-19", 18.0]]


def test_daylength_skips_days_with_too_few_values_and_needs_the_role(service) -> None:
    _add(service, "sensor.daemm", "lx", [(_ts(19, hour, 30), 1000.0) for hour in range(9, 13)])  # nur vier Stunden
    _configure(service, temperature="sensor.t", illuminance_dawn="sensor.daemm")
    assert service.compute_daylength(NOW) is None
    _configure(service, temperature="sensor.t")
    assert service.compute_daylength(NOW) is None


def test_daylight_compare_adds_the_sun_elevation_at_start_and_end(service) -> None:
    _add(service, "sensor.t", "°C", [(_ts(19, 8), 10.0)])
    _add(service, "sensor.daemm", "lx", RAMP)
    # Sonnenhöhe: linear von −6° (5:30) auf 40° (12:30) und wieder auf −6° (19:30), Stundenmittel
    elevation = [(_ts(19, h, 30), -6.0 + (46.0 / 7.0) * min(h - 5, 19 - h)) for h in range(5, 22)]
    _add(service, "sensor.hoehe", "°", elevation)
    _configure(service, temperature="sensor.t", illuminance_dawn="sensor.daemm", sun_elevation="sensor.hoehe")
    data = service.compute_daylight("2026-08-19", NOW)
    assert data["has_elevation"] is True
    row = data["compare"][0]
    assert row["sun_from"] is not None and -6.0 < row["sun_from"] < 12.0 and row["sun_to"] is not None
    assert data["compare"][1]["sun_from"] is None  # Tag ohne Werte: keine Höhe
    _configure(service, temperature="sensor.t", illuminance_dawn="sensor.daemm")
    assert service.compute_daylight("2026-08-19", NOW)["has_elevation"] is False
