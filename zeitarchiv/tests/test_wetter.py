"""Wetterdashboard (ROADMAP 1.43, Phase 1): Konfiguration, Zeitfenster, Kennzahlen, Tabelle."""

from __future__ import annotations

import json
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app import wetter_routes
from app.storage import hotbuffer
from app.storage import rollup as rollup_mod
from app.storage.index import Index
from app.wetter_routes import TableRequest, WetterDependencies, WetterService

TZ = ZoneInfo("Europe/Berlin")
NOW = datetime(2026, 8, 20, 12, 0, tzinfo=TZ)


def _ts(day: int, hour: int, minute: int = 0, month: int = 8) -> float:
    return datetime(2026, month, day, hour, minute, tzinfo=TZ).timestamp()


@pytest.fixture()
def service():
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-wetter-test-"))
    index = Index(tmp / "index.sqlite")
    yield WetterService(WetterDependencies(
        data_dir=tmp, index=index, tz=TZ, templates=None, app_root_context=lambda request: {},
    ))
    index.close()
    shutil.rmtree(tmp, ignore_errors=True)


def _add(service, entity_id, domain, state_class, unit, points):
    deps = service.deps
    deps.index.get_or_create_entity(entity_id, domain, state_class, unit)
    for ts, value in points:
        hotbuffer.append(deps.data_dir, entity_id, ts, value, TZ)
        deps.index.record_write(entity_id, ts, value)


def _configure(service, **roles):
    wetter_routes._save_config(service.deps.index, {**wetter_routes._empty_config(), **roles})


def test_config_round_trip_and_defaults(service) -> None:
    index = service.deps.index
    assert wetter_routes._load_config(index) == wetter_routes._empty_config()
    assert not wetter_routes.is_wetter_configured(index)
    _configure(service, temperature="sensor.t", extra_temperatures=[{"entity_id": "sensor.n", "name": "Nord"}])
    config = wetter_routes._load_config(index)
    assert config["temperature"] == "sensor.t"
    assert config["extra_temperatures"] == [{"entity_id": "sensor.n", "name": "Nord"}]
    assert json.loads(index.get_setting(wetter_routes.SETTING_CONFIG))["schema_version"] == 1
    assert wetter_routes.wetter_role_count(index) == 2


def test_broken_config_falls_back_to_empty(service) -> None:
    service.deps.index.set_setting(wetter_routes.SETTING_CONFIG, "{kaputt")
    assert wetter_routes._load_config(service.deps.index) == wetter_routes._empty_config()


def test_role_warning_checks_the_aggregation_type(service) -> None:
    _add(service, "sensor.t", "sensor", "measurement", "°C", [(_ts(20, 1), 12.0)])
    index = service.deps.index
    assert wetter_routes._role_warning(index, "temperature", "sensor.t") is None
    assert wetter_routes._role_warning(index, "precip_mm", "sensor.t")  # Messwert ist kein Zähler
    assert wetter_routes._role_warning(index, "temperature", "sensor.fehlt")
    assert wetter_routes._role_warning(index, "temperature", "") is None


def test_windows_bis_jetzt_cuts_the_previous_year_at_the_same_point(service) -> None:
    win = service.column_windows("monat", NOW, "bis_jetzt")
    assert win["partial"]
    assert win["start"] == datetime(2026, 8, 1, tzinfo=TZ) and win["end"] == NOW
    assert win["vj_start"] == datetime(2025, 8, 1, tzinfo=TZ)
    assert win["vj_end"] == datetime(2025, 8, 20, 12, tzinfo=TZ)


def test_windows_komplett_takes_the_whole_previous_year_period(service) -> None:
    win = service.column_windows("monat", NOW, "komplett")
    assert win["vj_end"] == datetime(2025, 9, 1, tzinfo=TZ)
    year = service.column_windows("jahr", NOW, "komplett")
    assert year["vj_start"] == datetime(2025, 1, 1, tzinfo=TZ) and year["vj_end"] == datetime(2026, 1, 1, tzinfo=TZ)


@pytest.mark.parametrize("mode", ["bis_jetzt", "komplett"])
def test_completed_periods_are_identical_in_both_modes(service, mode) -> None:
    yesterday = service.column_windows("gestern", NOW, mode)
    assert not yesterday["partial"]
    assert yesterday["start"] == datetime(2026, 8, 19, tzinfo=TZ) and yesterday["end"] == datetime(2026, 8, 20, tzinfo=TZ)
    assert yesterday["vj_end"] == datetime(2025, 8, 20, tzinfo=TZ)
    last_month = service.column_windows("vormonat", NOW, mode)
    assert last_month["start"] == datetime(2026, 7, 1, tzinfo=TZ) and last_month["end"] == datetime(2026, 8, 1, tzinfo=TZ)


def test_last_month_in_january_is_december_of_the_previous_year(service) -> None:
    win = service.column_windows("vormonat", datetime(2026, 1, 15, 9, tzinfo=TZ), "bis_jetzt")
    assert win["start"] == datetime(2025, 12, 1, tzinfo=TZ) and win["end"] == datetime(2026, 1, 1, tzinfo=TZ)
    assert win["vj_start"] == datetime(2024, 12, 1, tzinfo=TZ)


def test_leap_day_falls_back_to_the_28th_in_the_previous_year(service) -> None:
    win = service.column_windows("heute", datetime(2028, 2, 29, 10, tzinfo=TZ), "bis_jetzt")
    assert win["vj_start"] == datetime(2027, 2, 28, tzinfo=TZ)


def test_summarize_standard_has_dates_and_no_zero_for_missing(service) -> None:
    config = wetter_routes._empty_config()
    rows = [
        rollup_mod.FineRow(bucket_start=_ts(20, 1), value=12.0, min_value=11.0, max_value=13.0),
        rollup_mod.FineRow(bucket_start=_ts(20, 10), value=18.0, min_value=16.0, max_value=19.5),
    ]
    summary = service.summarize("standard", rows, config)
    assert summary == {"avg": 15.0, "max": 19.5, "max_ts": _ts(20, 10), "min": 11.0, "min_ts": _ts(20, 1)}
    assert service.summarize("standard", [], config) is None
    assert service.summarize("counter", [], config) is None
    assert service.summarize("switch", [], config) is None


def test_summarize_tie_takes_the_first_occurrence(service) -> None:
    rows = [
        rollup_mod.FineRow(bucket_start=_ts(20, 9), value=5.0, min_value=5.0, max_value=7.0),
        rollup_mod.FineRow(bucket_start=_ts(20, 3), value=5.0, min_value=5.0, max_value=7.0),
    ]
    summary = service.summarize("standard", rows, wetter_routes._empty_config())
    assert summary["max_ts"] == _ts(20, 3) and summary["min_ts"] == _ts(20, 3)


def test_summarize_counter_counts_rain_days_by_threshold(service) -> None:
    config = {**wetter_routes._empty_config(), "rain_day_mm": 1.0}
    rows = [
        rollup_mod.FineRow(bucket_start=_ts(18, 0), value=0.4),
        rollup_mod.FineRow(bucket_start=_ts(19, 0), value=3.0),
        rollup_mod.FineRow(bucket_start=_ts(20, 0), value=1.0),
    ]
    summary = service.summarize("counter", rows, config)
    assert summary["sum"] == pytest.approx(4.4)
    assert summary["days"] == 2
    assert summary["top"] == 3.0 and summary["top_ts"] == _ts(19, 0)


def test_summarize_switch_groups_hours_into_local_days(service) -> None:
    config = {**wetter_routes._empty_config(), "rain_day_minutes": 10}
    rows = [
        rollup_mod.FineRow(bucket_start=_ts(19, 6), on_seconds=300.0),
        rollup_mod.FineRow(bucket_start=_ts(19, 7), on_seconds=600.0),
        rollup_mod.FineRow(bucket_start=_ts(20, 5), on_seconds=120.0),
    ]
    summary = service.summarize("switch", rows, config)
    assert summary["minutes"] == pytest.approx(17.0)
    assert summary["days"] == 1  # nur der 19. erreicht 15 min >= 10
    assert summary["top"] == pytest.approx(15.0) and summary["top_ts"] == _ts(19, 6)


def test_table_computes_today_and_yesterday_from_the_hot_buffer(service) -> None:
    _add(service, "sensor.t", "sensor", "measurement", "°C", [
        (_ts(19, 8), 10.0), (_ts(19, 14), 20.0), (_ts(20, 1), 12.0), (_ts(20, 10), 18.0),
    ])
    _configure(service, temperature="sensor.t")
    data = service.compute_table(TableRequest(columns=["heute", "gestern"], vorjahr=True), NOW)
    assert [col["key"] for col in data["columns"]] == ["heute", "gestern"]
    row = data["rows"][0]
    assert row["key"] == "temperature" and row["kind"] == "standard" and row["unit"] == "°C"
    today = row["cells"]["heute"]["cur"]
    assert (today["max"], today["min"], today["avg"]) == (18.0, 12.0, 15.0)
    assert today["max_ts"] == _ts(20, 10) and today["min_ts"] == _ts(20, 1)
    yesterday = row["cells"]["gestern"]["cur"]
    assert (yesterday["max"], yesterday["min"]) == (20.0, 10.0)
    # Das Archiv beginnt erst heute: das Vorjahr ist leer und bleibt "fehlend", nicht 0.
    assert row["cells"]["heute"]["vj"] is None


def test_table_without_vorjahr_has_no_vj_side(service) -> None:
    _add(service, "sensor.t", "sensor", "measurement", "°C", [(_ts(20, 1), 12.0)])
    _configure(service, temperature="sensor.t")
    data = service.compute_table(TableRequest(columns=["heute"], vorjahr=False), NOW)
    assert "vj" not in data["rows"][0]["cells"]["heute"]


def test_table_leaves_out_the_boundary_point_in_the_average(service) -> None:
    # Der Wert von gestern 23:00 gilt ab Mitternacht weiter; query_series() nähme ihn als Randpunkt
    # in Ø/Min auf. Die Wettertabelle rechnet nur über Werte, die im Fenster liegen.
    _add(service, "sensor.t", "sensor", "measurement", "°C", [(_ts(19, 23), 2.0), (_ts(20, 1), 12.0)])
    _configure(service, temperature="sensor.t")
    today = service.compute_table(TableRequest(columns=["heute"], vorjahr=False), NOW)["rows"][0]["cells"]["heute"]["cur"]
    assert (today["min"], today["avg"]) == (12.0, 12.0)


def test_table_counter_and_switch_rows(service) -> None:
    _add(service, "sensor.regen", "sensor", "total_increasing", "mm", [
        (_ts(19, 0), 100.0), (_ts(19, 12), 102.0), (_ts(20, 6), 103.5),
    ])
    _add(service, "binary_sensor.regen", "binary_sensor", None, "", [
        (_ts(20, 6), 1.0), (_ts(20, 6, 30), 0.0),
    ])
    _add(service, "sensor.t", "sensor", "measurement", "°C", [(_ts(20, 1), 12.0)])
    _configure(service, temperature="sensor.t", precip_mm="sensor.regen", precip_dur="binary_sensor.regen")
    data = service.compute_table(TableRequest(columns=["heute"], vorjahr=False), NOW)
    rows = {row["key"]: row for row in data["rows"]}
    assert list(rows) == ["temperature", "precip_mm", "precip_dur"]
    assert rows["precip_mm"]["cells"]["heute"]["cur"]["sum"] == pytest.approx(1.5)
    assert rows["precip_dur"]["cells"]["heute"]["cur"]["minutes"] == pytest.approx(30.0)
    assert rows["precip_dur"]["cells"]["heute"]["cur"]["days"] == 1


def test_rows_skip_roles_whose_type_does_not_fit(service) -> None:
    _add(service, "sensor.t", "sensor", "measurement", "°C", [(_ts(20, 1), 12.0)])
    _configure(service, temperature="sensor.t", precip_mm="sensor.t")  # Messwert als Zähler zugeordnet
    keys = [spec["key"] for spec in service._row_specs(wetter_routes._load_config(service.deps.index))]
    assert keys == ["temperature"]


def test_extra_thermometer_becomes_its_own_row(service) -> None:
    _add(service, "sensor.t", "sensor", "measurement", "°C", [(_ts(20, 1), 12.0)])
    _add(service, "sensor.n", "sensor", "measurement", "°C", [(_ts(20, 1), 9.0)])
    _configure(service, temperature="sensor.t", extra_temperatures=[{"entity_id": "sensor.n", "name": "Nord"}])
    data = service.compute_table(TableRequest(columns=["heute"], vorjahr=False), NOW)
    assert [(row["key"], row["label"]) for row in data["rows"]] == [("temperature", "Außentemperatur"), ("temperature:0", "Nord")]


def test_tiles_show_today_and_skip_extra_thermometers(service) -> None:
    _add(service, "sensor.t", "sensor", "measurement", "°C", [(_ts(20, 1), 12.0), (_ts(20, 10), 18.0)])
    _add(service, "sensor.n", "sensor", "measurement", "°C", [(_ts(20, 1), 9.0)])
    _configure(service, temperature="sensor.t", extra_temperatures=[{"entity_id": "sensor.n", "name": "Nord"}])
    tiles = service.compute_tiles(NOW)
    assert [tile["key"] for tile in tiles] == ["temperature"]
    assert tiles[0]["last"] == 18.0 and tiles[0]["today"]["min"] == 12.0 and tiles[0]["yesterday"] is None


# -- Routen ------------------------------------------------------------------------------------


def test_routes_page_setup_and_table_through_the_app(client) -> None:
    from app.main import index

    index.set_setting(wetter_routes.SETTING_CONFIG, "")
    index.set_setting(wetter_routes.SETTING_ENABLED, "0")
    page = client.get("/wetterdashboard")
    assert page.status_code == 200 and "Rollen zuordnen" in page.text
    assert client.post("/wetterdashboard/table", json={}).status_code == 409
    assert client.get("/wetterdashboard/tiles").status_code == 409
    assert "/wetterdashboard" not in client.get("/dashboards").text  # weder Menüeintrag noch Kachel-Link

    index.get_or_create_entity("sensor.wetter_test_temp", "sensor", "measurement", "°C")
    index.get_or_create_entity("sensor.wetter_test_regen", "sensor", "total_increasing", "mm")
    form = client.get("/wetterdashboard/setup")
    assert form.status_code == 200 and "Außentemperatur" in form.text

    rejected = client.post("/wetterdashboard/setup", data={"temperature": "sensor.wetter_test_regen"})
    assert rejected.status_code == 400  # Zähler als Außentemperatur
    assert client.post("/wetterdashboard/setup", data={"temperature": "sensor.gibt_es_nicht"}).status_code == 400

    saved = client.post("/wetterdashboard/setup", data={
        "temperature": "sensor.wetter_test_temp", "precip_mm": "sensor.wetter_test_regen",
        "rain_day_mm": "0,5", "rain_day_minutes": "15",
    })
    assert saved.status_code == 200 and "wetterDashboard()" in saved.text
    config = wetter_routes._load_config(index)
    assert config["rain_day_mm"] == 0.5 and config["rain_day_minutes"] == 15

    table = client.post("/wetterdashboard/table", json={"columns": ["heute", "jahr"], "vorjahr": True, "mode": "komplett"})
    assert table.status_code == 200
    body = table.json()
    assert [col["key"] for col in body["columns"]] == ["heute", "jahr"] and body["mode"] == "komplett"
    assert [row["key"] for row in body["rows"]] == ["temperature", "precip_mm"]
    assert client.get("/wetterdashboard/tiles").status_code == 200

    assert client.post("/wetterdashboard/enable").json() == {"enabled": True}
    dashboards = client.get("/dashboards").text
    assert 'href="wetterdashboard"' in dashboards and "/wetterdashboard" in dashboards  # Kachel und Menü
    assert client.post("/wetterdashboard/disable").json() == {"enabled": False}
