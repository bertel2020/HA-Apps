"""Einschaltungen eines Schalters als Serie (ROADMAP 1.74, Schritt 4): switch_counts.py, series_style und die
Abfrage /api/query-multi?metrics=."""

from __future__ import annotations

from app import chart_types, switch_counts


def _entry() -> dict:
    return {
        "entity_id": "binary_sensor.defrost", "friendly_name": "Abtauvorgänge", "unit": "", "decimals": None,
        "display_mode": "time", "aggregation_type": "switch", "chart_type": "bar",
        "points": [
            {"ts": 1.0, "value": 900.0, "min": None, "max": None, "count": 2},
            {"ts": 2.0, "value": 240.0, "min": None, "max": None, "count": None},
        ],
        "compare_points": [{"ts": 0.0, "value": 60.0, "min": None, "max": None, "count": 1}],
    }


def test_count_replaces_the_duration_series_by_a_counter() -> None:
    (series,) = switch_counts.switch_count_entries(_entry(), "count")
    assert (series["entity_id"], series["aggregation_type"], series["chart_type"]) == ("binary_sensor.defrost", "counter", "bar")
    assert series["source_type"] == "switch"
    assert series["unit"] == "Einschaltungen" and series["decimals"] == 0 and series["display_mode"] == "onoff"
    assert [p["value"] for p in series["points"]] == [2, None]  # unbekannt bleibt None, nie 0
    assert [p["value"] for p in series["compare_points"]] == [1]


def test_both_keeps_the_duration_and_adds_the_count_as_a_line() -> None:
    duration, count = switch_counts.switch_count_entries(_entry(), "both")
    assert duration["aggregation_type"] == "switch" and duration["points"][0]["value"] == 900.0
    assert count["entity_id"] == "binary_sensor.defrost#count" and count["base_entity_id"] == "binary_sensor.defrost"
    assert count["base_name"] == "Abtauvorgänge" and "source_type" not in duration
    assert (count["aggregation_type"], count["chart_type"], count["friendly_name"]) == ("counter", "line", "Abtauvorgänge · Anzahl")


def test_previous_years_are_counted_too() -> None:
    entry = _entry()
    entry["years"] = [{"year": 2025, "points": [{"ts": 1.0, "value": 5.0, "count": 3}], "cut_points": None}]
    (series,) = switch_counts.switch_count_entries(entry, "count")
    assert series["years"][0]["points"][0]["value"] == 3 and series["years"][0]["cut_points"] is None


def test_the_input_entry_is_not_changed() -> None:
    entry = _entry()
    switch_counts.switch_count_entries(entry, "both")
    assert entry["aggregation_type"] == "switch" and entry["points"][0]["value"] == 900.0


def test_series_style_keeps_the_metric_only_for_switches() -> None:
    style = {
        "a": {"metric": "both"}, "b": {"metric": "count"}, "c": {"metric": "duration"},
        "d": {"metric": "count", "kind": "line"}, "e": {"metric": "bogus"},
    }
    types = {"a": "switch", "b": "counter", "c": "switch", "d": "standard", "e": "switch"}
    assert chart_types.series_style_clean(style, list(types), types) == {
        "a": {"metric": "both"}, "c": {"metric": "duration"}, "d": {"kind": "line"},
    }
    # ohne Typen (Aufrufer ohne Zugriff auf den Index) bleiben gültige Werte stehen
    assert chart_types.series_style_clean(style, list(types)) == {
        "a": {"metric": "both"}, "b": {"metric": "count"}, "c": {"metric": "duration"},
        "d": {"metric": "count", "kind": "line"},
    }


def test_the_query_rejects_an_unknown_metric(client) -> None:
    ok = client.get("/api/query-multi?entity_ids=ghost.one&range=day&metrics=ghost.one:count")
    assert ok.status_code == 200
    assert client.get("/api/query-multi?entity_ids=ghost.one&range=day&metrics=ghost.one:duration").status_code == 200
    bad = client.get("/api/query-multi?entity_ids=ghost.one&range=day&metrics=ghost.one:bogus")
    assert bad.status_code == 400


def test_the_display_mode_of_the_entity_is_the_default_metric() -> None:
    assert [switch_counts.default_metric(mode) for mode in ("onoff", "time", "count", "both", None)] == [
        "duration", "duration", "count", "both", "duration",
    ]


def test_the_count_line_is_drawn_as_points_on_a_whole_number_axis_from_zero() -> None:
    """Editor und Kachel behandeln die Anzahl (source_type) gleich: keine Glättung, keine Fläche, kein Haltepunkt bis zum
    Fensterende, Achse ab 0 mit ganzzahliger Teilung — sonst läuft die Kurve zwischen die Werte."""
    from _paths import APP, tile_js

    editor = (APP / "static/js/pages/chart_editor.js").read_text(encoding="utf-8")
    for source in (editor, tile_js()):
        assert source.count("!s.source_type") >= 4
        assert "minInterval: 1" in source
        assert "&& !isCountAxis" in source
