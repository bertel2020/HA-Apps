"""Mehrjahresvergleich im Chart-Editor ("Jahre" neben Laufsumme/Soll):
query_previous_years() in app/storage/query.py, der Parameter previous_years
von /api/query-multi und die Anbindung in chart_editor.js/.html."""

from __future__ import annotations

import shutil
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pyarrow as pa
import pyarrow.parquet as pq

from _paths import APP, TEMPLATES
from app.storage import query, rollup
from app.storage.index import Index

TZ = ZoneInfo("Europe/Berlin")
NOW = datetime(2026, 10, 6, 18, 0, 0, tzinfo=TZ)
EDITOR_JS = (APP / "static" / "js" / "pages" / "chart_editor.js").read_text(encoding="utf-8")
EDITOR_TEMPLATE = (TEMPLATES / "chart_editor.html").read_text(encoding="utf-8")


def _archive_counter_year(tmp: Path, entity_id: str, year: int) -> None:
    """Ein Jahr Zähler mit 10 kWh je Tag (08 Uhr +3, 20 Uhr +7), samt Rollups —
    dieselbe Bauart wie _archive_counter_year() in test_query.py."""
    archive_dir = tmp / "archive" / entity_id
    archive_dir.mkdir(parents=True, exist_ok=True)
    value = 0.0
    day = datetime(year, 1, 1, tzinfo=TZ)
    end = datetime(year + 1, 1, 1, tzinfo=TZ)
    months: dict[int, tuple[list[float], list[float]]] = {}
    while day < end:
        ts_list, value_list = months.setdefault(day.month, ([], []))
        for hour, step in ((8, 3.0), (20, 7.0)):
            value += step
            ts_list.append(day.replace(hour=hour).timestamp())
            value_list.append(value)
        day += timedelta(days=1)
    for month, (ts_list, value_list) in months.items():
        table = pa.table({"ts": ts_list, "value": value_list})
        pq.write_table(table, archive_dir / f"{year}-{month:02d}.parquet")
        rollup.append_completed_month(tmp, entity_id, "counter", table, year, month, TZ)


def _setup(years: list[int]) -> tuple[Path, Index, str]:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-multi-year-test-"))
    index = Index(tmp / "index.sqlite")
    entity_id = "sensor.ertrag"
    index.get_or_create_entity(entity_id, "sensor", "total_increasing", "kWh")
    for year in years:
        _archive_counter_year(tmp, entity_id, year)
    index.record_write(entity_id, datetime(min(years), 1, 1, 8, tzinfo=TZ).timestamp())
    return tmp, index, entity_id


def test_previous_years_newest_first_and_years_without_data_left_out() -> None:
    """2024 hat keine Daten: es fehlt ganz (Lücken ausblenden), statt als leere
    Linie oder mit Nullwerten zu erscheinen."""
    tmp, index, entity_id = _setup([2022, 2023, 2025])
    try:
        result = query.query_previous_years(tmp, index, entity_id, TZ, NOW, offset=0, years=0)
        assert [entry["year"] for entry in result] == [2025, 2023, 2022]
    finally:
        index.close()
        shutil.rmtree(tmp, ignore_errors=True)


def test_previous_years_counts_back_from_the_shown_year_including_gaps() -> None:
    """"5 Jahre" heißt 2021–2025, auch wenn davon nur einige Daten haben — eine
    Lücke verschiebt das Fenster nicht weiter in die Vergangenheit."""
    tmp, index, entity_id = _setup([2019, 2023, 2025])
    try:
        two = query.query_previous_years(tmp, index, entity_id, TZ, NOW, offset=0, years=2)
        assert [entry["year"] for entry in two] == [2025]
        five = query.query_previous_years(tmp, index, entity_id, TZ, NOW, offset=0, years=5)
        assert [entry["year"] for entry in five] == [2025, 2023]
    finally:
        index.close()
        shutil.rmtree(tmp, ignore_errors=True)


def test_previous_year_values_match_the_existing_previous_year_compare() -> None:
    """Dieselben Monatswerte wie der bestehende Vorjahresvergleich (offset -1),
    und cut_points enden am selben Tag wie query_compare_cut()."""
    tmp, index, entity_id = _setup([2025])
    try:
        (entry,) = query.query_previous_years(tmp, index, entity_id, TZ, NOW, offset=0, years=0)
        previous = query.query_series(tmp, index, entity_id, "year", TZ, NOW, offset=-1)
        assert [(p["ts"], p["value"]) for p in entry["points"]] == [(p["ts"], p["value"]) for p in previous["points"]]

        cut = query.query_compare_cut(
            tmp, index, entity_id, "year", TZ, NOW, offset=0, continuous=False, year_over_year=False,
        )
        assert sum(p["value"] for p in entry["cut_points"]) == sum(p["value"] for p in cut["points"])
        assert sum(p["value"] for p in entry["cut_points"]) == 7.0 + 277 * 10.0 + 3.0
    finally:
        index.close()
        shutil.rmtree(tmp, ignore_errors=True)


def test_a_past_shown_year_has_no_cut() -> None:
    """Ist das angezeigte Jahr schon abgeschlossen, gibt es kein "bis zum
    gleichen Tag" — die Vorjahre werden ganz verglichen."""
    tmp, index, entity_id = _setup([2023, 2024])
    try:
        result = query.query_previous_years(tmp, index, entity_id, TZ, NOW, offset=-1, years=0)
        assert [entry["year"] for entry in result] == [2024, 2023]
        assert all(entry["cut_points"] is None for entry in result)
    finally:
        index.close()
        shutil.rmtree(tmp, ignore_errors=True)


def test_unknown_entity_or_entity_without_first_value_gives_no_years() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-multi-year-test-"))
    index = Index(tmp / "index.sqlite")
    try:
        assert query.query_previous_years(tmp, index, "sensor.gibt_es_nicht", TZ, NOW, offset=0, years=0) == []
        index.get_or_create_entity("sensor.leer", "sensor", "measurement", "°C")
        assert query.query_previous_years(tmp, index, "sensor.leer", TZ, NOW, offset=0, years=0) == []
    finally:
        index.close()
        shutil.rmtree(tmp, ignore_errors=True)


def test_api_returns_years_only_for_previous_year_compare_of_one_calendar_year(client) -> None:
    """previous_years wirkt nur dort, wo das Feld "Jahre" erscheint: Vorjahr,
    Zeitraum Jahr, kalendarisch, genau eine Entität. previous_years=1 ist der
    bisherige Vorjahresvergleich und ändert an der Antwort nichts."""
    ghost = "sensor.gibt_es_nicht"
    base = f"/api/query-multi?entity_ids={ghost}&range=year&offset=0&compare=true&compare_mode=previous"

    with_years = client.get(base + "&previous_years=5").json()["series"][0]
    assert with_years["years"] == []

    for url in (
        base,
        base + "&previous_years=1",
        base + "&previous_years=5&continuous=true",
        f"/api/query-multi?entity_ids={ghost}&range=month&offset=0&compare=true&previous_years=5",
        f"/api/query-multi?entity_ids={ghost},sensor.auch_nicht&range=year&offset=0&compare=true&previous_years=5",
    ):
        response = client.get(url)
        assert response.status_code == 200, (url, response.text)
        assert all("years" not in s for s in response.json()["series"]), url


def test_api_rejects_negative_previous_years(client) -> None:
    response = client.get(
        "/api/query-multi?entity_ids=sensor.gibt_es_nicht&range=year&offset=0&compare=true&previous_years=-1"
    )
    assert response.status_code == 400


def test_multi_year_fields_sit_in_the_previous_year_row() -> None:
    """Kein dritter Eintrag im Vergleichen-Menü (siehe
    test_years_compare_is_not_a_separate_menu_option): "Jahre" und "Ø-Linie"
    stehen in derselben Zeile wie Laufsumme/Soll, die ab 2 Jahren verschwinden."""
    zeile = EDITOR_TEMPLATE.split(
        'x-if="compare && compareMode === \'previous\' && range === \'year\'"', 1
    )[1].split('<div class="card">', 1)[0]
    assert 'x-show="multiYearAvailable"' in zeile
    assert "setCompareYears(opt.value)" in zeile
    assert 'x-show="multiYearRequested"' in zeile
    assert zeile.count('x-show="!multiYearRequested"') == 2


def test_multi_year_options_default_to_the_existing_previous_year() -> None:
    options = EDITOR_JS.split("const COMPARE_YEARS_OPTIONS = [", 1)[1].split("];", 1)[0]
    assert [line.strip().split("value: ", 1)[1].split(",", 1)[0] for line in options.splitlines() if "value:" in line] == [
        "1", "2", "5", "10", "0",
    ]
    assert "compareYears: 1," in EDITOR_JS


def test_switching_to_several_years_resets_running_total_and_target_entity() -> None:
    fn = EDITOR_JS.split("setCompareYears(value) {", 1)[1].split("\n        },", 1)[0]
    assert "this.compareCumsum = false;" in fn
    assert "this.compareSollEntityId = '';" in fn
    assert "if (this.sollAutoAdded)" in fn
    assert "this.load();" in fn


def test_multi_year_render_path_comes_before_the_existing_ones() -> None:
    """Der bestehende Renderpfad (renderYearsCompare) bleibt unverändert; der
    neue sitzt davor und greift nur, wenn die Vorjahre geladen sind."""
    render = EDITOR_JS.split("        render() {", 1)[1]
    assert render.index("this.renderMultiYear()") < render.index("this.renderYearsCompare()")
    assert "Array.isArray(this.series[0].years)" in EDITOR_JS
    fn = EDITOR_JS.split("renderMultiYear() {", 1)[1].split("\n        },", 1)[0]
    assert "type: 'bar'" not in fn  # immer Linien


def test_legend_style_stays_available_and_year_table_is_added() -> None:
    """Legenden-Stil Chips/Tabelle funktioniert im Mehrjahresmodus wie sonst;
    die Tabelle Jahr × Monat kommt zusätzlich darunter."""
    assert "x-for=\"stat in multiYearSeriesStats\"" in EDITOR_TEMPLATE
    assert EDITOR_TEMPLATE.count("x-for=\"stat in multiYearSeriesStats\"") == 2
    assert "multiYearActive && (legendStyle === 'chips' || !chartStats)" in EDITOR_TEMPLATE
    assert "legendStyle === 'table' && multiYearActive" in EDITOR_TEMPLATE
    assert 'class="dt compact multi-year-table"' in EDITOR_TEMPLATE
