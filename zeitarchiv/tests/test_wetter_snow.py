"""Wetterdashboard: Tagesreihe des Schnees aus dem Wetterzustand (ROADMAP 1.75, Schritt 1).

Geprüft wird die Datengrundlage der Schneefalltage:

- Sekunden mit Schnee und Sekunden mit bekanntem Zustand je Ortstag, aus den Stunden-Rollups.
- Schnee ist ``snowy`` und ``snowy-rainy``; Regen, Hagel und alles andere zählen nicht.
- „Nicht verfügbar“ zählt weder als Schnee noch als bekannte Zeit.
- Der laufende Tag fehlt, Tage vor dem Archivbeginn der Entität auch.
- Ein Tag der Zeitumstellung bleibt vollständig bei seinem Datum.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

import _paths  # noqa: F401
from app import wetter_analysis as analysis
from app import wetter_routes
from app.storage.index import STATE_UNAVAILABLE_INDEX, Index
from app.storage.ingestion import IngestEvent, IngestionService
from app.wetter_routes import WetterDependencies, WetterService

TZ = ZoneInfo("Europe/Berlin")
ENTITY = "weather.lage"
DAY = 86400.0
HOUR = 3600.0


def _ts(*args: int) -> float:
    return datetime(*args, tzinfo=TZ).timestamp()


def _state(state: str, ts: float, entity_id: str = ENTITY) -> IngestEvent:
    return IngestEvent(
        event_id=uuid.uuid4().hex, entity_id=entity_id, domain="weather", ts=ts, value=None, state=state,
    )


@pytest.fixture
def setup(tmp_path: Path):
    index = Index(tmp_path / "index.sqlite")
    service = WetterService(WetterDependencies(
        data_dir=tmp_path, index=index, tz=TZ, templates=None, app_root_context=lambda request: {},
    ))
    try:
        yield service, IngestionService(tmp_path, index, TZ), index
    finally:
        index.close()


def _configure(index: Index, **roles) -> dict:
    wetter_routes._save_config(index, {**wetter_routes._empty_config(), **roles})
    return wetter_routes._load_config(index)


def _days(history: list[dict]) -> dict[str, dict]:
    return {entry["date"].isoformat(): entry for entry in history}


def test_day_totals_count_snow_and_known_time_per_local_day() -> None:
    midnight = _ts(2026, 12, 1)
    hours = {
        midnight + 23 * HOUR - HOUR: {0: HOUR},  # Wolken, 22 bis 23 Uhr
        midnight + 23 * HOUR: {1: 1800.0, 2: 1800.0},  # Schnee, dann Schneeregen
        midnight + 24 * HOUR: {1: HOUR, STATE_UNAVAILABLE_INDEX: 0.0},  # nächster Tag
    }

    totals = analysis.snow_day_totals(hours, {1, 2}, STATE_UNAVAILABLE_INDEX, TZ)

    assert [(t["date"].isoformat(), t["snow_s"], t["known_s"]) for t in totals] == [
        ("2026-12-01", 3600.0, 7200.0), ("2026-12-02", 3600.0, 3600.0),
    ]


def test_snow_history_reads_days_from_the_hour_rollups(setup) -> None:
    service, ingest, index = setup
    for text, ts in (
        ("cloudy", _ts(2026, 11, 10, 6)),
        ("snowy", _ts(2026, 11, 10, 20)),  # über Mitternacht
        ("cloudy", _ts(2026, 11, 11, 2)),
        ("snowy-rainy", _ts(2026, 11, 20, 8)),
        ("rainy", _ts(2026, 11, 20, 8, 30)),
        ("hail", _ts(2026, 11, 30, 12)),
        ("cloudy", _ts(2026, 11, 30, 13)),
        ("cloudy", _ts(2026, 12, 1, 8)),  # rotiert den November ins Archiv
    ):
        ingest.ingest(_state(text, ts))
    config = _configure(index, weather_state=ENTITY)

    history = service.snow_history(config, datetime(2026, 12, 15, 12, tzinfo=TZ))

    days = _days(history)
    # Der Archivbeginn ist 6 Uhr am 10.11.: davor gibt es keine Zeit, und es gibt keinen Tag davor.
    assert history[0]["date"].isoformat() == "2026-11-10"
    assert days["2026-11-10"]["known_s"] == pytest.approx(18 * HOUR)
    assert days["2026-11-10"]["snow_s"] == pytest.approx(4 * HOUR)
    assert days["2026-11-11"]["snow_s"] == pytest.approx(2 * HOUR)
    assert days["2026-11-11"]["known_s"] == pytest.approx(DAY)
    assert days["2026-11-20"]["snow_s"] == pytest.approx(1800.0)  # Schneeregen zählt als Schnee
    assert days["2026-11-30"]["snow_s"] == 0.0  # Hagel nicht
    assert days["2026-11-15"]["snow_s"] == 0.0 and days["2026-11-15"]["known_s"] == pytest.approx(DAY)  # Monatsmitte
    # Der laufende Tag fehlt, der Vortag steht drin.
    assert history[-1]["date"].isoformat() == "2026-12-14"


def test_unavailable_time_counts_neither_as_snow_nor_as_known(setup) -> None:
    service, ingest, index = setup
    for text, ts in (
        ("cloudy", _ts(2026, 11, 24, 12)),
        ("unavailable", _ts(2026, 11, 25, 10)),
        ("cloudy", _ts(2026, 11, 26, 10)),
        ("cloudy", _ts(2026, 12, 1, 8)),
    ):
        ingest.ingest(_state(text, ts))
    config = _configure(index, weather_state=ENTITY)

    days = _days(service.snow_history(config, datetime(2026, 12, 2, 12, tzinfo=TZ)))

    assert days["2026-11-25"]["known_s"] == pytest.approx(10 * HOUR)
    assert days["2026-11-26"]["known_s"] == pytest.approx(14 * HOUR)
    assert days["2026-11-25"]["snow_s"] == 0.0


def test_a_daylight_saving_day_stays_whole(setup) -> None:
    service, ingest, index = setup
    for text, ts in (
        ("cloudy", _ts(2026, 10, 24, 12)),
        ("snowy", _ts(2026, 10, 25)),  # Umstellung auf Winterzeit: 25 Stunden
        ("cloudy", _ts(2026, 10, 26)),
        ("cloudy", _ts(2026, 11, 2, 8)),
    ):
        ingest.ingest(_state(text, ts))
    config = _configure(index, weather_state=ENTITY)

    days = _days(service.snow_history(config, datetime(2026, 11, 3, 12, tzinfo=TZ)))

    assert days["2026-10-25"]["snow_s"] == pytest.approx(25 * HOUR)
    assert days["2026-10-25"]["known_s"] == pytest.approx(25 * HOUR)
    assert days["2026-10-26"]["snow_s"] == 0.0


def test_no_role_no_entity_or_a_numeric_entity_gives_no_days(setup) -> None:
    service, ingest, index = setup
    now = datetime(2026, 12, 15, 12, tzinfo=TZ)
    ingest.ingest(_state("snowy", _ts(2026, 11, 10)))
    ingest.ingest(_state("cloudy", _ts(2026, 12, 1, 8)))

    assert service.snow_history(_configure(index), now) == []
    assert service.snow_history(_configure(index, weather_state="weather.gibt_es_nicht"), now) == []

    index.get_or_create_entity("sensor.t", "sensor", "measurement", "°C")
    assert service.snow_history(_configure(index, weather_state="sensor.t"), now) == []


def test_an_entity_that_started_today_has_no_completed_day(setup) -> None:
    service, ingest, index = setup
    ingest.ingest(_state("snowy", _ts(2026, 12, 15, 6)))
    config = _configure(index, weather_state=ENTITY)

    assert service.snow_history(config, datetime(2026, 12, 15, 12, tzinfo=TZ)) == []


# -- Auswertung (Schritt 2) ------------------------------------------------------------------

MIN_S = 600.0  # 10 Minuten, die Einstellung der Regentage


def _day(day: date, snow_min: float = 0.0, known_h: float = 24.0) -> dict:
    return {"date": day, "snow_s": snow_min * 60.0, "known_s": known_h * HOUR}


def _run(start: date, snow_flags: list[bool]) -> list[dict]:
    return [_day(start + timedelta(days=i), 120.0 if flag else 0.0) for i, flag in enumerate(snow_flags)]


def test_the_minimum_duration_decides_what_is_a_snowfall_day() -> None:
    days = [_day(date(2026, 12, 1), 5), _day(date(2026, 12, 2), 10), _day(date(2026, 12, 3), 120)]

    result = analysis.snow_kenntage(days, MIN_S)

    assert result == {"2026": {"snow": [0] * 11 + [2], "days": [0] * 11 + [3]}}


def test_a_gap_in_the_state_is_no_day_without_snow_but_snow_in_it_still_counts() -> None:
    days = [
        _day(date(2026, 12, 1), 0, known_h=10),  # Ausfall, kein Beleg für „kein Schnee“
        _day(date(2026, 12, 2), 0, known_h=18),  # genug bekannt
        _day(date(2026, 12, 3), 30, known_h=2),  # Schnee ist ein positiver Beleg
    ]

    december = analysis.snow_kenntage(days, MIN_S)["2026"]

    assert december["days"][11] == 2 and december["snow"][11] == 1


def test_seasons_run_from_july_to_june_and_list_the_newest_first() -> None:
    days = (
        _run(date(2026, 6, 28), [True, False, False])  # 28. bis 30.6. gehören noch zu 2025/26
        + _run(date(2026, 11, 10), [True, True, False, True])
        + _run(date(2026, 12, 1), [False])
    )

    seasons = analysis.snow_seasons(days, MIN_S, date(2026, 12, 15))

    assert [s["season"] for s in seasons] == ["2026/27", "2025/26"]
    running, finished = seasons
    assert running["running"] is True and finished["running"] is False
    assert (running["first"], running["last"], running["snow_days"], running["days"]) == (
        "2026-11-10", "2026-11-13", 3, 5)
    assert running["longest"] == {"value": 2, "date": "2026-11-10", "end": "2026-11-11"}
    assert (finished["first"], finished["snow_days"]) == ("2026-06-28", 1)


def test_a_season_without_snow_has_no_dates() -> None:
    seasons = analysis.snow_seasons(_run(date(2026, 11, 1), [False, False]), MIN_S, date(2026, 12, 15))

    assert seasons[0]["first"] is None and seasons[0]["last"] is None
    assert seasons[0]["snow_days"] == 0 and seasons[0]["longest"] is None
    assert analysis.snow_records(_run(date(2026, 11, 1), [False, False]), MIN_S, date(2026, 12, 15)) is None


def test_a_streak_runs_over_a_month_and_year_change_and_breaks_at_a_missing_day() -> None:
    days = _run(date(2025, 12, 30), [True, True, True, True])  # bis 2.1.
    days += [_day(date(2026, 1, 4), 120.0), _day(date(2026, 1, 5), 120.0)]  # der 3.1. fehlt

    seasons = analysis.snow_seasons(days, MIN_S, date(2026, 6, 1))

    assert seasons[0]["longest"] == {"value": 4, "date": "2025-12-30", "end": "2026-01-02"}


def test_records_pick_the_earliest_first_and_the_latest_last_snow_of_finished_seasons() -> None:
    days = (
        _run(date(2024, 12, 5), [True]) + _run(date(2025, 4, 9), [True])  # 2024/25: spät erster, spät letzter
        + _run(date(2025, 11, 12), [True, True, True]) + _run(date(2026, 3, 29), [True])  # 2025/26
        + _run(date(2026, 10, 20), [True])  # laufend, aber früh
    )

    records = analysis.snow_records(days, MIN_S, date(2026, 11, 27))

    assert records["earliest_first_snow"] == {"season": "2026/27", "date": "2026-10-20"}
    assert records["latest_last_snow"] == {"season": "2024/25", "date": "2025-04-09"}
    assert records["longest_snow_streak"]["value"] == 3
    assert records["longest_snow_streak"]["ongoing"] is False


def test_the_running_season_gives_no_latest_last_snow_and_a_streak_to_yesterday_is_ongoing() -> None:
    days = _run(date(2026, 11, 25), [True, True])  # 25. und 26.11., heute der 27.

    records = analysis.snow_records(days, MIN_S, date(2026, 11, 27))

    assert "latest_last_snow" not in records
    assert records["longest_snow_streak"] == {"value": 2, "date": "2026-11-25", "end": "2026-11-26", "ongoing": True}


# -- Route (Schritt 3) -----------------------------------------------------------------------

def _with_temperature(ingest: IngestionService, index: Index, *days: datetime) -> None:
    for day in days:
        ingest.ingest(IngestEvent(
            event_id=uuid.uuid4().hex, entity_id="sensor.t", domain="sensor", ts=day.timestamp(), value=1.0,
            unit="°C", state_class="measurement",
        ))


def _longterm(setup, changes, now=datetime(2026, 12, 15, 12, tzinfo=TZ), **roles) -> dict:
    service, ingest, index = setup
    _with_temperature(ingest, index, datetime(2026, 11, 10, 12, tzinfo=TZ), datetime(2026, 12, 14, 12, tzinfo=TZ))
    for text, ts in changes:
        ingest.ingest(_state(text, ts))
    _configure(index, temperature="sensor.t", **roles)
    return service.compute_longterm(now)


WINTER = (
    ("cloudy", _ts(2026, 11, 10, 6)),
    ("snowy", _ts(2026, 11, 10, 20)),
    ("cloudy", _ts(2026, 11, 11, 2)),
    ("snowy-rainy", _ts(2026, 11, 20, 8)),
    ("cloudy", _ts(2026, 11, 20, 9)),
    ("cloudy", _ts(2026, 12, 1, 8)),
)


def test_longterm_carries_snow_days_seasons_and_records(setup) -> None:
    result = _longterm(setup, WINTER, weather_state=ENTITY)

    assert result["snow_enabled"] is True
    assert result["snow_since"] == "2026-11-10"
    assert result["snow_kenntage"]["2026"]["snow"][10] == 3  # 10., 11. und 20.11.
    season = result["snow_seasons"][0]
    assert (season["season"], season["first"], season["last"], season["snow_days"]) == (
        "2026/27", "2026-11-10", "2026-11-20", 3)
    records = {entry["key"]: entry for entry in result["records"]}
    assert records["longest_snow_streak"]["value"] == 2 and records["longest_snow_streak"]["ongoing"] is False
    assert records["earliest_first_snow"]["season"] == "2026/27"
    assert "latest_last_snow" not in records  # die einzige Saison läuft noch
    assert records["longest_snow_streak"]["entity_id"] == ENTITY


def test_longterm_uses_the_rain_day_minutes_as_the_minimum_duration(setup) -> None:
    changes = (*WINTER[:3], ("snowy", _ts(2026, 11, 20, 8)), ("cloudy", _ts(2026, 11, 20, 8, 5)), WINTER[-1])

    short = _longterm(setup, changes, weather_state=ENTITY, rain_day_minutes=10)

    assert short["snow_kenntage"]["2026"]["snow"][10] == 2  # fünf Minuten am 20.11. reichen nicht
    assert short["snow_min_minutes"] == 10


def test_longterm_without_the_role_has_no_snow(setup) -> None:
    result = _longterm(setup, WINTER)

    assert result["snow_enabled"] is False
    assert result["snow_kenntage"] is None and result["snow_seasons"] == []
    assert not [entry for entry in result["records"] if "snow" in entry["key"]]


def test_longterm_with_a_fresh_entity_only_says_since_when(setup) -> None:
    result = _longterm(setup, [("snowy", _ts(2026, 12, 15, 6))], weather_state=ENTITY)

    assert result["snow_enabled"] is True and result["snow_since"] == "2026-12-15"
    assert result["snow_kenntage"] is None and result["snow_seasons"] == []
