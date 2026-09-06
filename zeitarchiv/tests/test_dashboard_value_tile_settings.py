"""Persistenztests für die erweiterten Werte-Kachel-Einstellungen."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.storage.index import Index


def test_new_value_tile_enables_sparkline_and_uses_raw_resolution(tmp_path: Path) -> None:
    index = Index(tmp_path / "index.sqlite")
    try:
        index.get_or_create_entity("sensor.one", "sensor", "measurement", "°C")
        dashboard_id = index.get_default_dashboard_id()
        assert index.pin_entity_to_dashboard(dashboard_id, "sensor.one")
        pin = index.list_dashboard_pins(dashboard_id)[0]
        assert pin["show_sparkline"] == 1
        assert pin["sparkline_resolution"] == "raw"
    finally:
        index.close()


def test_value_tile_resolution_and_entity_can_be_changed(tmp_path: Path) -> None:
    index = Index(tmp_path / "index.sqlite")
    try:
        index.get_or_create_entity("sensor.one", "sensor", "measurement", "°C")
        index.get_or_create_entity("sensor.two", "sensor", "measurement", "°C")
        dashboard_id = index.get_default_dashboard_id()
        index.pin_entity_to_dashboard(dashboard_id, "sensor.one")

        assert index.set_dashboard_entity_pin_sparkline_resolution(
            dashboard_id, "sensor.one", "5min"
        )
        assert index.set_dashboard_entity_pin_entity(
            dashboard_id, "sensor.one", "sensor.two"
        )
        pin = index.list_dashboard_pins(dashboard_id)[0]
        assert pin["item_entity_id"] == "sensor.two"
        assert pin["sparkline_resolution"] == "5min"
    finally:
        index.close()


def test_new_value_tile_defaults_reproduce_the_previous_tile(tmp_path: Path) -> None:
    """Die vier Kennzahlen-Spalten sind so vorbelegt, dass eine frisch
    angeheftete Kachel genau das zeigt, was sie vor der Erweiterung zeigte:
    Kalendertag, aktueller Wert, keine Kennzahlen-Zeile."""
    index = Index(tmp_path / "index.sqlite")
    try:
        index.get_or_create_entity("sensor.one", "sensor", "measurement", "°C")
        dashboard_id = index.get_default_dashboard_id()
        index.pin_entity_to_dashboard(dashboard_id, "sensor.one")
        pin = index.list_dashboard_pins(dashboard_id)[0]
        assert pin["range_key"] == "day"
        assert pin["continuous"] == 0
        assert pin["primary_metric"] == "last"
        assert pin["stats_metrics"] == ""
    finally:
        index.close()


def test_metrics_are_stored_in_display_order_without_duplicates(tmp_path: Path) -> None:
    """Die Anzeige liest stats_metrics unbesehen — die Reihenfolge auf der
    Kachel darf deshalb nicht davon abhängen, in welcher Reihenfolge der
    Nutzer die Knöpfe gedrückt hat."""
    index = Index(tmp_path / "index.sqlite")
    try:
        index.get_or_create_entity("sensor.one", "sensor", "measurement", "°C")
        dashboard_id = index.get_default_dashboard_id()
        index.pin_entity_to_dashboard(dashboard_id, "sensor.one")

        assert index.set_dashboard_entity_pin_metrics(
            dashboard_id, "sensor.one", stats_metrics=["max", "min", "min"]
        )
        pin = index.list_dashboard_pins(dashboard_id)[0]
        assert pin["stats_metrics"] == "min,max"
    finally:
        index.close()


def test_metrics_setter_leaves_unnamed_fields_untouched(tmp_path: Path) -> None:
    """Ein gemeinsamer Setter für vier Felder: wer nur den Zeitraum umstellt,
    darf Hauptwert und Kennzahlen-Zeile nicht mit zurücksetzen."""
    index = Index(tmp_path / "index.sqlite")
    try:
        index.get_or_create_entity("sensor.one", "sensor", "measurement", "°C")
        dashboard_id = index.get_default_dashboard_id()
        index.pin_entity_to_dashboard(dashboard_id, "sensor.one")

        index.set_dashboard_entity_pin_metrics(
            dashboard_id, "sensor.one", range_key="month", continuous=True,
            primary_metric="avg", stats_metrics=["min", "max"],
        )
        index.set_dashboard_entity_pin_metrics(dashboard_id, "sensor.one", range_key="year")

        pin = index.list_dashboard_pins(dashboard_id)[0]
        assert pin["range_key"] == "year"
        assert pin["continuous"] == 1
        assert pin["primary_metric"] == "avg"
        assert pin["stats_metrics"] == "min,max"
    finally:
        index.close()


def test_metrics_setter_rejects_values_the_tile_cannot_show(tmp_path: Path) -> None:
    index = Index(tmp_path / "index.sqlite")
    try:
        index.get_or_create_entity("sensor.one", "sensor", "measurement", "°C")
        dashboard_id = index.get_default_dashboard_id()
        index.pin_entity_to_dashboard(dashboard_id, "sensor.one")

        # "decade" gibt es als Zeitraum der Abfrage, aber nicht auf der Kachel.
        with pytest.raises(ValueError):
            index.set_dashboard_entity_pin_metrics(dashboard_id, "sensor.one", range_key="decade")
        # "auto" kennt _table_aggregates(), ist als Hauptwert aber keine Aussage.
        with pytest.raises(ValueError):
            index.set_dashboard_entity_pin_metrics(dashboard_id, "sensor.one", primary_metric="auto")
        # "last" ist der Hauptwert, keine Kennzahl der Zeile.
        with pytest.raises(ValueError):
            index.set_dashboard_entity_pin_metrics(dashboard_id, "sensor.one", stats_metrics=["last"])

        pin = index.list_dashboard_pins(dashboard_id)[0]
        assert (pin["range_key"], pin["primary_metric"], pin["stats_metrics"]) == ("day", "last", "")
    finally:
        index.close()


def test_duplicating_a_dashboard_keeps_the_metric_settings(tmp_path: Path) -> None:
    """duplicate_dashboard() zählt die zu kopierenden Spalten einzeln auf —
    eine vergessene Spalte fiele beim Duplizieren still auf den Standard
    zurück, ohne Fehlermeldung."""
    index = Index(tmp_path / "index.sqlite")
    try:
        index.get_or_create_entity("sensor.one", "sensor", "measurement", "°C")
        dashboard_id = index.get_default_dashboard_id()
        index.pin_entity_to_dashboard(dashboard_id, "sensor.one")
        index.set_dashboard_entity_pin_metrics(
            dashboard_id, "sensor.one", range_key="month", continuous=True,
            primary_metric="max", stats_metrics=["avg"],
        )

        copy_id = index.duplicate_dashboard(dashboard_id)
        pin = index.list_dashboard_pins(copy_id)[0]
        assert pin["range_key"] == "month"
        assert pin["continuous"] == 1
        assert pin["primary_metric"] == "max"
        assert pin["stats_metrics"] == "avg"
    finally:
        index.close()


def test_old_database_without_metric_columns_migrates_to_the_previous_behaviour(
    tmp_path: Path,
) -> None:
    """Eine vor der Erweiterung angelegte Datenbank darf beim Öffnen weder
    scheitern noch bestehende Kacheln sichtbar verändern."""
    db_path = tmp_path / "index.sqlite"
    connection = sqlite3.connect(db_path)
    connection.executescript(
        """
        CREATE TABLE dashboards (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL,
            position INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE dashboard_pins (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            dashboard_id INTEGER NOT NULL DEFAULT 1,
            item_type TEXT NOT NULL, item_id INTEGER NOT NULL, item_entity_id TEXT,
            position INTEGER NOT NULL,
            grid_cols INTEGER NOT NULL DEFAULT 1, grid_rows INTEGER NOT NULL DEFAULT 1,
            show_legend INTEGER NOT NULL DEFAULT 0, show_sparkline INTEGER NOT NULL DEFAULT 1,
            UNIQUE(dashboard_id, item_type, item_id, item_entity_id));
        INSERT INTO dashboards (id, name, position) VALUES (1, 'Alt', 0);
        INSERT INTO dashboard_pins (dashboard_id, item_type, item_id, item_entity_id, position)
            VALUES (1, 'entity', 0, 'sensor.alt', 0);
        """
    )
    connection.commit()
    connection.close()

    index = Index(db_path)
    try:
        pin = index.list_dashboard_pins(1)[0]
        assert pin["item_entity_id"] == "sensor.alt"
        assert pin["range_key"] == "day"
        assert pin["continuous"] == 0
        assert pin["primary_metric"] == "last"
        assert pin["stats_metrics"] == ""
    finally:
        index.close()


def test_primary_metric_is_dropped_from_the_stats_row() -> None:
    """Derselbe Wert zweimal auf einer Kachel wäre nur Rauschen: ist Ø der
    Hauptwert, zeigt die Zeile darunter nur noch Min und Max."""
    from app.main import _tile_metric_context

    kontext = _tile_metric_context({
        "range_key": "month", "continuous": 0,
        "primary_metric": "avg", "stats_metrics": "min,avg,max",
    })
    assert kontext["stats_metrics"] == ["min", "max"]
    assert kontext["primary_label"] == "Ø"


def test_the_period_is_shown_exactly_once_per_tile() -> None:
    """In der Kennzahlen-Zeile, wenn es sie gibt — sonst im Wert-Bereich an
    der Stelle des Alters. Bei einem aggregierten Hauptwert sagt das Alter des
    letzten Rohpunkts ohnehin nichts über einen Monatsdurchschnitt."""
    from app.main import _tile_metric_context

    nur_hauptwert = _tile_metric_context({
        "range_key": "month", "continuous": 0, "primary_metric": "avg", "stats_metrics": "",
    })
    assert nur_hauptwert["show_period_in_value_row"] is True

    mit_zeile = _tile_metric_context({
        "range_key": "month", "continuous": 0, "primary_metric": "avg", "stats_metrics": "min",
    })
    assert mit_zeile["show_period_in_value_row"] is False

    standard = _tile_metric_context({
        "range_key": "day", "continuous": 0, "primary_metric": "last", "stats_metrics": "",
    })
    assert standard["show_period_in_value_row"] is False
    assert standard["primary_label"] == ""


def test_rolling_periods_are_labelled_with_the_actual_window() -> None:
    """"30 Tage" statt "Monat rollierend": _window() rechnet dort genau 30
    Tage, keinen Kalendermonat. Das Etikett macht die Vereinfachung sichtbar,
    statt sie unter dem Wort "Monat" zu verstecken."""
    from app.main import _tile_metric_context

    def label(range_key, continuous):
        return _tile_metric_context({
            "range_key": range_key, "continuous": continuous,
            "primary_metric": "last", "stats_metrics": "",
        })["range_label"]

    assert label("day", 0) == "Tag"
    assert label("day", 1) == "24 Std."
    assert label("month", 0) == "Monat"
    assert label("month", 1) == "30 Tage"
    assert label("year", 1) == "12 Monate"


def test_a_corrupt_stored_value_falls_back_instead_of_breaking_the_page() -> None:
    """Ein von Hand verbogener Datenbankwert soll die Dashboard-Seite nicht
    mit einem KeyError abschießen — die Kachel fällt auf den Standard zurück."""
    from app.main import _tile_metric_context

    kontext = _tile_metric_context({
        "range_key": "jahrzehnt", "continuous": 0,
        "primary_metric": "unfug", "stats_metrics": "min",
    })
    assert kontext["range_key"] == "day"
    assert kontext["primary_metric"] == "last"
    assert kontext["range_label"] == "Tag"


def test_the_label_tables_in_python_and_javascript_agree() -> None:
    """Die Zeitraum-Etiketten stehen zweimal: der Server beschriftet die erste
    Anzeige, der Browser beschriftet nach einer Änderung im Kachelmenü neu.
    Driften sie auseinander, zeigt dieselbe Kachel je nach Weg einen anderen
    Zeitraum an — ohne dass irgendwo etwas fehlschlägt."""
    from app.main import _TILE_METRIC_LABELS, _TILE_RANGE_LABELS

    script = (
        Path(__file__).resolve().parents[1] / "app/static/js/dashboard-tiles.js"
    ).read_text(encoding="utf-8")
    for range_key, (kalendarisch, rollierend) in _TILE_RANGE_LABELS.items():
        assert f"{range_key}: ['{kalendarisch}', '{rollierend}']" in script, range_key
    for metric, label in _TILE_METRIC_LABELS.items():
        assert f"{metric}: '{label}'" in script, metric


def test_metrics_that_make_no_sense_for_the_entity_type_are_not_offered() -> None:
    """Dieselbe Regel wie in der Chart-Legende und in _tile_aggregates():
    Summe nur bei Zählern und Schaltern, Min/Max nicht bei Schaltern."""
    from app.main import _tile_available_metrics

    assert _tile_available_metrics("standard") == ["min", "avg", "max"]
    assert _tile_available_metrics("counter") == ["min", "avg", "max", "sum"]
    assert _tile_available_metrics("switch") == ["sum"]
    assert "sum" not in _tile_available_metrics(None)


def test_switching_the_entity_drops_metrics_it_cannot_show(tmp_path: Path) -> None:
    """Ein Entitätswechsel lässt die gespeicherten Kennzahlen stehen. Ein Σ,
    das für einen Zähler gesetzt wurde, darf danach nicht als "–" auf einer
    Messwert-Kachel kleben bleiben."""
    from app.main import _tile_metric_context

    kontext = _tile_metric_context(
        {"range_key": "day", "continuous": 0, "primary_metric": "last",
         "stats_metrics": "min,avg,sum"},
        "standard",
    )
    assert kontext["stats_metrics"] == ["min", "avg"]
    assert kontext["available_metrics"] == ["min", "avg", "max"]


def test_the_tile_menu_offers_every_setting_the_endpoint_accepts() -> None:
    """Gegenprobe zwischen Menü und Datenmodell: eine Kennzahl, die der Setter
    kennt, aber kein Knopf anbietet, wäre unerreichbar — und umgekehrt ein
    Knopf ohne Gegenstück im Setter ein stiller 400er."""
    from app.storage.index import (
        DASHBOARD_TILE_PRIMARY_METRICS,
        DASHBOARD_TILE_RANGES,
        DASHBOARD_TILE_STATS_METRICS,
    )

    menu = (
        Path(__file__).resolve().parents[1] / "app/templates/_dashboard_tile_menu.html"
    ).read_text(encoding="utf-8")
    for range_key in DASHBOARD_TILE_RANGES:
        assert f"data-range=\"{{{{ value }}}}\"" in menu
        assert f"('{range_key}', " in menu, range_key
    for metric in DASHBOARD_TILE_STATS_METRICS:
        assert f"('{metric}', " in menu, metric
    for metric in DASHBOARD_TILE_PRIMARY_METRICS:
        assert (f"('{metric}', " in menu) or (f'data-primary="{metric}"' in menu), metric
    # Beide Pole des Zeitfensters tragen einen Namen — ein An/Aus-Schalter
    # ließe offen, was "aus" bedeutet.
    assert "Laufend" in menu and "Rollierend" in menu
    assert "Kontinuierlich" not in menu
