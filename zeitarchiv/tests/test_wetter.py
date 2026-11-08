"""Wetterdashboard (ROADMAP 1.43, Phase 1): Konfiguration, Zeitfenster, Kennzahlen, Tabelle."""

from __future__ import annotations

import json
import shutil
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app import wetter_analysis as analysis_mod
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



def _wetter_script() -> str:
    """wetter.js samt element-table-render.js: die Vergleichstabelle wurde dorthin herausgelöst (Roadmap 1.48), damit
    dieselbe Zeichnung in der Lovelace-Karte läuft. Die Prüfungen gelten dem Zusammenspiel beider."""
    from _paths import APP

    return "\n".join((APP / "static/js" / name).read_text(encoding="utf-8") for name in ("wetter.js", "element-table-render.js"))


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
    assert list(rows) == ["temperature", "kenntage", "precip_mm", "precip_dur"]
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
    assert [(row["key"], row["label"]) for row in data["rows"]] == [("temperature", "Temperatur außen"), ("kenntage", "Kenntage"), ("temperature:0", "Nord")]


def test_tiles_show_today_and_include_extra_sensors(service) -> None:
    _add(service, "sensor.t", "sensor", "measurement", "°C", [(_ts(20, 1), 12.0), (_ts(20, 10), 18.0)])
    _add(service, "sensor.n", "sensor", "measurement", "°C", [(_ts(20, 1), 9.0)])
    _configure(service, temperature="sensor.t", extra_temperatures=[{"entity_id": "sensor.n", "name": "Nord"}])
    tiles = service.compute_tiles(NOW)
    assert [tile["key"] for tile in tiles] == ["temperature", "temperature:0"]
    assert tiles[1]["label"] == "Nord" and tiles[1]["today"]["min"] == 9.0
    assert tiles[0]["last"] == 18.0 and tiles[0]["today"]["min"] == 12.0 and tiles[0]["yesterday"] is None


def test_temperature_comparison_has_a_series_per_sensor_and_leaves_thin_days_empty(service) -> None:
    hours = [(_ts(19, hour), 10.0 + hour * 0.1) for hour in range(24)]
    _add(service, "sensor.t", "sensor", "measurement", "°C", hours + [(_ts(18, 1), 5.0)])
    _add(service, "sensor.n", "sensor", "measurement", "°C", [(ts, 8.0) for ts, _v in hours])
    _configure(service, temperature="sensor.t", extra_temperatures=[{"entity_id": "sensor.n", "name": "Nord"}])
    data = service.compute_temperatures(7, NOW)
    assert data["dates"][-1] == "2026-08-20" and len(data["dates"]) == 7
    assert [row["label"] for row in data["series"]] == ["Temperatur", "Nord"]
    day = data["dates"].index("2026-08-19")
    assert data["series"][1]["values"][day] == 8.0
    assert data["series"][0]["values"][day] is not None
    assert data["series"][0]["values"][day - 1] is None  # ein einzelner Wert reicht nicht für ein Tagesmittel


def test_temperature_comparison_route_validates_the_period(client) -> None:
    from app.main import index

    index.get_or_create_entity("sensor.wetter_test_temp9", "sensor", "measurement", "°C")
    index.set_setting(wetter_routes.SETTING_ENABLED, "1")
    wetter_routes._save_config(index, {**wetter_routes._empty_config(), "temperature": "sensor.wetter_test_temp9"})  # noqa: SLF001
    assert client.get("/wetterdashboard/temperaturen?days=5").status_code == 400
    result = client.get("/wetterdashboard/temperaturen?days=7")
    assert result.status_code == 200 and len(result.json()["dates"]) == 7
    index.set_setting(wetter_routes.SETTING_CONFIG, "")


def test_long_term_tab_has_the_year_by_month_temperature_card(client) -> None:
    from app.main import index

    index.get_or_create_entity("sensor.wetter_test_temp10", "sensor", "measurement", "°C")
    index.set_setting(wetter_routes.SETTING_ENABLED, "1")
    wetter_routes._save_config(index, {**wetter_routes._empty_config(), "temperature": "sensor.wetter_test_temp10"})  # noqa: SLF001
    html = client.get("/wetterdashboard").text
    assert "tempYearsTable" in html
    assert client.get("/wetterdashboard/monatsvergleich?metric=temperature").status_code == 200  # Datenquelle der Karte
    # Ohne Niederschlag und Helligkeit gibt es nichts umzuschalten.
    assert "setTempYearsMetric(" not in html
    index.set_setting(wetter_routes.SETTING_CONFIG, "")


def test_year_by_month_card_offers_precipitation_and_sun_when_assigned(client) -> None:
    from app.main import index

    index.get_or_create_entity("sensor.wetter_test_temp11", "sensor", "measurement", "°C")
    index.get_or_create_entity("sensor.wetter_test_lux11", "sensor", "measurement", "lx")
    index.set_setting(wetter_routes.SETTING_ENABLED, "1")
    wetter_routes._save_config(  # noqa: SLF001
        index, {**wetter_routes._empty_config(), "temperature": "sensor.wetter_test_temp11", "illuminance": "sensor.wetter_test_lux11"},
    )
    html = client.get("/wetterdashboard").text
    assert "setTempYearsMetric('temperature')" in html
    assert "setTempYearsMetric('sun')" in html
    assert "setTempYearsMetric('precip')" not in html
    index.set_setting(wetter_routes.SETTING_CONFIG, "")


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
    assert saved.status_code == 200 and "wetterDashboard([" in saved.text
    config = wetter_routes._load_config(index)
    assert config["rain_day_mm"] == 0.5 and config["rain_day_minutes"] == 15

    table = client.post("/wetterdashboard/table", json={"columns": ["heute", "jahr"], "vorjahr": True, "mode": "komplett"})
    assert table.status_code == 200
    body = table.json()
    assert [col["key"] for col in body["columns"]] == ["heute", "jahr"] and body["mode"] == "komplett"
    assert [row["key"] for row in body["rows"]] == ["temperature", "kenntage", "precip_mm"]
    assert client.get("/wetterdashboard/tiles").status_code == 200

    assert client.post("/wetterdashboard/enable").json() == {"enabled": True}
    dashboards = client.get("/dashboards").text
    assert 'href="wetterdashboard"' in dashboards and "/wetterdashboard" in dashboards  # Kachel und Menü
    assert client.post("/wetterdashboard/disable").json() == {"enabled": False}


# -- Phase 2: Auswertungen über viele Jahre ----------------------------------------------------


def _rain_setup(service):
    _add(service, "sensor.regen", "sensor", "total_increasing", "mm", [
        (_ts(18, 0), 100.0), (_ts(19, 12), 102.0), (_ts(20, 6), 103.5),
    ])
    _add(service, "binary_sensor.regen", "binary_sensor", None, "", [(_ts(20, 6), 1.0), (_ts(20, 6, 30), 0.0)])
    _add(service, "sensor.t", "sensor", "measurement", "°C", [(_ts(19, 8), 10.0), (_ts(20, 1), 12.0)])


def test_precip_series_for_the_counter_and_the_rain_sensor(service) -> None:
    _rain_setup(service)
    _configure(service, temperature="sensor.t", precip_mm="sensor.regen", precip_dur="binary_sensor.regen")
    amount = service.compute_precip("mm", NOW)
    assert amount["mode"] == "mm" and amount["unit"] == "mm" and amount["available"] == ["mm", "dur"]
    assert amount["total"] == pytest.approx(3.5) and amount["year"] == 2026
    assert amount["wettest"]["date"] == "2026-08-19"
    duration = service.compute_precip("dur", NOW)
    assert duration["mode"] == "dur" and duration["unit"] == "min" and duration["total"] == pytest.approx(30.0)
    assert duration["threshold"] == 10.0


def test_precip_falls_back_to_the_other_kind_and_is_none_without_roles(service) -> None:
    _rain_setup(service)
    _configure(service, temperature="sensor.t", precip_dur="binary_sensor.regen")
    assert service.compute_precip("mm", NOW)["mode"] == "dur"  # nur Regensensor vorhanden
    _configure(service, temperature="sensor.t")
    assert service.compute_precip("mm", NOW) is None
    assert service.compute_monthly("precip", "mm", NOW) is None


def test_monthly_and_longterm_have_the_expected_shape(service) -> None:
    _rain_setup(service)
    _configure(service, temperature="sensor.t", precip_mm="sensor.regen")
    monthly = service.compute_monthly("temperature", now_local=NOW)
    assert monthly["metric"] == "temperature" and monthly["current_year"] == 2026 and monthly["headline"] is None
    # Zwei Messwerte sind kein abgedeckter Tag (18 Stunden nötig): kein Monatsmittel aus Lückentagen.
    assert len(monthly["avg"]) == 12 and monthly["years"] == {}
    rain = service.compute_monthly("precip", "mm", NOW)
    assert rain["unit"] == "mm" and rain["years"]["2026"][7] == pytest.approx(3.5)
    longterm = service.compute_longterm(NOW)
    assert longterm["records"] == [] and longterm["records_days"] == 0  # ein paar Tage sind kein Archiv
    assert longterm["kenntage"] == {} and longterm["current_year"] == 2026  # ohne abgedeckte Tage keine Kenntage


def test_kenntage_rows_in_the_table_for_months_only(service) -> None:
    _add(service, "sensor.t", "sensor", "measurement", "°C", [(_ts(19, h), -2.0 if h < 12 else 6.0) for h in range(0, 24, 3)])
    _configure(service, temperature="sensor.t")
    data = service.compute_table(TableRequest(columns=["heute", "monat"], vorjahr=False), NOW)
    row = next(row for row in data["rows"] if row["key"] == "kenntage")
    assert row["cells"]["heute"] == {}  # an einem einzelnen Tag keine Kennzahl
    assert row["cells"]["monat"]["cur"] is None or row["cells"]["monat"]["cur"]["frost"] >= 0


def test_phase_two_routes_through_the_app(client) -> None:
    from app.main import index

    index.get_or_create_entity("sensor.wetter_test_temp2", "sensor", "measurement", "°C")
    index.set_setting(wetter_routes.SETTING_CONFIG, "")
    for path in ("niederschlag", "monatsvergleich", "langzeit"):
        assert client.get(f"/wetterdashboard/{path}").status_code == 409  # noch nicht eingerichtet
    _save = {**wetter_routes._empty_config(), "temperature": "sensor.wetter_test_temp2"}
    wetter_routes._save_config(index, _save)
    assert client.get("/wetterdashboard/niederschlag").status_code == 404  # keine Niederschlagsrolle
    assert client.get("/wetterdashboard/monatsvergleich?metric=precip").status_code == 404
    assert client.get("/wetterdashboard/monatsvergleich?metric=unsinn").status_code == 400
    assert client.get("/wetterdashboard/monatsvergleich?metric=temperature").status_code == 200
    assert client.get("/wetterdashboard/langzeit").status_code == 200
    index.set_setting(wetter_routes.SETTING_CONFIG, "")


def test_matrix_for_temperature_and_rain(service) -> None:
    _rain_setup(service)
    _configure(service, temperature="sensor.t", precip_mm="sensor.regen", precip_dur="binary_sensor.regen")
    rain = service.compute_matrix("precip", "mm", NOW)
    assert rain["years"] == [2026] and rain["unit"] == "mm" and rain["entity_id"] == "sensor.regen"
    assert any(cell[2] == pytest.approx(2.0) for cell in rain["cells"])
    minutes = service.compute_matrix("precip", "dur", NOW)
    # Der erste Wechsel war heute: vor dem Archiv und heute selbst bleibt die Matrix leer.
    assert minutes["unit"] == "min" and minutes["cells"] == [] and minutes["missing"] == []
    temperature = service.compute_matrix("avg", now_local=NOW)
    assert temperature["unit"] == "°C" and temperature["cells"] == []  # zu wenige Stunden je Tag: keine abgedeckten Tage
    _configure(service, temperature="sensor.t")
    assert service.compute_matrix("precip", "mm", NOW) is None


def test_matrix_route_validates_the_metric(client) -> None:
    from app.main import index

    index.get_or_create_entity("sensor.wetter_test_temp3", "sensor", "measurement", "°C")
    wetter_routes._save_config(index, {**wetter_routes._empty_config(), "temperature": "sensor.wetter_test_temp3"})
    assert client.get("/wetterdashboard/matrix?metric=unsinn").status_code == 400
    assert client.get("/wetterdashboard/matrix?metric=precip").status_code == 404
    answer = client.get("/wetterdashboard/matrix?metric=max")
    assert answer.status_code == 200 and answer.json()["years"] == []
    index.set_setting(wetter_routes.SETTING_CONFIG, "")


# -- Phase 3: Heizen ---------------------------------------------------------------------------


def test_heating_config_round_trip_and_defaults(service) -> None:
    index = service.deps.index
    config = wetter_routes._load_config(index)
    assert config["heating"] == [] and config["heating_room"] == 20.0 and config["heating_limit"] == 15.0
    _configure(service, temperature="sensor.t", heating=[{"entity_id": "sensor.wp", "name": "WP"}], heating_room=21.0, heating_limit=14.0)
    again = wetter_routes._load_config(index)
    assert again["heating"] == [{"entity_id": "sensor.wp", "name": "WP"}] and again["heating_room"] == 21.0
    assert wetter_routes.wetter_role_count(index) == 2  # Temperatur und ein Heizzähler
    index.set_setting(wetter_routes.SETTING_CONFIG, json.dumps({"temperature": "x", "heating_room": 99, "heating_limit": "kalt"}))
    assert wetter_routes._load_config(index)["heating_room"] == 20.0  # unplausible Werte fallen auf den Standard zurück


def test_heating_counters_ignore_non_counters_and_convert_units(service) -> None:
    _add(service, "sensor.wp", "sensor", "total_increasing", "Wh", [(_ts(19, 0), 1000.0)])
    _add(service, "sensor.stab", "sensor", "total_increasing", "kWh", [(_ts(19, 0), 5.0)])
    _add(service, "sensor.t", "sensor", "measurement", "°C", [(_ts(19, 0), 1.0)])
    _configure(service, temperature="sensor.t", heating=[
        {"entity_id": "sensor.wp", "name": ""}, {"entity_id": "sensor.stab", "name": "Heizstab"},
        {"entity_id": "sensor.t", "name": "falsch"}, {"entity_id": "sensor.fehlt", "name": ""},
    ])
    counters = service.heating_counters(wetter_routes._load_config(service.deps.index))
    assert [(c["entity_id"], c["scale"]) for c in counters] == [("sensor.wp", 0.001), ("sensor.stab", 1.0)]
    assert counters[1]["name"] == "Heizstab"


def test_compute_heating_needs_counters_and_a_temperature(service) -> None:
    _add(service, "sensor.t", "sensor", "measurement", "°C", [(_ts(19, 0), 1.0)])
    _configure(service, temperature="sensor.t")
    assert service.compute_heating(NOW) is None
    _configure(service, temperature="sensor.t", heating=[{"entity_id": "sensor.fehlt", "name": ""}])
    assert service.compute_heating(NOW) is None


def test_compute_heating_shape(service) -> None:
    _add(service, "sensor.t", "sensor", "measurement", "°C", [(_ts(19, 0), 1.0)])
    _add(service, "sensor.wp", "sensor", "total_increasing", "kWh", [(_ts(19, 0), 10.0), (_ts(19, 12), 14.0)])
    _configure(service, temperature="sensor.t", heating=[{"entity_id": "sensor.wp", "name": "Wärmepumpe"}])
    data = service.compute_heating(NOW)
    assert data["counters"] == [{"entity_id": "sensor.wp", "name": "Wärmepumpe"}]
    assert data["room"] == 20.0 and data["limit"] == 15.0 and data["today"] == "2026-08-20"
    assert data["days"] == []  # August liegt außerhalb der Heizperiode, und ein Messwert ist kein abgedeckter Tag


def test_heating_setup_and_route_through_the_app(client) -> None:
    from app.main import index

    index.get_or_create_entity("sensor.wetter_test_temp4", "sensor", "measurement", "°C")
    index.get_or_create_entity("sensor.wetter_test_wp", "sensor", "total_increasing", "kWh")
    index.set_setting(wetter_routes.SETTING_CONFIG, "")
    base = {"temperature": "sensor.wetter_test_temp4"}
    assert client.post("/wetterdashboard/setup", data={**base, "heating_entity_id": "sensor.wetter_test_temp4"}).status_code == 400
    assert client.post("/wetterdashboard/setup", data={**base, "heating_room": "40"}).status_code == 400
    assert client.post("/wetterdashboard/setup", data={**base, "heating_limit": "kalt"}).status_code == 400
    assert client.get("/wetterdashboard/heizen").status_code == 409  # die abgelehnten Eingaben haben nichts gespeichert
    assert client.post("/wetterdashboard/setup", data=base).status_code == 200
    assert client.get("/wetterdashboard/heizen").status_code == 404  # eingerichtet, aber kein Heizzähler
    saved = client.post("/wetterdashboard/setup", data={
        **base, "heating_entity_id": ["sensor.wetter_test_wp", ""], "heating_name": ["WP", ""],
        "heating_room": "21,5", "heating_limit": "14",
    })
    assert saved.status_code == 200 and "Heizen" in saved.text  # Reiter erscheint
    config = wetter_routes._load_config(index)
    assert config["heating"] == [{"entity_id": "sensor.wetter_test_wp", "name": "WP"}]
    assert (config["heating_room"], config["heating_limit"]) == (21.5, 14.0)
    answer = client.get("/wetterdashboard/heizen")
    assert answer.status_code == 200 and answer.json()["room"] == 21.5 and answer.json()["counters"][0]["name"] == "WP"
    assert "wetter-heating.js" in client.get("/wetterdashboard").text
    index.set_setting(wetter_routes.SETTING_CONFIG, "")


def test_the_weather_dashboard_can_be_the_start_page(client) -> None:
    from app.main import STARTSEITE_LABELS, index

    assert "wetterdashboard" in STARTSEITE_LABELS
    previous = index.get_setting("startseite", "uebersicht")
    try:
        index.set_setting("startseite", "wetterdashboard")
        answer = client.get("/", follow_redirects=False)
        assert answer.status_code == 307 and answer.headers["location"].endswith("/wetterdashboard")
        index.set_setting("startseite", "energiedashboard")
        assert client.get("/", follow_redirects=False).headers["location"].endswith("/energiedashboard")
        index.set_setting("startseite", "unsinn")  # unbekannter Wert: zurück auf die Übersicht
        assert client.get("/", follow_redirects=False).headers["location"].endswith("/uebersicht")
        index.set_setting("startseite", "wetterdashboard")
        assert 'edash-pin-home-badge' in client.get("/dashboards").text
        index.set_setting("startseite", "uebersicht")
        assert client.post("/settings/darstellung", data={"startseite": "wetterdashboard"}).status_code == 200
        assert index.get_setting("startseite") == "wetterdashboard"
        assert client.post("/settings/darstellung", data={"startseite": "unsinn"}).status_code == 400
    finally:
        index.set_setting("startseite", previous)


def test_view_template_never_iterates_data_that_may_still_be_null() -> None:
    """Die Daten der Reiter kommen erst nach dem Laden. Ein x-for direkt auf `longterm.records`,
    `heat.counters` o. Ä. wirft bei jedem Seitenaufruf einen Alpine-Fehler in die Konsole."""
    import re

    from _paths import TEMPLATES

    text = (TEMPLATES / "_wetter_view.html").read_text(encoding="utf-8")
    for match in re.finditer(r'x-for="[^"]* in ([^"]+)"', text):
        expression = match.group(1)
        assert not re.match(r"^(longterm|heat|precip|monthly|matrix)\.", expression), expression


def test_radiation_defaults_to_whole_numbers_and_other_roles_stay_automatic(service) -> None:
    _add(service, "sensor.t", "sensor", "measurement", "°C", [(_ts(20, 1), 12.0)])
    _add(service, "sensor.rad", "sensor", "measurement", "W/m²", [(_ts(20, 1), 300.0)])
    _add(service, "sensor.wind", "sensor", "measurement", "km/h", [(_ts(20, 1), 9.0)])
    _configure(service, temperature="sensor.t", radiation="sensor.rad", wind="sensor.wind")
    specs = {spec["key"]: spec for spec in service._row_specs(wetter_routes._load_config(service.deps.index))}
    assert specs["radiation"]["decimals"] == 0 and specs["wind"]["decimals"] is None
    service.deps.index.set_config("sensor.rad", decimals="2")  # eine ausdrückliche Einstellung gewinnt
    again = {spec["key"]: spec for spec in service._row_specs(wetter_routes._load_config(service.deps.index))}
    assert again["radiation"]["decimals"] == 2


def test_radiation_has_no_minimum_row_in_the_script() -> None:

    script = _wetter_script()
    assert "row.key === 'radiation'" in script


def test_tabs_use_the_app_standard_tab_row() -> None:
    from _paths import TEMPLATES

    text = (TEMPLATES / "_wetter_view.html").read_text(encoding="utf-8")
    assert 'class="entity-tabs' in text and text.count('class="entity-tab"') >= 2
    assert "wt-tabs button" not in text


def test_column_selection_is_a_popover_like_the_entity_list() -> None:
    from _paths import TEMPLATES

    text = (TEMPLATES / "_wetter_view.html").read_text(encoding="utf-8")
    assert "toggleDDPicker('wt-cols-{{ sfx }}')" in text and 'id="wt-cols-{{ sfx }}-popover"' in text
    assert 'class="wt-check"><input type="checkbox" :checked="columns.includes(key)"' not in text


def test_comparison_table_sits_in_the_app_table_frame() -> None:
    from _paths import TEMPLATES

    assert 'class="tbl-wrap wt-table-wrap"' in (TEMPLATES / "_wetter_view.html").read_text(encoding="utf-8")
    assert "is-scrollable" in _wetter_script()


def test_tiles_carry_the_entity_and_link_to_it_and_css_uses_defined_font_tokens(service) -> None:
    from _paths import APP, TEMPLATES

    _add(service, "sensor.t", "sensor", "measurement", "°C", [(_ts(20, 1), 12.0)])
    _configure(service, temperature="sensor.t")
    assert service.compute_tiles(NOW)[0]["entity_id"] == "sensor.t"
    assert "'entities/' + encodeURIComponent(tile.entity_id)" in (TEMPLATES / "_wetter_view.html").read_text(encoding="utf-8")
    css = (APP / "static/css/pages/wetterdashboard.css").read_text(encoding="utf-8")
    assert "var(--mono)" not in css and "var(--sans)" not in css  # in app.css heißen die Schriften --font-mono/--font-display


def test_cards_match_the_energy_dashboard_card_metrics() -> None:
    from _paths import APP

    # Die Kartenregeln stehen seit dem Reiter „Langzeit“ des Energiedashboards in app.css (beide Seiten brauchen sie).
    css = (APP / "static/css/app.css").read_text(encoding="utf-8")
    energy = (APP / "static/css/pages/energiedashboard.css").read_text(encoding="utf-8")
    assert ".edash-card{border:1px solid var(--border);border-radius:12px;background:var(--surface);padding:16px 18px" in energy
    assert ".wt-card{padding:16px 18px;border-radius:12px;box-shadow:var(--shadow);}" in css


def test_setup_form_uses_app_fields_and_info_buttons(client) -> None:
    from app.main import index

    index.set_setting(wetter_routes.SETTING_CONFIG, "")
    html = client.get("/wetterdashboard/setup").text
    assert html.count('class="field"') >= 4 and "wt-item-add" in html
    assert html.count("hint-toggle") == 5  # Weitere Sensoren, Regentage, Sonne und Tageslicht, Anzeige, Experimentell
    assert '<p class="hint">' not in html  # lose .hint-Absätze verschwänden auf dem Telefon ohne Knopf


def test_conditions_layout_setting_defaults_saves_and_rejects_unknown_values(client) -> None:
    from app.main import index

    index.get_or_create_entity("sensor.wetter_test_temp12", "sensor", "measurement", "°C")
    index.get_or_create_entity("sensor.wetter_test_wind12", "sensor", "measurement", "km/h")
    index.set_setting(wetter_routes.SETTING_CONFIG, "")
    base = {"temperature": "sensor.wetter_test_temp12", "wind": "sensor.wetter_test_wind12"}
    assert wetter_routes.DEFAULT_LAGE_LAYOUT in wetter_routes.LAGE_LAYOUTS
    assert client.post("/wetterdashboard/setup", data={**base, "lage_layout": "riesig"}).status_code == 400
    page = client.post("/wetterdashboard/setup", data=base)  # ohne Angabe: Standard
    assert f'data-layout="{wetter_routes.DEFAULT_LAGE_LAYOUT}"' in page.text
    for layout in wetter_routes.LAGE_LAYOUTS:
        saved = client.post("/wetterdashboard/setup", data={**base, "lage_layout": layout})
        assert saved.status_code == 200 and f'data-layout="{layout}"' in saved.text
    # Ein Aufruf ohne das Feld (ältere Aufrufer) behält die letzte Wahl.
    assert 'data-layout="large"' in client.post("/wetterdashboard/setup", data={**base, "lage_layout": "large"}).text
    assert 'data-layout="large"' in client.post("/wetterdashboard/setup", data=base).text
    # Die Auswahl steht in der Einrichtung, mit der gespeicherten Wahl vorbelegt.
    setup = client.get("/wetterdashboard/setup").text
    assert 'name="lage_layout" value="large" checked' in setup
    index.set_setting(wetter_routes.SETTING_CONFIG, "")


def test_tile_style_setting_defaults_saves_and_rejects_unknown_values(client) -> None:
    from app.main import index

    index.get_or_create_entity("sensor.wetter_test_temp13", "sensor", "measurement", "°C")
    index.set_setting(wetter_routes.SETTING_CONFIG, "")
    base = {"temperature": "sensor.wetter_test_temp13"}
    assert wetter_routes.DEFAULT_TILE_STYLE in wetter_routes.TILE_STYLES
    assert client.post("/wetterdashboard/setup", data={**base, "tile_style": "neon"}).status_code == 400
    page = client.post("/wetterdashboard/setup", data=base)  # ohne Angabe: Standard
    assert f'data-tile-style="{wetter_routes.DEFAULT_TILE_STYLE}"' in page.text
    for style in wetter_routes.TILE_STYLES:
        saved = client.post("/wetterdashboard/setup", data={**base, "tile_style": style})
        assert saved.status_code == 200 and f'data-tile-style="{style}"' in saved.text
    # Ein Aufruf ohne das Feld (ältere Aufrufer) behält die letzte Wahl.
    assert 'data-tile-style="tint"' in client.post("/wetterdashboard/setup", data={**base, "tile_style": "tint"}).text
    assert 'data-tile-style="tint"' in client.post("/wetterdashboard/setup", data=base).text
    setup = client.get("/wetterdashboard/setup").text
    assert 'name="tile_style" value="tint" checked' in setup
    index.set_setting(wetter_routes.SETTING_CONFIG, "")


# -- Stichtag ---------------------------------------------------------------------------------


def test_reference_day_moves_every_column_to_that_day(service) -> None:
    anchor = datetime(2026, 3, 14, 12, tzinfo=TZ)
    heute = service.column_windows("heute", NOW, "bis_jetzt", anchor)
    assert heute["start"] == datetime(2026, 3, 14, tzinfo=TZ) and heute["end"] == datetime(2026, 3, 15, tzinfo=TZ)
    assert not heute["partial"] and heute["vj_end"] == datetime(2025, 3, 15, tzinfo=TZ)  # ein vergangener Tag ist vollständig
    monat = service.column_windows("monat", NOW, "bis_jetzt", anchor)
    assert monat["start"] == datetime(2026, 3, 1, tzinfo=TZ) and monat["end"] == datetime(2026, 3, 15, tzinfo=TZ)
    assert monat["partial"] and monat["vj_end"] == datetime(2025, 3, 15, tzinfo=TZ)  # derselbe Ausschnitt im Vorjahr
    komplett = service.column_windows("monat", NOW, "komplett", anchor)
    assert komplett["vj_end"] == datetime(2025, 4, 1, tzinfo=TZ)
    assert service.column_windows("vormonat", NOW, "bis_jetzt", anchor)["start"] == datetime(2026, 2, 1, tzinfo=TZ)
    assert service.column_windows("gestern", NOW, "bis_jetzt", anchor)["start"] == datetime(2026, 3, 13, tzinfo=TZ)
    assert service.column_windows("jahr", NOW, "bis_jetzt", anchor)["end"] == datetime(2026, 3, 15, tzinfo=TZ)


def test_reference_day_on_the_last_day_of_a_month_makes_the_month_complete(service) -> None:
    anchor = datetime(2026, 7, 31, 12, tzinfo=TZ)
    monat = service.column_windows("monat", NOW, "bis_jetzt", anchor)
    assert monat["end"] == datetime(2026, 8, 1, tzinfo=TZ) and not monat["partial"]


def test_table_at_a_reference_day_reads_that_day_from_the_archive(service) -> None:
    _add(service, "sensor.t", "sensor", "measurement", "°C", [(_ts(10, 8), 4.0), (_ts(10, 16), 9.0), (_ts(19, 8), 10.0), (_ts(20, 1), 12.0)])
    _configure(service, temperature="sensor.t")
    data = service.compute_table(TableRequest(columns=["heute", "gestern"], vorjahr=False, at="2026-08-10"), NOW)
    assert data["at"] == "2026-08-10" and data["today"] == "2026-08-20"
    heute = data["rows"][0]["cells"]["heute"]["cur"]
    assert (heute["max"], heute["min"]) == (9.0, 4.0)
    assert data["columns"][0]["cur"]["start"] == datetime(2026, 8, 10, tzinfo=TZ).timestamp()
    assert not data["columns"][0]["partial"]


def test_reference_day_today_or_later_means_now_and_garbage_is_rejected(service) -> None:
    _add(service, "sensor.t", "sensor", "measurement", "°C", [(_ts(20, 1), 12.0)])
    _configure(service, temperature="sensor.t")
    for value in ("2026-08-20", "2027-01-01", None, ""):
        assert service.compute_table(TableRequest(columns=["heute"], vorjahr=False, at=value), NOW)["at"] is None
    with pytest.raises(Exception) as caught:
        service.compute_table(TableRequest(columns=["heute"], at="gestern"), NOW)
    assert getattr(caught.value, "status_code", None) == 400


def test_tiles_and_table_show_placeholders_while_loading() -> None:
    from _paths import TEMPLATES

    text = (TEMPLATES / "_wetter_view.html").read_text(encoding="utf-8")
    assert 'x-show="!tilesLoaded"' in text and "wt-tile is-loading" in text and 'class="wt-loading"' in text
    assert "this.tilesLoaded = true" in _wetter_script()


def test_day_columns_name_the_hour_not_a_minute() -> None:

    script = _wetter_script()
    assert "t('{from}–{to} Uhr'" in script and "fmtDate(formats.hm, ts)" not in script


def test_tabs_are_keyboard_operable_and_linked_to_their_panels() -> None:
    import re

    from _paths import TEMPLATES

    text = (TEMPLATES / "_wetter_view.html").read_text(encoding="utf-8")
    tabs = set(re.findall(r'role="tab" id="wt-tab-(\w+)" aria-controls="wt-panel-\1"', text))
    panels = set(re.findall(r'role="tabpanel" id="wt-panel-(\w+)" aria-labelledby="wt-tab-\1"', text))
    assert tabs == panels and {"overview", "long"} <= tabs
    assert text.count('@keydown="tabKey($event)"') == len(tabs) and text.count(":tabindex=") == len(tabs)
    script = _wetter_script()
    assert all(key in script for key in ("ArrowLeft", "ArrowRight", "Home", "End"))


def test_toggles_are_app_chips_and_card_heads_keep_one_layout() -> None:
    from _paths import TEMPLATES

    text = (TEMPLATES / "_wetter_view.html").read_text(encoding="utf-8")
    assert text.count('class="filter-chip"') == 2  # Spanne der Vorjahre, Heizzähler
    assert '<input type="checkbox" x-model="band"' in text and 'class="wt-check"><input type="checkbox"' not in text
    # Jede Karte beginnt mit der Kopfzeile: Titel links, Bedienelemente rechts.
    assert text.count('<div class="wt-controls">') >= 8


def test_year_over_year_is_one_menu_and_not_part_of_the_column_list() -> None:
    from _paths import TEMPLATES

    text = (TEMPLATES / "_wetter_view.html").read_text(encoding="utf-8")
    assert "toggleDDPicker('wt-yoy-{{ sfx }}')" in text and 'id="wt-yoy-{{ sfx }}-popover"' in text
    assert 'x-model="vorjahr"' not in text and "Mit Vorjahr" not in text  # kein Haken zwischen den Spalten mehr
    assert "wt-picker-sep" not in text
    script = _wetter_script()
    assert "setYoy(choice)" in script and "yoyLabel()" in script


def test_tiles_use_short_names_and_the_layout_helper(service) -> None:
    from _paths import TEMPLATES

    _add(service, "sensor.regen", "sensor", "total_increasing", "mm", [(_ts(19, 0), 100.0), (_ts(20, 6), 103.5)])
    _add(service, "binary_sensor.regen", "binary_sensor", None, "", [(_ts(20, 6), 1.0), (_ts(20, 6, 30), 0.0)])
    _add(service, "sensor.t", "sensor", "measurement", "°C", [(_ts(20, 1), 12.0)])
    _configure(service, temperature="sensor.t", precip_mm="sensor.regen", precip_dur="binary_sensor.regen")
    assert [tile["label"] for tile in service.compute_tiles(NOW)] == ["Temperatur", "Niederschlag", "Regen"]
    assert "WetterLayout.tileColumns" in _wetter_script()
    assert "wetter-layout.js" in (TEMPLATES / "wetterdashboard.html").read_text(encoding="utf-8")


def test_reference_day_uses_the_dashboard_stichtag_bar_of_the_app() -> None:
    from _paths import TEMPLATES

    view = (TEMPLATES / "_wetter_view.html").read_text(encoding="utf-8")
    page = (TEMPLATES / "wetterdashboard.html").read_text(encoding="utf-8")
    assert "{{ period_nav(true, true) }}" in view and "period_assets()" in page
    assert "wt-daynav" not in view and 'type="date"' not in view
    script = _wetter_script()
    assert "Alpine.store('dashStichtag').date" in script and "'dashboard-stichtag'" in script
    assert "shiftStichtag" not in script and "setStichtag" not in script


def test_page_context_carries_the_calendar_bounds_for_the_stichtag_bar(client) -> None:
    from app.main import index

    index.get_or_create_entity("sensor.wetter_test_temp5", "sensor", "measurement", "°C")
    index.set_setting(wetter_routes.SETTING_CONFIG, "")
    html = client.get("/wetterdashboard").text
    assert "dash-period-nav" not in html  # noch nicht eingerichtet: keine Leiste
    wetter_routes._save_config(index, {**wetter_routes._empty_config(), "temperature": "sensor.wetter_test_temp5"})
    page = client.get("/wetterdashboard").text
    assert "dash-period-nav" in page and "dashboard-period-nav.js" in page and "calendar-picker.js" in page
    index.set_setting(wetter_routes.SETTING_CONFIG, "")


def test_columns_with_a_reference_day_show_the_weekday_under_the_date() -> None:

    script = _wetter_script()
    assert "formats.weekday" in script and "weekday: new Intl.DateTimeFormat" in script


def test_wind_and_sun_tab_groups_tiles_and_rows() -> None:
    from _paths import TEMPLATES

    view = (TEMPLATES / "_wetter_view.html").read_text(encoding="utf-8")
    assert "{% if show_windtab %}" in view and "has_windtiles" in view and 'id="wt-panel-wind"' in view
    assert 'weather_card("basis"' in view and 'weather_card("wind"' in view
    assert 'weather_card("klima"' in view and 'id="wt-panel-klima"' in view
    script = _wetter_script()
    assert "const WIND_KEYS = ['wind', 'gust', 'radiation', 'illuminance']" in script and "groupOf" in script


def test_wind_and_sun_tab_only_appears_with_one_of_its_roles(client) -> None:
    from app.main import index

    index.get_or_create_entity("sensor.wetter_test_temp6", "sensor", "measurement", "°C")
    index.get_or_create_entity("sensor.wetter_test_wind6", "sensor", "measurement", "km/h")
    base = {**wetter_routes._empty_config(), "temperature": "sensor.wetter_test_temp6"}  # noqa: SLF001
    wetter_routes._save_config(index, base)  # noqa: SLF001
    assert "wt-tab-wind" not in client.get("/wetterdashboard").text
    wetter_routes._save_config(index, {**base, "wind": "sensor.wetter_test_wind6"})  # noqa: SLF001
    assert "wt-tab-wind" in client.get("/wetterdashboard").text
    index.set_setting(wetter_routes.SETTING_CONFIG, "")


def test_extra_sensors_get_tiles_and_the_climate_tab_only_with_extras(client) -> None:
    from app.main import index

    index.get_or_create_entity("sensor.wetter_test_temp7", "sensor", "measurement", "°C")
    index.get_or_create_entity("sensor.wetter_test_keller7", "sensor", "measurement", "%")
    base = {**wetter_routes._empty_config(), "temperature": "sensor.wetter_test_temp7"}  # noqa: SLF001
    wetter_routes._save_config(index, base)  # noqa: SLF001
    assert "wt-tab-klima" not in client.get("/wetterdashboard").text
    wetter_routes._save_config(  # noqa: SLF001
        index, {**base, "extra_temperatures": [{"entity_id": "sensor.wetter_test_keller7", "name": "Keller"}]}
    )
    assert "wt-tab-klima" in client.get("/wetterdashboard").text
    tiles = client.get("/wetterdashboard/tiles").json()["tiles"]
    assert [tile["key"] for tile in tiles] == ["temperature", "temperature:0"]
    assert tiles[1]["label"] == "Keller"
    index.set_setting(wetter_routes.SETTING_CONFIG, "")


def test_setup_suggestions_route_and_button(client) -> None:
    from app.main import index

    index.get_or_create_entity("sensor.wetter_test_aussen8", "sensor", "measurement", "°C")
    data = client.get("/wetterdashboard/setup/vorschlaege").json()
    assert set(data) == {"roles", "heating", "extras", "missing", "has_catalog"}
    setup = client.get("/wetterdashboard/setup").text
    assert "suggest()" in setup and "roleVals" in setup and "wetterdashboard/setup/vorschlaege" in setup


def test_hidden_blocks_leave_the_table_and_are_saved_from_the_setup_form(service, client) -> None:
    from app.main import index

    _add(service, "sensor.t", "sensor", "measurement", "°C", [(_ts(20, 1), 12.0), (_ts(20, 10), 18.0)])
    _add(service, "sensor.h", "sensor", "measurement", "%", [(_ts(20, 1), 60.0)])
    _configure(service, temperature="sensor.t", humidity="sensor.h", hidden_blocks=["kenntage", "humidity"])
    data = service.compute_table(TableRequest(columns=["heute"], vorjahr=False), NOW)
    assert [row["key"] for row in data["rows"]] == ["temperature"]

    index.get_or_create_entity("sensor.wetter_test_temp9", "sensor", "measurement", "°C")
    base = {**wetter_routes._empty_config(), "temperature": "sensor.wetter_test_temp9"}  # noqa: SLF001
    wetter_routes._save_config(index, {**base, "hidden_blocks": ["gust"]})  # noqa: SLF001
    form = {"temperature": "sensor.wetter_test_temp9"}
    client.post("/wetterdashboard/setup", data=form)  # ohne Marker bleibt die Auswahl
    assert wetter_routes._load_config(index)["hidden_blocks"] == ["gust"]  # noqa: SLF001
    client.post("/wetterdashboard/setup", data={**form, "blocks_submitted": "1", "show_block": ["kenntage", "wind"]})
    assert "kenntage" not in wetter_routes._load_config(index)["hidden_blocks"]  # noqa: SLF001
    assert "gust" in wetter_routes._load_config(index)["hidden_blocks"]  # noqa: SLF001
    index.set_setting(wetter_routes.SETTING_CONFIG, "")


def test_tiles_follow_the_reference_day(service) -> None:
    _add(service, "sensor.t", "sensor", "measurement", "°C", [(_ts(19, 1), 3.0), (_ts(19, 12), 11.0), (_ts(19, 22), 6.0), (_ts(20, 9), 15.0)])
    _configure(service, temperature="sensor.t")
    today = service.compute_tiles(NOW)[0]
    assert today["last"] == 15.0 and today["today"]["max"] == 15.0
    past = service.compute_tiles(NOW, at="2026-08-19")[0]
    assert past["last"] == 6.0  # Wert am Ende des gewählten Tages
    assert past["today"]["min"] == 3.0 and past["today"]["max"] == 11.0
    assert service.compute_tiles(NOW, at="2026-08-20")[0]["last"] == 15.0  # heute gilt als „jetzt“


# -- Sonnenscheindauer (ROADMAP 1.43, Ergänzung Helligkeit) --------------------------------------


def _hour_rows(day: int, values: list[tuple[int, float]]) -> list:
    return [rollup_mod.FineRow(bucket_start=_ts(day, hour), value=value) for hour, value in values]


def test_summarize_sunshine_counts_hours_at_or_above_the_threshold_per_day(service) -> None:
    config = {**wetter_routes._empty_config(), "sun_threshold_lx": 25000.0}
    rows = (
        _hour_rows(18, [(9, 30000.0), (10, 25000.0), (11, 24999.0)])  # zwei Stunden: die Schwelle selbst zählt
        + _hour_rows(19, [(9, 90000.0), (10, 98800.0), (11, 60000.0), (12, 10000.0)])  # drei Stunden
    )
    summary = service.summarize("sunshine", rows, config)
    assert summary["hours"] == pytest.approx(5.0)
    assert summary["top"] == pytest.approx(3.0) and summary["top_ts"] == _ts(19, 9)


def test_summarize_sunshine_counts_a_cut_hour_only_up_to_the_end(service) -> None:
    config = {**wetter_routes._empty_config(), "sun_threshold_lx": 25000.0}
    rows = _hour_rows(20, [(10, 40000.0), (11, 40000.0)])
    # Bis 11:30: die zweite Stunde ist erst zur Hälfte vorbei (laufende Stunde bzw. Schnitt des Vorjahresfensters).
    assert service.summarize("sunshine", rows, config, until=_ts(20, 11, 30))["hours"] == pytest.approx(1.5)
    assert service.summarize("sunshine", rows, config, until=_ts(21, 0))["hours"] == pytest.approx(2.0)


def test_summarize_sunshine_bridges_gaps_between_two_bright_hours_only(service) -> None:
    # Ein Sensor, der mittags bei 98.800 lx klebt, löst kein Ereignis aus: die Stunden dazwischen haben keine Zeile.
    config = {**wetter_routes._empty_config(), "sun_threshold_lx": 25000.0}
    bright = _hour_rows(20, [(10, 98800.0), (14, 98800.0)])
    assert service.summarize("sunshine", bright, config)["hours"] == pytest.approx(5.0)  # 10 bis 14 Uhr
    # Dunkle Zeile danach: die Lücke gilt nicht als sonnig (die Wolke kam irgendwann in der Lücke).
    assert service.summarize("sunshine", _hour_rows(20, [(10, 98800.0), (13, 5000.0)]), config)["hours"] == pytest.approx(1.0)
    # Eine Lücke über der Grenze (Ausfall) zählt nicht.
    assert service.summarize("sunshine", _hour_rows(19, [(7, 98800.0)]) + _hour_rows(19, [(15, 98800.0)]), config)["hours"] == pytest.approx(2.0)
    # Das Ende des Fensters kappt auch aufgefüllte Stunden.
    assert service.summarize("sunshine", bright, config, until=_ts(20, 12, 30))["hours"] == pytest.approx(2.5)


def test_summarize_sunshine_without_values_is_missing_not_zero(service) -> None:
    config = {**wetter_routes._empty_config(), "sun_threshold_lx": 25000.0}
    assert service.summarize("sunshine", [], config) is None
    assert service.summarize("sunshine", [rollup_mod.FineRow(bucket_start=_ts(20, 9))], config) is None
    # Ein dunkler Tag mit Werten ist 0 Stunden, nicht „fehlt“.
    dark = service.summarize("sunshine", _hour_rows(20, [(9, 3000.0)]), config)
    assert dark["hours"] == 0.0 and dark["top"] is None and dark["top_ts"] is None


def test_table_has_a_sunshine_row_from_the_illuminance_role(service) -> None:
    _add(service, "sensor.t", "sensor", "measurement", "°C", [(_ts(19, 8), 10.0), (_ts(20, 1), 12.0)])
    _add(service, "sensor.lux_sued", "sensor", "measurement", "lx", [
        (_ts(19, 8), 30000.0), (_ts(19, 9), 10000.0), (_ts(19, 10), 40000.0), (_ts(19, 11), 0.0),
        (_ts(20, 10), 40000.0), (_ts(20, 11), 40000.0),
    ])
    _configure(service, temperature="sensor.t", illuminance="sensor.lux_sued")
    data = service.compute_table(TableRequest(columns=["heute", "gestern"], vorjahr=True), NOW)
    rows = {row["key"]: row for row in data["rows"]}
    assert list(rows) == ["temperature", "kenntage", "illuminance"]
    sun = rows["illuminance"]
    assert sun["kind"] == "sunshine" and sun["label"] == "Sonnenscheindauer"
    assert sun["cells"]["gestern"]["cur"]["hours"] == pytest.approx(2.0)  # 8 Uhr und 10 Uhr
    assert sun["cells"]["heute"]["cur"]["hours"] == pytest.approx(2.0)  # 10 und 11 Uhr bei 40.000 lx
    assert sun["cells"]["heute"]["vj"] is None  # das Archiv beginnt erst gestern: kein Vorjahr, nie 0
    assert data["sun_threshold_lx"] == 25000.0


def test_sunshine_threshold_config_round_trip_and_bounds(service) -> None:
    index = service.deps.index
    assert wetter_routes._load_config(index)["sun_threshold_lx"] == wetter_routes.DEFAULT_SUN_THRESHOLD_LX
    _configure(service, temperature="sensor.t", sun_threshold_lx=40000.0)
    assert wetter_routes._load_config(index)["sun_threshold_lx"] == 40000.0
    _configure(service, temperature="sensor.t", sun_threshold_lx=5.0)  # außerhalb: Standard statt Unsinn
    assert wetter_routes._load_config(index)["sun_threshold_lx"] == wetter_routes.DEFAULT_SUN_THRESHOLD_LX


def test_sunshine_setup_through_the_app(client) -> None:
    from app.main import index

    index.set_setting(wetter_routes.SETTING_CONFIG, "")
    index.get_or_create_entity("sensor.sonne_test_temp", "sensor", "measurement", "°C")
    index.get_or_create_entity("sensor.sonne_test_lux", "sensor", "measurement", "lx")
    index.get_or_create_entity("sensor.sonne_test_zaehler", "sensor", "total_increasing", "kWh")
    form = client.get("/wetterdashboard/setup").text
    assert 'name="sun_threshold_lx"' in form and 'data-role="illuminance"' in form
    base = {"temperature": "sensor.sonne_test_temp", "illuminance": "sensor.sonne_test_lux"}
    assert client.post("/wetterdashboard/setup", data={**base, "sun_threshold_lx": "30000"}).status_code == 200
    config = wetter_routes._load_config(index)
    assert config["illuminance"] == "sensor.sonne_test_lux" and config["sun_threshold_lx"] == 30000.0
    assert client.post("/wetterdashboard/setup", data={**base, "sun_threshold_lx": "abc"}).status_code == 400
    assert client.post("/wetterdashboard/setup", data={**base, "sun_threshold_lx": "50"}).status_code == 400
    # Ein Zähler passt nicht zur Rolle Helligkeit.
    assert client.post("/wetterdashboard/setup", data={
        "temperature": "sensor.sonne_test_temp", "illuminance": "sensor.sonne_test_zaehler",
    }).status_code == 400
    # Leer: Standard.
    assert client.post("/wetterdashboard/setup", data=base).status_code == 200
    assert wetter_routes._load_config(index)["sun_threshold_lx"] == wetter_routes.DEFAULT_SUN_THRESHOLD_LX
    index.set_setting(wetter_routes.SETTING_CONFIG, "")


def test_sunshine_row_and_metrics_exist_in_the_script() -> None:

    script = _wetter_script()
    assert "row.kind === 'sunshine'" in script and "field: 'hours'" in script
    assert "data.sun_threshold_lx" in script  # die Schwelle steht in der Beschriftung, nicht versteckt


def test_tiles_carry_the_sunshine_hours_when_illuminance_is_assigned(service) -> None:
    """Die Sonnenscheindauer ist eine Kachel mit Stunden; mit dem Aggregationstyp „sunshine“ stürzte die Abfrage einmal ab."""
    _add(service, "sensor.t", "sensor", "measurement", "°C", [(_ts(20, 1), 12.0)])
    _add(service, "sensor.lux_sued", "sensor", "measurement", "lx", [
        (_ts(19, 10, 30), 40000.0), (_ts(19, 11, 30), 40000.0), (_ts(20, 10, 30), 40000.0), (_ts(20, 11, 30), 40000.0),
    ])
    _configure(service, temperature="sensor.t", illuminance="sensor.lux_sued")

    tiles = {tile["key"]: tile for tile in service.compute_tiles(NOW)}
    assert list(tiles) == ["temperature", "illuminance"]
    sun = tiles["illuminance"]
    assert sun["kind"] == "sunshine" and sun["label"] == "Sonnenschein"
    assert sun["today"]["hours"] == pytest.approx(2.0) and sun["yesterday"]["hours"] == pytest.approx(2.0)


def _dayimage_data(service) -> None:
    def day(number: int, lux: dict[int, float]) -> tuple[list, list]:
        main = [(_ts(number, hour, 30), value) for hour, value in lux.items()]
        dawn = [(_ts(number, hour, 30), 1000.0 if value >= 1000 else 0.0) for hour, value in lux.items()]
        return main, dawn

    sunny = {hour: 80000.0 for hour in range(8, 17)}
    gray = {hour: 9000.0 for hour in range(8, 17)}
    mixed = {hour: (80000.0 if hour in (10, 11, 12) else 9000.0) for hour in range(8, 17)}
    short = {7: 0.0, 8: 9000.0, 9: 9000.0, 10: 0.0}
    main_rows: list = []
    dawn_rows: list = []
    for number, lux in ((19, sunny), (18, gray), (17, mixed), (16, short)):
        main, dawn = day(number, lux)
        main_rows += main
        dawn_rows += dawn
    _add(service, "sensor.lux_sued", "sensor", "measurement", "lx", main_rows)
    _add(service, "sensor.lux_daem", "sensor", "measurement", "lx", dawn_rows)
    _configure(service, temperature="sensor.t", illuminance="sensor.lux_sued", illuminance_dawn="sensor.lux_daem")


def test_dayimage_classifies_each_day_by_its_sun_share(service) -> None:
    _dayimage_data(service)
    data = service.compute_dayimage(NOW, days=6)
    classes = {row["date"][-2:]: row["class"] for row in data["days"]}
    assert classes == {"15": None, "16": None, "17": "mixed", "18": "overcast", "19": "sunny", "20": None}
    sunny = next(row for row in data["days"] if row["date"].endswith("19"))
    assert sunny["share"] == pytest.approx(1.0) and sunny["bright_hours"] == pytest.approx(9.0)
    mixed = next(row for row in data["days"] if row["date"].endswith("17"))
    assert mixed["share"] == pytest.approx(3 / 9, abs=0.12)
    assert data["counts"] == {"sunny": 1, "mixed": 1, "overcast": 1, "none": 3}
    assert next(row for row in data["days"] if row["date"].endswith("20"))["today"] is True  # heute noch ohne helle Stunden


def test_heating_days_carry_the_sun_class_of_the_dayimage(service) -> None:
    config_days = [["2026-08-10", 5.0, [1.0]], ["2026-08-17", 5.0, [1.0]], ["2026-08-18", 5.0, [1.0]], ["2026-08-19", 5.0, [1.0]]]
    _dayimage_data(service)
    with service.deps.index._conn:  # noqa: SLF001 — die Testdaten sind absteigend geschrieben
        service.deps.index._conn.execute(  # noqa: SLF001
            "UPDATE entities SET first_ts = ? WHERE entity_id = 'sensor.lux_sued'", (_ts(16, 7, 30),),
        )
    config = wetter_routes._load_config(service.deps.index)
    sun = service._heating_sun(config_days, config, NOW)
    # Tage ohne Klasse (vor dem Archivbeginn der Helligkeit) fehlen, es steht nie eine geratene Klasse da.
    assert sun == {"2026-08-17": "mixed", "2026-08-18": "overcast", "2026-08-19": "sunny"}
    assert service._heating_sun([], config, NOW) == {}
    _configure(service, temperature="sensor.t")
    assert service._heating_sun(config_days, wetter_routes._load_config(service.deps.index), NOW) == {}


def _pv_data(service, last_factor: float = 1.0, unit: str = "W") -> None:
    lux_rows, pv_rows = [], []
    for day in range(10, 20):
        for hour in range(8, 16):
            lux = 60000.0
            if not (day == 19 and hour == 11):  # eine Lücke: der gesättigte Süd-Sensor schreibt bei gleichem Wert nichts
                lux_rows.append((_ts(day, hour, 30), lux))
            factor = last_factor if day == 19 else 1.0
            pv_rows.append((_ts(day, hour, 30), (3000.0 if unit == "W" else 3.0) * factor))
    _add(service, "sensor.lux_sued", "sensor", "measurement", "lx", lux_rows)
    _add(service, "sensor.pv", "sensor", "measurement", unit, pv_rows)
    _configure(service, temperature="sensor.t", illuminance="sensor.lux_sued", pv_power="sensor.pv")


def test_pv_compares_the_closed_days_with_the_learned_expectation(service) -> None:
    _pv_data(service, last_factor=0.5)
    data = service.compute_pv(NOW, days=3)
    rows = {row["date"][-2:]: row for row in data["days"]}
    assert sorted(rows) == ["17", "18", "19"] and rows["18"]["flagged"] is False
    assert rows["19"]["flagged"] is True and rows["19"]["deviation"] == pytest.approx(-50.0, abs=2.0)
    assert rows["19"]["actual"] == pytest.approx(12.0)  # acht Stunden zu 1,5 kW, die Lücke um 11 Uhr zählt mit
    assert data["window"] == 28 and data["limit"] == 15


def test_pv_needs_both_roles_and_a_power_unit(service) -> None:
    _pv_data(service)
    assert service.compute_pv(NOW, days=3) is not None
    _configure(service, temperature="sensor.t", illuminance="sensor.lux_sued")
    assert service.compute_pv(NOW, days=3) is None
    _add(service, "sensor.pv_wh", "sensor", "measurement", "%", [(_ts(19, 12), 50.0)])
    _configure(service, temperature="sensor.t", illuminance="sensor.lux_sued", pv_power="sensor.pv_wh")
    assert service.compute_pv(NOW, days=3) is None  # keine Leistungseinheit: nicht raten


def test_pv_card_needs_both_roles_and_a_route(client) -> None:
    from app.main import index

    config = wetter_routes._empty_config()
    assert not wetter_routes.card_available("pvlight", {**config, "pv_power": "sensor.pv"})
    assert wetter_routes.card_available("pvlight", {**config, "pv_power": "sensor.pv", "illuminance": "sensor.l"})
    index.set_setting(wetter_routes.SETTING_CONFIG, "")
    index.get_or_create_entity("sensor.wetter_test_temp5", "sensor", "measurement", "°C")
    assert client.post("/wetterdashboard/setup", data={"temperature": "sensor.wetter_test_temp5"}).status_code == 200
    assert client.get("/wetterdashboard/pv").status_code == 409  # keine PV-Rolle


def test_dayimage_falls_back_to_the_brightness_on_days_without_dawn_rows(service) -> None:
    """Der Dämmerungssensor ist oft erst kurz im Archiv: Tage davor zählen die hellen Stunden nach der Helligkeit selbst."""
    _add(service, "sensor.lux_sued", "sensor", "measurement", "lx", [(_ts(18, hour, 30), 80000.0) for hour in range(8, 17)])
    _add(service, "sensor.lux_daem", "sensor", "measurement", "lx", [(_ts(19, 9, 30), 1000.0), (_ts(19, 10, 30), 1000.0)])
    _configure(service, temperature="sensor.t", illuminance="sensor.lux_sued", illuminance_dawn="sensor.lux_daem")
    data = service.compute_dayimage(NOW, days=3)
    day18 = next(row for row in data["days"] if row["date"].endswith("18"))
    assert day18["class"] == "sunny" and day18["bright_hours"] >= 8


def test_sunhours_curve_marks_hours_above_the_threshold_and_holds_gaps(service) -> None:
    _add(service, "sensor.lux_sued", "sensor", "measurement", "lx", [
        (_ts(19, 8, 30), 10000.0), (_ts(19, 9, 30), 60000.0), (_ts(19, 10, 30), 98800.0),
        (_ts(19, 13, 30), 98800.0),  # 11 und 12 Uhr ohne Zeile: der Sensor hält seinen Wert
        (_ts(19, 14, 30), 12000.0),
    ])
    _configure(service, temperature="sensor.t", illuminance="sensor.lux_sued")
    data = service.compute_sunhours("2026-08-19", NOW)
    assert data["date"] == "2026-08-19" and data["threshold"] == 25000.0 and data["saturation"] == 98800
    by_hour = {round((row[0] - data["start"]) / 3600): row for row in data["hours"]}
    assert sorted(by_hour) == [8, 9, 10, 11, 12, 13, 14]
    assert by_hour[11][3] is True and by_hour[12][3] is True and by_hour[10][3] is False  # nur die Lücke ist „gehalten“
    assert data["sun_hours"] == pytest.approx(5.0)  # 9, 10, 11, 12, 13 Uhr
    assert data["peak"]["value"] == pytest.approx(98800.0)


def test_sunhours_without_values_and_without_role(service) -> None:
    assert service.compute_sunhours(None, NOW) is None
    _add(service, "sensor.lux_sued", "sensor", "measurement", "lx", [(_ts(10, 12), 5000.0)])
    _configure(service, temperature="sensor.t", illuminance="sensor.lux_sued")
    data = service.compute_sunhours("2026-08-19", NOW)
    assert data["hours"] == [] and data["sun_hours"] is None


def test_sunhours_card_and_route_through_the_app(client) -> None:
    from app.main import index

    index.set_setting(wetter_routes.SETTING_CONFIG, "")
    index.get_or_create_entity("sensor.sh_temp", "sensor", "measurement", "°C")
    index.get_or_create_entity("sensor.sh_lux", "sensor", "measurement", "lx")
    base = {"temperature": "sensor.sh_temp"}
    assert client.post("/wetterdashboard/setup", data=base).status_code == 200
    assert 'x-ref="sunChart"' not in client.get("/wetterdashboard").text
    assert client.get("/wetterdashboard/sonnenstunden").status_code == 409
    assert client.post("/wetterdashboard/setup", data={**base, "illuminance": "sensor.sh_lux"}).status_code == 200
    page = client.get("/wetterdashboard").text
    assert 'x-ref="sunChart"' in page and 'id="wt-tab-wind"' in page
    assert client.get("/wetterdashboard/sonnenstunden").json()["hours"] == []
    index.set_setting(wetter_routes.SETTING_CONFIG, "")


def test_dayimage_needs_the_illuminance_role_and_values(service) -> None:
    assert service.compute_dayimage(NOW) is None
    _add(service, "sensor.lux_sued", "sensor", "measurement", "lx", [(_ts(2, 12), 5000.0)])
    _configure(service, temperature="sensor.t", illuminance="sensor.lux_sued")
    assert service.compute_dayimage(NOW, days=5) is None  # nichts in den letzten fünf Tagen


def test_dayimage_card_and_route_through_the_app(client) -> None:
    from app.main import index

    index.set_setting(wetter_routes.SETTING_CONFIG, "")
    index.get_or_create_entity("sensor.tb_temp", "sensor", "measurement", "°C")
    index.get_or_create_entity("sensor.tb_lux", "sensor", "measurement", "lx")
    base = {"temperature": "sensor.tb_temp"}
    assert client.post("/wetterdashboard/setup", data=base).status_code == 200
    assert "data-wt-dayimage" not in client.get("/wetterdashboard").text
    assert client.post("/wetterdashboard/setup", data={**base, "illuminance": "sensor.tb_lux"}).status_code == 200
    assert "data-wt-dayimage" in client.get("/wetterdashboard").text
    assert client.get("/wetterdashboard/tagesbild").status_code == 409  # Rolle ohne Werte
    assert client.post("/wetterdashboard/setup", data={
        **base, "illuminance": "sensor.tb_lux", "cards_submitted": "1", "show_card": ["basis"],
    }).status_code == 200
    assert "data-wt-dayimage" not in client.get("/wetterdashboard").text
    index.set_setting(wetter_routes.SETTING_CONFIG, "")


def test_dayimage_detail_combines_the_light_curve_and_the_key_figures_of_the_day(service) -> None:
    _add(service, "sensor.lux_sued", "sensor", "measurement", "lx", [(_ts(18, hour, 30), 60000.0) for hour in range(9, 14)])
    _add(service, "sensor.t_aussen", "sensor", "measurement", "°C", [(_ts(18, 6, 30), 3.0), (_ts(18, 14, 30), 11.5)])
    _configure(service, temperature="sensor.t_aussen", illuminance="sensor.lux_sued")
    data = service.compute_dayimage_detail("2026-08-18", NOW)
    assert data["sun"]["date"] == "2026-08-18" and data["sun"]["sun_hours"] == pytest.approx(5.0)
    assert len(data["sun"]["hours"]) == 5 and data["sun"]["threshold"] == 25000.0
    assert data["conditions"]["date"] == "2026-08-18"
    assert data["conditions"]["temp"]["min"] == pytest.approx(3.0) and data["conditions"]["temp"]["max"] == pytest.approx(11.5)


def test_dayimage_detail_accepts_today_and_needs_the_illuminance_role(service) -> None:
    assert service.compute_dayimage_detail("2026-08-18", NOW) is None
    _add(service, "sensor.lux_sued", "sensor", "measurement", "lx", [(_ts(19, 9, 30), 60000.0)])
    _add(service, "sensor.t_aussen", "sensor", "measurement", "°C", [(_ts(19, 8, 30), 7.0)])
    _configure(service, temperature="sensor.t_aussen", illuminance="sensor.lux_sued")
    now = datetime(2026, 8, 19, 15, 0, tzinfo=TZ)
    today = now.date().isoformat()
    data = service.compute_dayimage_detail(today, now)
    assert data["sun"]["date"] == today and data["conditions"]["date"] == today  # heute zählt als Tag („bisher“)
    assert data["conditions"]["temp"]["min"] == pytest.approx(7.0)  # auch nur mit Temperatur gibt es Eckwerte


def test_dayimage_detail_route_and_dialog_markup(client) -> None:
    from app.main import index

    index.set_setting(wetter_routes.SETTING_CONFIG, "")
    index.get_or_create_entity("sensor.td_temp", "sensor", "measurement", "°C")
    index.get_or_create_entity("sensor.td_lux", "sensor", "measurement", "lx")
    base = {"temperature": "sensor.td_temp", "illuminance": "sensor.td_lux"}
    assert client.post("/wetterdashboard/setup", data=base).status_code == 200
    page = client.get("/wetterdashboard").text
    assert 'x-ref="dayDlg"' in page and 'openDay(cell)' in page and "pickDay" not in page
    assert client.get("/wetterdashboard/tagesbild/tag?date=2026-01-05").status_code == 200
    assert client.get("/wetterdashboard/tagesbild/tag?date=kaputt").status_code == 400
    index.set_setting(wetter_routes.SETTING_CONFIG, "")


def test_card_availability_follows_the_assigned_roles() -> None:
    config = wetter_routes._empty_config()
    assert not any(wetter_routes.card_available(key, config) for key in wetter_routes.CARD_KEYS)
    config.update(temperature="sensor.t", illuminance_dawn="sensor.d", heating=[{"entity_id": "sensor.h", "name": ""}])
    available = {key for key in wetter_routes.CARD_KEYS if wetter_routes.card_available(key, config)}
    assert available == {"basis", "monthly", "longterm", "conditions", "daylight", "heating"}  # ohne Helligkeit kein Tagesbild


def test_hidden_cards_are_saved_only_for_available_cards_and_hide_cards_and_tabs(client) -> None:
    from app.main import index

    index.set_setting(wetter_routes.SETTING_CONFIG, "")
    for entity, unit in (("sensor.karten_t", "°C"), ("sensor.karten_w", "km/h")):
        index.get_or_create_entity(entity, "sensor", "measurement", unit)
    base = {"temperature": "sensor.karten_t", "wind": "sensor.karten_w", "cards_submitted": "1"}
    page = client.get("/wetterdashboard/setup").text
    assert 'name="show_card"' in page and "cardAvailable(" in page
    # alles angehakt: nichts ausgeblendet
    shown = ["conditions", "basis", "monthly", "wind", "longterm", "daylight", "heating"]
    assert client.post("/wetterdashboard/setup", data={**base, "show_card": shown}).status_code == 200
    assert wetter_routes._load_config(index)["hidden_cards"] == []
    html = client.get("/wetterdashboard").text
    assert 'id="wt-tab-wind"' in html and 'id="wt-tab-long"' in html and "data-wt-conditions" in html
    # Wetterlage, Wind & Sonne, Langzeit abgewählt; Tageslicht (keine Rolle) und Heizen (kein Zähler) sind nicht wählbar
    assert client.post("/wetterdashboard/setup", data={**base, "show_card": ["basis", "monthly"]}).status_code == 200
    assert wetter_routes._load_config(index)["hidden_cards"] == ["conditions", "wind", "longterm"]
    html = client.get("/wetterdashboard").text
    assert 'id="wt-tab-wind"' not in html and 'id="wt-tab-long"' not in html and "data-wt-conditions" not in html
    assert 'id="wt-tab-overview"' in html
    # alles abgewählt: auch die Übersicht fehlt
    assert client.post("/wetterdashboard/setup", data={**base, "show_card": []}).status_code == 200
    assert 'id="wt-tab-overview"' not in client.get("/wetterdashboard").text
    index.set_setting(wetter_routes.SETTING_CONFIG, "")


# -- Sonne im Langzeit: Monatsvergleich, Kenntage, Rekorde, Matrix ---------------------------------


def _sun_longterm_data(service) -> None:
    _add(service, "sensor.t", "sensor", "measurement", "°C", [(_ts(d, 12), 15.0) for d in range(16, 20)])
    _dayimage_data(service)
    # Die Testdaten sind absteigend geschrieben, der Archivbeginn ist aber der älteste Tag.
    with service.deps.index._conn:  # noqa: SLF001
        service.deps.index._conn.execute(  # noqa: SLF001
            "UPDATE entities SET first_ts = ? WHERE entity_id IN ('sensor.lux_sued', 'sensor.lux_daem')", (_ts(16, 7, 30),),
        )


def test_sun_history_lists_every_closed_day_since_the_archive_start(service) -> None:
    _sun_longterm_data(service)
    days = service.sun_history(wetter_routes._load_config(service.deps.index), NOW)
    assert [(entry["date"].day, entry["class"]) for entry in days] == [
        (16, None), (17, "mixed"), (18, "overcast"), (19, "sunny"),
    ]
    assert days[3]["hours"] == pytest.approx(9.0) and days[0]["hours"] == pytest.approx(0.0)


def test_monthly_comparison_offers_the_sun_hours(service) -> None:
    _sun_longterm_data(service)
    data = service.compute_monthly("sun", "mm", NOW)
    assert data["metric"] == "sun" and data["unit"] == "h" and data["headline"] is None
    assert data["years"]["2026"][7] == pytest.approx(12.0)  # 9 sonnig + 3 wechselhaft
    assert service.compute_monthly("sun", "mm", NOW) is not None
    service.deps.index.set_setting(wetter_routes.SETTING_CONFIG, "")
    assert service.compute_monthly("sun", "mm", NOW) is None


def test_year_matrix_shows_the_day_classes(service) -> None:
    _sun_longterm_data(service)
    data = service.compute_matrix("sun", "mm", NOW)
    assert data["metric"] == "sun" and data["years"] == [2026]
    by_slot = {cell[0]: cell[2] for cell in data["cells"]}
    slot = analysis_mod.matrix_slot
    assert by_slot[slot(date(2026, 8, 19))] == 2 and by_slot[slot(date(2026, 8, 18))] == 0
    assert by_slot[slot(date(2026, 8, 17))] == 1
    assert [slot(date(2026, 8, 16)), 0] in data["missing"]  # zu kurzer Tag: grau, nicht trüb
    assert data["hours"]["2026-08-19"] == pytest.approx(9.0) and "2026-08-16" not in data["hours"]


def test_longterm_carries_the_sun_kenntage(service) -> None:
    _sun_longterm_data(service)
    data = service.compute_longterm(NOW)
    month = data["sun_kenntage"]["2026"]
    assert month["sunny"][7] == 1 and month["overcast"][7] == 1 and month["days"][7] == 3
    assert not any(entry["key"].startswith(("sunniest", "longest")) for entry in data["records"])  # Archiv zu kurz


def _sun_days(classes: list[str | None], hours: float = 6.0) -> list[dict]:
    start = date(2025, 1, 1)
    return [
        {"date": start + timedelta(days=i), "class": kind, "share": None, "hours": hours if kind else None, "bright": 9}
        for i, kind in enumerate(classes)
    ]


def test_sun_kenntage_count_only_sunny_and_overcast_days() -> None:
    days = _sun_days(["sunny", "mixed", "overcast", None, "sunny"])
    assert analysis_mod.sun_kenntage(days) == {"2025": {
        "sunny": [2] + [0] * 11, "overcast": [1] + [0] * 11, "days": [4] + [0] * 11,
    }}


def test_sun_records_find_the_longest_runs_and_the_sunniest_month() -> None:
    kinds = (["sunny"] * 2 + ["overcast"] * 3 + ["sunny"] * 4 + [None] + ["sunny"] * 5) + ["mixed"] * 360
    days = _sun_days(kinds)
    days[3]["hours"] = 11.5
    monthly = {"years": {"2025": [200.0, 310.5] + [None] * 10}}
    out = analysis_mod.sun_records(days, monthly, date(2026, 6, 1))
    assert out["sunniest_day"] == {"value": 11.5, "date": "2025-01-04"}
    assert out["sunniest_month"] == {"value": 310.5, "date": "2025-02-01"}
    assert out["longest_sunny_streak"] == {"value": 5, "date": "2025-01-11", "end": "2025-01-15"}
    assert out["longest_overcast_streak"] == {"value": 3, "date": "2025-01-03", "end": "2025-01-05"}
    assert analysis_mod.sun_records(days[:100], monthly, date(2026, 6, 1)) is None  # kein Jahr Werte


def test_sun_records_skip_the_running_month() -> None:
    days = _sun_days(["sunny"] * 400)
    out = analysis_mod.sun_records(days, {"years": {"2026": [None] * 5 + [999.0] + [None] * 6, "2025": [100.0] + [None] * 11}}, date(2026, 6, 10))
    assert out["sunniest_month"]["date"] == "2025-01-01"


def test_longterm_page_offers_the_sun_views_only_with_the_illuminance_role(client) -> None:
    from app.main import index

    index.set_setting(wetter_routes.SETTING_CONFIG, "")
    index.get_or_create_entity("sensor.sl_temp", "sensor", "measurement", "°C")
    index.get_or_create_entity("sensor.sl_lux", "sensor", "measurement", "lx")
    base = {"temperature": "sensor.sl_temp"}
    assert client.post("/wetterdashboard/setup", data=base).status_code == 200
    page = client.get("/wetterdashboard").text
    assert "setMatrixMetric('sun')" not in page and "setMonthlyMetric('sun')" not in page
    assert client.post("/wetterdashboard/setup", data={**base, "illuminance": "sensor.sl_lux"}).status_code == 200
    page = client.get("/wetterdashboard").text
    assert "setMatrixMetric('sun')" in page and "setMonthlyMetric('sun')" in page
    assert 'x-ref="kenntageChartSunny"' in page and 'x-ref="kenntageChartOvercast"' in page
    assert client.get("/wetterdashboard/monatsvergleich?metric=sun").status_code in (200, 404)
    assert client.get("/wetterdashboard/matrix?metric=wolke").status_code == 400
    index.set_setting(wetter_routes.SETTING_CONFIG, "")


def test_humidity_extras_get_their_own_series_apart_from_the_temperatures(service) -> None:
    _add(service, "sensor.t", "sensor", "measurement", "°C", [(_ts(19, 12), 10.0)])
    _add(service, "sensor.n", "sensor", "measurement", "°C", [(_ts(19, 12), 8.0)])
    _add(service, "sensor.f", "sensor", "measurement", "%", [(_ts(19, 12), 60.0)])
    _configure(
        service, temperature="sensor.t",
        extra_temperatures=[{"entity_id": "sensor.n", "name": "Nord"}, {"entity_id": "sensor.f", "name": "Keller feucht"}],
    )
    data = service.compute_temperatures(7, NOW)
    assert [row["label"] for row in data["series"]] == ["Temperatur", "Nord"]
    assert [row["label"] for row in data["humidity"]] == ["Keller feucht"]
    assert all(row["unit"] == "%" for row in data["humidity"])


def test_sky_limits_form_fills_missing_fields_and_rejects_unordered_ones(client) -> None:
    from app.main import index

    index.get_or_create_entity("sensor.wetter_test_temp6", "sensor", "measurement", "°C")
    index.set_setting(wetter_routes.SETTING_CONFIG, "")
    base = {"temperature": "sensor.wetter_test_temp6", "sun_threshold_lx": "25000"}
    assert client.post("/wetterdashboard/setup", data={**base, "sky_sunny_lx": "90000"}).status_code == 200
    assert wetter_routes._load_config(index)["sky_cuts_lx"] == [15000.0, 25000.0, 90000.0]  # leere Felder: automatisch
    assert client.post("/wetterdashboard/setup", data={**base, "sky_cloudy_lx": "30000", "sky_fair_lx": "20000"}).status_code == 400
    assert wetter_routes._load_config(index)["sky_cuts_lx"] == [15000.0, 25000.0, 90000.0]  # abgelehnt, nichts überschrieben
    assert client.post("/wetterdashboard/setup", data=base).status_code == 200
    assert wetter_routes._load_config(index)["sky_cuts_lx"] == []


def test_sky_limits_form_with_an_extreme_threshold_does_not_reject_empty_fields(client) -> None:
    """Die automatischen Grenzen (0,6x / 1x / 2,8x der Sonnenschwelle) liegen bei 1000 lx oder 50000 lx außerhalb des
    erlaubten Bereichs; ein leeres Feld lieferte dann einen 400 mit leerem Wert statt den Rand zu nehmen."""
    from app.main import index

    index.get_or_create_entity("sensor.wetter_test_temp7", "sensor", "measurement", "°C")
    index.set_setting(wetter_routes.SETTING_CONFIG, "")
    base = {"temperature": "sensor.wetter_test_temp7"}
    assert client.post("/wetterdashboard/setup", data={**base, "sun_threshold_lx": "50000", "sky_cloudy_lx": "20000"}).status_code == 200
    assert wetter_routes._load_config(index)["sky_cuts_lx"] == [20000.0, 50000.0, 100000.0]  # 2,8 x 50000 -> Obergrenze
    index.set_setting(wetter_routes.SETTING_CONFIG, "")


def test_pressure_trend_needs_a_known_unit(service) -> None:
    """Die Tendenz rechnet in hPa um. Eine unbekannte Einheit (hier bar) gab früher eine Tendenz aus der Rohdifferenz,
    als wäre es hPa; jetzt gibt es keine. Eine fehlende oder anders geschriebene bekannte Einheit zählt weiter."""
    trends: dict[str, object] = {}
    for entity, unit, first, last in (("sensor.p_bar", "bar", 1.000, 1.020), ("sensor.p_low", "hpa", 1000.0, 1004.0), ("sensor.p_none", "", 1000.0, 1004.0)):
        _add(service, entity, "sensor", "measurement", unit, [(_ts(19, 6), first), (_ts(19, 18), last)])
        _configure(service, pressure=entity)
        data = service.compute_conditions_day("2026-08-19", NOW)
        trends[entity] = data["pressure"]["trend"] if data and data.get("pressure") else "fehlt"
    assert trends == {"sensor.p_bar": None, "sensor.p_low": "rising", "sensor.p_none": "rising"}
