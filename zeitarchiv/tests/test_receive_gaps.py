"""Empfangs-Lücken: Pausen zwischen empfangenen Werten werden beim Eintreffen festgehalten, unabhängig davon, ob der
Wert gespeichert oder verworfen wurde (Wertänderungsfilter, Auflösung). Grundlage der Lücken-Erkennung bei Entitäten,
deren Speicher nicht mehr der Lieferung entspricht; der Heartbeat des Filters spielt dabei keine Rolle."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import pytest

from app.storage.index import (
    MAX_RECEIVE_GAPS_PER_ENTITY,
    MIN_RECEIVE_GAP_SECONDS,
    Index,
    gap_method,
)

ENTITY = "sensor.empfang"


@pytest.fixture()
def index():
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-receive-gaps-"))
    idx = Index(tmp / "index.sqlite")
    idx.get_or_create_entity(ENTITY, "sensor", "measurement", "°C")
    try:
        yield idx
    finally:
        idx.close()
        shutil.rmtree(tmp, ignore_errors=True)


_counter = 0


def _event(index: Index, ts: float, *, recorded: bool = True, entity_id: str = ENTITY) -> None:
    global _counter
    _counter += 1
    event_id = f"e{_counter}"
    index.claim_ingest_event(event_id, entity_id, ts)
    index.complete_ingest_event(event_id, entity_id, ts, recorded=recorded, value=1.0 if recorded else None)


def test_a_pause_over_five_minutes_is_recorded_even_when_the_value_was_discarded(index) -> None:
    _event(index, 1000.0)
    _event(index, 1000.0 + 600, recorded=False)  # vom Filter verworfen, aber empfangen
    assert index.list_receive_gaps(ENTITY) == [(1000.0, 1600.0)]


def test_short_pauses_and_the_first_value_record_nothing(index) -> None:
    _event(index, 1000.0)
    assert index.list_receive_gaps(ENTITY) == []
    _event(index, 1000.0 + MIN_RECEIVE_GAP_SECONDS)  # genau die Mindestgröße ist noch keine Pause
    assert index.list_receive_gaps(ENTITY) == []
    assert index.get_entity(ENTITY)["receive_gaps_since"] == 1000.0


def test_late_arrivals_do_not_create_or_move_anything(index) -> None:
    _event(index, 5000.0)
    _event(index, 1000.0)  # Nachzügler
    assert index.list_receive_gaps(ENTITY) == []
    assert index.get_entity(ENTITY)["last_seen_ts"] == 5000.0


def test_the_heartbeat_does_not_matter_for_the_pauses(index) -> None:
    """Sensor im 5-Minuten-Takt: der Filter verwirft fast alles (Lebenszeichen nur alle 6 Stunden), im Speicher
    stehen Abstände von 6 Stunden. Im Empfangsstrom gibt es keine Pause über 5 Minuten, und eine echte Pause von
    40 Minuten wird gefunden — unabhängig vom Lebenszeichen."""
    t = 0.0
    for _ in range(100):
        _event(index, t, recorded=(t % 21600 == 0))
        t += 300
    assert index.list_receive_gaps(ENTITY) == []
    t += 2400 - 300
    _event(index, t, recorded=False)
    assert index.list_receive_gaps(ENTITY, min_seconds=1800) == [(t - 2400, t)]


def test_listing_filters_by_window_and_duration(index) -> None:
    for ts in (0.0, 1000.0, 3000.0, 3400.0, 10_000.0):
        _event(index, ts, recorded=False)
    assert [round(e - s) for s, e in index.list_receive_gaps(ENTITY)] == [1000, 2000, 400, 6600]
    assert index.list_receive_gaps(ENTITY, from_ts=3500.0) == [(3400.0, 10_000.0)]
    assert index.list_receive_gaps(ENTITY, min_seconds=1500) == [(1000.0, 3000.0), (3400.0, 10_000.0)]
    assert index.list_receive_gaps(ENTITY, from_ts=0.0, to_ts=1000.0) == [(0.0, 1000.0)]


def test_the_table_is_cleaned_with_the_entity_and_with_clearing(index) -> None:
    _event(index, 0.0)
    _event(index, 1000.0)
    assert index.list_receive_gaps(ENTITY)
    index.clear_entity_data(ENTITY)
    assert index.list_receive_gaps(ENTITY) == []
    entity = index.get_entity(ENTITY)
    assert entity["last_seen_ts"] is None and entity["receive_gaps_since"] is None
    _event(index, 5000.0)
    _event(index, 9000.0)
    assert index.list_receive_gaps(ENTITY)
    index.delete_entity(ENTITY)
    assert index.list_receive_gaps(ENTITY) == []


def test_pruning_follows_retention_age_and_the_cap(index) -> None:
    now = 1_000_000_000.0
    index._conn.executemany(
        "INSERT INTO receive_gaps (entity_id, start_ts, end_ts) VALUES (?, ?, ?)",
        [(ENTITY, now - 100 * 86400, now - 100 * 86400 + 1000),        # 100 Tage alt
         (ENTITY, now - 10 * 86400, now - 10 * 86400 + 1000),           # 10 Tage alt
         (ENTITY, now - 900 * 86400, now - 900 * 86400 + 1000)],        # älter als zwei Jahre
    )
    index.set_config(ENTITY, retention="90d")
    removed = index.prune_receive_gaps(now, {"90d": 90})
    assert removed == 2
    assert [round(now - e) for _s, e in index.list_receive_gaps(ENTITY)] == [10 * 86400 - 1000]
    # Obergrenze: die ältesten gehen zuerst
    index._conn.executemany(
        "INSERT INTO receive_gaps (entity_id, start_ts, end_ts) VALUES (?, ?, ?)",
        [(ENTITY, now - i * 10 - 1000, now - i * 10) for i in range(MAX_RECEIVE_GAPS_PER_ENTITY + 5)],
    )
    index.prune_receive_gaps(now, {})
    count = index._conn.execute("SELECT COUNT(*) FROM receive_gaps WHERE entity_id = ?", (ENTITY,)).fetchone()[0]
    assert count == MAX_RECEIVE_GAPS_PER_ENTITY


def test_the_method_follows_filter_and_resolution(index) -> None:
    entity = lambda **kw: {"value_filter": "off", "resolution": "raw", **kw}  # noqa: E731
    assert gap_method(entity()) == "stored"
    assert gap_method(entity(value_filter="decimals")) == "receive"
    assert gap_method(entity(resolution="1h")) == "receive"


# --- Analyse -----------------------------------------------------------------------------------------------------

from zoneinfo import ZoneInfo  # noqa: E402

from app import cleanup_stats  # noqa: E402
from app.storage import cleanup  # noqa: E402

TZ = ZoneInfo("Europe/Berlin")


def _entity(**kw):
    base = {"entity_id": ENTITY, "gap_threshold": "15", "value_filter": "off", "resolution": "raw",
            "receive_gaps_since": None}
    return {**base, **kw}


def test_stored_method_is_the_unchanged_behaviour() -> None:
    rows = [(0.0, 1.0), (600.0, 1.0), (4000.0, 1.0)]
    marks = cleanup.detect_gaps(rows, 15.0, "auto", TZ)
    assert list(marks) == [4000.0]


def test_filtered_storage_does_not_create_gaps_in_the_receive_method() -> None:
    """Gespeichert sind nur Lebenszeichen im 6-Stunden-Abstand. In der alten Methode wäre jeder davon eine Lücke; im
    Empfangsstrom gab es keine Pause, also auch keine Lücke."""
    rows = [(i * 21600.0, 1.0) for i in range(5)]
    assert len(cleanup.detect_gaps(rows, 15.0, "auto", TZ)) == 4
    assert cleanup.detect_gaps(rows, 15.0, "auto", TZ, receive_gaps=[], stored_until=0.0) == {}


def test_a_receive_pause_is_attributed_to_the_next_stored_row() -> None:
    rows = [(0.0, 1.0), (21600.0, 1.0), (43200.0, 1.0)]
    # Pause von 40 Minuten endet bei 22000 (ein verworfener Wert), der nächste gespeicherte Wert steht bei 43200
    marks = cleanup.detect_gaps(rows, 15.0, "auto", TZ, receive_gaps=[(19600.0, 22000.0)], stored_until=0.0)
    assert list(marks) == [43200.0]
    assert "ohne empfangenen Wert" in marks[43200.0]


def test_the_threshold_decides_which_receive_pauses_count() -> None:
    rows = [(0.0, 1.0), (10_000.0, 1.0)]
    gaps = [(100.0, 1100.0), (5000.0, 9000.0)]  # 1000 s und 4000 s
    assert len(cleanup.detect_gaps(rows, 30.0, "auto", TZ, receive_gaps=gaps, stored_until=0.0)) == 1
    assert len(cleanup.detect_gaps(rows, 10.0, "auto", TZ, receive_gaps=gaps, stored_until=0.0)) == 1  # zusammen auf einer Zeile


def test_the_streaming_analysis_counts_the_same_rows() -> None:
    rows = [(0.0, 1.0), (21600.0, 1.0), (43200.0, 1.0), (64800.0, 1.0)]
    analysis = cleanup.analyze_raw_rows_page(
        lambda: iter(rows), filter_="gaps", page=1, page_size=50, gap_threshold_minutes=15.0,
        outlier_factor=None, tz=TZ, receive_gaps=[(30000.0, 33000.0)], gap_stored_until=0.0,
    )
    assert analysis["counts"]["gaps"] == 1
    assert [row["ts"] for row in analysis["rows"]] == [43200.0]


def test_gap_options_and_cache_key_follow_the_method() -> None:
    assert cleanup.gap_options(None, _entity()) == {"gap_threshold_minutes": 15.0}
    assert cleanup.gap_options(None, _entity(gap_threshold="off")) == {"gap_threshold_minutes": None}
    assert cleanup.gap_cache_key(_entity()) == "15"
    assert cleanup.gap_cache_key(_entity(value_filter="decimals")) == "r15"
    assert cleanup.gap_cache_key(_entity(value_filter="decimals", gap_threshold="1")) == "r5"
    assert cleanup.gap_cache_key(_entity(value_filter="decimals", gap_threshold="off")) == "off"


def test_gap_options_reads_the_pauses_from_the_start_of_recording(index) -> None:
    for ts in (0.0, 4000.0, 8000.0):
        _event(index, ts, recorded=False)
    index.set_config(ENTITY, value_filter="decimals", gap_threshold="30")
    entity = index.get_entity(ENTITY)
    options = cleanup.gap_options(index, entity, 0.0, 10_000.0)
    assert options["gap_threshold_minutes"] == 30.0
    assert options["receive_gaps"] == [(0.0, 4000.0), (4000.0, 8000.0)]
    assert options["gap_stored_until"] == 0.0
    # eine Entität ohne begonnene Aufzeichnung hat nichts zu bewerten
    assert cleanup.gap_options(index, _entity(value_filter="decimals"))["receive_gaps"] == []


def test_a_cached_count_is_only_valid_for_the_method_it_was_measured_with() -> None:
    plain = _entity()
    filtered = _entity(value_filter="decimals")
    entry = {"gap_threshold": "15", "gap_profile": {"method": "stored", "days": 30, "events": 100, "tiers": {}}}
    assert cleanup_stats.gaps_measured(plain, entry)
    assert not cleanup_stats.gaps_measured(plain, {"gap_threshold": "15"})  # ohne Pausen-Profil: einmal neu messen
    assert not cleanup_stats.gaps_measured(filtered, entry)
    assert cleanup_stats.gaps_measured(filtered, {"gap_threshold": "r15"})
