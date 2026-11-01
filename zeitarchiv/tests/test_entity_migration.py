"""Tests für app/storage/entity_migration.py.

Die eigentliche Monats-Klassifizierung/Deduplizierung (import_rows()/
plan_import_rows() aus symcon_import.py) ist bereits in test_symcon_import.py
ausführlich abgedeckt — diese Tests prüfen nur, was entity_migration.py selbst
hinzufügt: Typ-/Einheiten-Prüfung, den Umrechnungsfaktor, die drei
post_action-Varianten und das automatische Umhängen von Dashboard-Kacheln
(inklusive des Duplikat-Falls)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from app.storage import entity_migration, hotbuffer
from app.storage.index import Index

TZ = ZoneInfo("Europe/Berlin")


def _ts(y: int, m: int, d: int, h: int = 12) -> float:
    return datetime(y, m, d, h, tzinfo=TZ).timestamp()


def _write_archive_month(root: Path, entity_id: str, label: str, rows: list[tuple[float, float]]) -> None:
    archive_dir = root / "archive" / entity_id
    archive_dir.mkdir(parents=True, exist_ok=True)
    pq.write_table(
        pa.table({"ts": [r[0] for r in rows], "value": [r[1] for r in rows]}),
        archive_dir / f"{label}.parquet",
    )


def _make_entity(
    index: Index, root: Path, entity_id: str, domain: str, state_class: str, unit: str,
    rows: list[tuple[float, float]],
) -> None:
    """Legt eine Entität mit echten, über cleanup.iter_raw_rows() lesbaren
    Archivdateien an — alle rows liegen bewusst außerhalb des laufenden
    Kalendermonats, damit sie garantiert ins Archiv (nicht den Hot Buffer)
    fallen, unabhängig davon, wann der Test läuft."""
    index.get_or_create_entity(entity_id, domain, state_class, unit)
    by_month: dict[str, list[tuple[float, float]]] = {}
    for ts, value in rows:
        by_month.setdefault(datetime.fromtimestamp(ts, TZ).strftime("%Y-%m"), []).append((ts, value))
    for label, month_rows in by_month.items():
        _write_archive_month(root, entity_id, label, month_rows)
    for ts, value in sorted(rows):
        index.record_write(entity_id, ts, value)


def test_plan_migration_blocks_on_incompatible_aggregation_type(tmp_path: Path) -> None:
    index = Index(tmp_path / "index.sqlite")
    try:
        _make_entity(index, tmp_path, "sensor.source", "sensor", "total_increasing", "kWh", [(_ts(2020, 1, 1), 1.0)])
        _make_entity(index, tmp_path, "sensor.target", "sensor", "measurement", "kWh", [(_ts(2020, 1, 2), 2.0)])
        with pytest.raises(entity_migration.IncompatibleTypesError):
            entity_migration.plan_migration(tmp_path, index, "sensor.source", "sensor.target", TZ)
        with pytest.raises(entity_migration.IncompatibleTypesError):
            entity_migration.execute_migration(tmp_path, index, "sensor.source", "sensor.target", TZ)
    finally:
        index.close()


def test_plan_migration_surfaces_unit_mismatch_without_blocking(tmp_path: Path) -> None:
    index = Index(tmp_path / "index.sqlite")
    try:
        _make_entity(index, tmp_path, "sensor.source", "sensor", "measurement", "kWh", [(_ts(2020, 1, 1), 1.0)])
        _make_entity(index, tmp_path, "sensor.target", "sensor", "measurement", "Wh", [(_ts(2020, 2, 1), 500.0)])
        plan = entity_migration.plan_migration(tmp_path, index, "sensor.source", "sensor.target", TZ)
        assert plan.unit_mismatch is True
        assert plan.rows_to_transfer == 1
        assert plan.duplicate_rows == 0
        assert plan.suggested_factor == 1000.0
    finally:
        index.close()


@pytest.mark.parametrize(
    ("source_unit", "target_unit", "expected"),
    [
        ("kWh", "Wh", 1000.0),
        ("Wh", "kWh", 0.001),
        ("MWh", "kWh", 1000.0),
        ("kW", "W", 1000.0),
        ("W", "MW", 0.000001),
        ("kWh", "kWh", None),  # identisch — kein Faktor nötig
        ("kWh", "kW", None),  # Energie vs. Leistung, keine gültige Umrechnung
        ("°C", "°F", None),  # nicht rein multiplikativ (bräuchte eine Verschiebung)
        ("kWh", "", None),
        ("", "Wh", None),
        ("kWh", "m³", None),  # keine bekannte gemeinsame Größe
    ],
)
def test_suggest_factor(source_unit: str, target_unit: str, expected: float | None) -> None:
    result = entity_migration.suggest_factor(source_unit, target_unit)
    if expected is None:
        assert result is None
    else:
        assert result == pytest.approx(expected)


def test_execute_migration_scales_values_by_factor(tmp_path: Path) -> None:
    index = Index(tmp_path / "index.sqlite")
    try:
        _make_entity(index, tmp_path, "sensor.source", "sensor", "measurement", "kWh", [(_ts(2020, 1, 1), 2.0)])
        _make_entity(index, tmp_path, "sensor.target", "sensor", "measurement", "Wh", [])
        entity_migration.execute_migration(
            tmp_path, index, "sensor.source", "sensor.target", TZ, factor=1000.0, post_action="keep",
        )
        table = pq.read_table(tmp_path / "archive" / "sensor.target" / "2020-01.parquet")
        assert table.column("value").to_pylist() == [2000.0]
    finally:
        index.close()


def test_execute_migration_keeps_target_value_on_timestamp_overlap(tmp_path: Path) -> None:
    """Bei einer Zeitstempel-Überschneidung gewinnt immer der bereits
    vorhandene Wert der Zielentität — die Quelle überschreibt ihn nie."""
    index = Index(tmp_path / "index.sqlite")
    try:
        overlap_ts = _ts(2020, 1, 10)
        _make_entity(index, tmp_path, "sensor.source", "sensor", "measurement", "°C", [
            (_ts(2020, 1, 1), 10.0), (overlap_ts, 999.0),
        ])
        _make_entity(index, tmp_path, "sensor.target", "sensor", "measurement", "°C", [
            (overlap_ts, 20.0),
        ])
        result = entity_migration.execute_migration(
            tmp_path, index, "sensor.source", "sensor.target", TZ, post_action="keep",
        )
        assert result.rows_transferred == 1
        assert result.duplicate_rows == 1
        table = pq.read_table(tmp_path / "archive" / "sensor.target" / "2020-01.parquet")
        values_by_ts = dict(zip(table.column("ts").to_pylist(), table.column("value").to_pylist()))
        assert values_by_ts[overlap_ts] == 20.0
        assert values_by_ts[_ts(2020, 1, 1)] == 10.0
    finally:
        index.close()


def test_overlap_resolution_source_overwrites_target_value_in_an_archived_month(tmp_path: Path) -> None:
    """overlap_resolution="source" ("Quellwerte übernehmen"): an einem
    überschneidenden Zeitstempel in einem bereits archivierten Monat gewinnt
    hier die Quelle, statt (wie der Standardfall) verworfen zu werden."""
    index = Index(tmp_path / "index.sqlite")
    try:
        overlap_ts = _ts(2020, 1, 10)
        _make_entity(index, tmp_path, "sensor.source", "sensor", "measurement", "°C", [
            (_ts(2020, 1, 1), 10.0), (overlap_ts, 999.0),
        ])
        _make_entity(index, tmp_path, "sensor.target", "sensor", "measurement", "°C", [
            (overlap_ts, 20.0),
        ])
        result = entity_migration.execute_migration(
            tmp_path, index, "sensor.source", "sensor.target", TZ,
            post_action="keep", overlap_resolution="source",
        )
        assert result.overlap_resolution == "source"
        assert result.rows_transferred == 1
        assert result.duplicate_rows == 1
        assert result.overwritten_rows == 1
        table = pq.read_table(tmp_path / "archive" / "sensor.target" / "2020-01.parquet")
        values_by_ts = dict(zip(table.column("ts").to_pylist(), table.column("value").to_pylist()))
        assert values_by_ts[overlap_ts] == 999.0
        assert values_by_ts[_ts(2020, 1, 1)] == 10.0
    finally:
        index.close()


def test_overlap_resolution_source_overwrites_target_value_in_the_hot_buffer(tmp_path: Path) -> None:
    """Derselbe Fall, aber für den laufenden Kalendermonat (Hot Buffer statt
    Archivdatei) — ein eigener Codepfad in _overwrite_existing_values()."""
    index = Index(tmp_path / "index.sqlite")
    try:
        # Zwei feste Stellen am Anfang des laufenden Monats; "jetzt" und der Monatserste fallen am 1. zusammen.
        month_start = datetime.now(TZ).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        other_ts = month_start.timestamp() + 60
        overlap_ts = month_start.timestamp() + 120

        index.get_or_create_entity("sensor.source", "sensor", "measurement", "°C")
        index.get_or_create_entity("sensor.target", "sensor", "measurement", "°C")
        hotbuffer.append(tmp_path, "sensor.target", overlap_ts, 20.0, TZ)
        index.record_write("sensor.target", overlap_ts, 20.0)
        hotbuffer.append(tmp_path, "sensor.source", other_ts, 10.0, TZ)
        hotbuffer.append(tmp_path, "sensor.source", overlap_ts, 999.0, TZ)
        index.record_write("sensor.source", other_ts, 10.0)
        index.record_write("sensor.source", overlap_ts, 999.0)

        result = entity_migration.execute_migration(
            tmp_path, index, "sensor.source", "sensor.target", TZ,
            post_action="keep", overlap_resolution="source",
        )
        assert result.duplicate_rows == 1
        assert result.overwritten_rows == 1
        rows = dict(hotbuffer.read_rows(hotbuffer.hot_path(tmp_path, "sensor.target", overlap_ts, TZ)))
        assert rows[overlap_ts] == 999.0
    finally:
        index.close()


def test_post_action_keep_leaves_source_untouched(tmp_path: Path) -> None:
    index = Index(tmp_path / "index.sqlite")
    try:
        _make_entity(index, tmp_path, "sensor.source", "sensor", "measurement", "°C", [(_ts(2020, 1, 1), 1.0)])
        _make_entity(index, tmp_path, "sensor.target", "sensor", "measurement", "°C", [])
        entity_migration.execute_migration(
            tmp_path, index, "sensor.source", "sensor.target", TZ, post_action="keep",
        )
        source = index.get_entity("sensor.source")
        assert source is not None
        assert source["row_count"] == 1
        assert (tmp_path / "archive" / "sensor.source" / "2020-01.parquet").exists()
    finally:
        index.close()


def test_post_action_clear_empties_but_keeps_source_configuration(tmp_path: Path) -> None:
    index = Index(tmp_path / "index.sqlite")
    try:
        _make_entity(index, tmp_path, "sensor.source", "sensor", "measurement", "°C", [(_ts(2020, 1, 1), 1.0)])
        _make_entity(index, tmp_path, "sensor.target", "sensor", "measurement", "°C", [])
        index.set_config("sensor.source", resolution="5min", retention="1y", decimals="2")

        entity_migration.execute_migration(
            tmp_path, index, "sensor.source", "sensor.target", TZ, post_action="clear",
        )
        source = index.get_entity("sensor.source")
        assert source is not None
        assert source["row_count"] == 0
        assert source["resolution"] == "5min"
        assert not (tmp_path / "archive" / "sensor.source").exists()
    finally:
        index.close()


def test_post_action_delete_removes_source_entity_entirely(tmp_path: Path) -> None:
    index = Index(tmp_path / "index.sqlite")
    try:
        _make_entity(index, tmp_path, "sensor.source", "sensor", "measurement", "°C", [(_ts(2020, 1, 1), 1.0)])
        _make_entity(index, tmp_path, "sensor.target", "sensor", "measurement", "°C", [])

        entity_migration.execute_migration(
            tmp_path, index, "sensor.source", "sensor.target", TZ, post_action="delete",
        )
        assert index.get_entity("sensor.source") is None
        assert not (tmp_path / "archive" / "sensor.source").exists()
        target = index.get_entity("sensor.target")
        assert target["row_count"] == 1
    finally:
        index.close()


def test_delete_repoints_dashboard_pin_and_keeps_tile_settings(tmp_path: Path) -> None:
    index = Index(tmp_path / "index.sqlite")
    try:
        _make_entity(index, tmp_path, "sensor.source", "sensor", "measurement", "°C", [(_ts(2020, 1, 1), 1.0)])
        _make_entity(index, tmp_path, "sensor.target", "sensor", "measurement", "°C", [])
        dashboard_id = index.get_default_dashboard_id()
        pin_id = index.pin_entity_to_dashboard(dashboard_id, "sensor.source")
        assert pin_id
        index.set_dashboard_entity_pin_sparkline_resolution(dashboard_id, pin_id, "5min")

        result = entity_migration.execute_migration(
            tmp_path, index, "sensor.source", "sensor.target", TZ, post_action="delete",
        )

        assert result.repointed_dashboards
        pins = index.list_dashboard_pins(dashboard_id)
        assert len(pins) == 1
        assert pins[0]["item_entity_id"] == "sensor.target"
        assert pins[0]["sparkline_resolution"] == "5min"
    finally:
        index.close()


def test_delete_repoints_source_pin_even_when_target_already_pinned(tmp_path: Path) -> None:
    """Seit dem Mehrfach-Anheften-Feature (Nutzer-Wunsch: dieselbe Entität mit
    unterschiedlichen Einstellungen mehrfach zeigen) ist eine bereits
    angeheftete Zielentität kein Sonderfall mehr — das Umhängen legt eine
    zweite, unabhängig konfigurierte Kachel der Zielentität an, statt die
    Quell-Kachel ersatzlos zu entfernen."""
    index = Index(tmp_path / "index.sqlite")
    try:
        _make_entity(index, tmp_path, "sensor.source", "sensor", "measurement", "°C", [(_ts(2020, 1, 1), 1.0)])
        _make_entity(index, tmp_path, "sensor.target", "sensor", "measurement", "°C", [])
        dashboard_id = index.get_default_dashboard_id()
        assert index.pin_entity_to_dashboard(dashboard_id, "sensor.source")
        assert index.pin_entity_to_dashboard(dashboard_id, "sensor.target")

        result = entity_migration.execute_migration(
            tmp_path, index, "sensor.source", "sensor.target", TZ, post_action="delete",
        )

        assert result.repointed_dashboards
        pins = index.list_dashboard_pins(dashboard_id)
        assert [p["item_entity_id"] for p in pins] == ["sensor.target", "sensor.target"]
    finally:
        index.close()


def test_source_and_target_must_differ(tmp_path: Path) -> None:
    index = Index(tmp_path / "index.sqlite")
    try:
        _make_entity(index, tmp_path, "sensor.a", "sensor", "measurement", "°C", [(_ts(2020, 1, 1), 1.0)])
        with pytest.raises(ValueError):
            entity_migration.plan_migration(tmp_path, index, "sensor.a", "sensor.a", TZ)
    finally:
        index.close()
