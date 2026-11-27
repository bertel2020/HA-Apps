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
from datetime import datetime
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
