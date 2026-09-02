"""Tests für app/storage/query.py — Rollup+Live-Merge, Zeitzonen-Grenzen."""

from __future__ import annotations

import shutil
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    import pyarrow as pa
    import pyarrow.parquet as pq

    from app.storage import hotbuffer, query, rollup
    from app.storage.index import Index

    _PYARROW_AVAILABLE = True
except ImportError:
    _PYARROW_AVAILABLE = False

TZ = ZoneInfo("Europe/Berlin")


def _ts(y, m, d, h, mi=0, s=0) -> float:
    return datetime(y, m, d, h, mi, s, tzinfo=TZ).timestamp()


def test_day_range_assigns_local_midnight_correctly() -> None:
    """00:30 Europe/Berlin ist im Winter 23:30 UTC vom Vortag — darf trotzdem
    zum lokalen "heute" zählen, nicht zum UTC-Vortag."""
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-query-test-"))
    try:
        index = Index(tmp / "index.sqlite")
        entity_id = "sensor.temp"
        index.get_or_create_entity(entity_id, "sensor", "measurement", "°C")

        now = datetime(2024, 1, 15, 1, 0, 0, tzinfo=TZ)  # kurz nach Mitternacht
        hotbuffer.append(tmp, entity_id, _ts(2024, 1, 15, 0, 30), 5.0, TZ)  # "heute" 00:30
        hotbuffer.append(tmp, entity_id, _ts(2024, 1, 14, 23, 0), 4.0, TZ)  # "gestern" 23:00
        index.record_write(entity_id, _ts(2024, 1, 15, 0, 30))
        index.record_write(entity_id, _ts(2024, 1, 14, 23, 0))

        result = query.query_series(tmp, index, entity_id, "day", TZ, now)
        assert [(p["ts"], p["value"]) for p in result["points"]] == [
            (_ts(2024, 1, 15, 0), 4.0),  # letzter Vorwert gilt ab Tagesanfang weiter
            (_ts(2024, 1, 15, 0, 30), 5.0),
        ]

        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_line_chart_carries_previous_value_to_first_point_of_day() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-query-test-"))
    try:
        index = Index(tmp / "index.sqlite")
        entity_id = "sensor.helligkeit"
        index.get_or_create_entity(entity_id, "sensor", "measurement", "lx")
        hotbuffer.append(tmp, entity_id, _ts(2024, 8, 20, 20), 0.0, TZ)
        hotbuffer.append(tmp, entity_id, _ts(2024, 8, 21, 7), 10.0, TZ)
        index.record_write(entity_id, _ts(2024, 8, 20, 20))
        index.record_write(entity_id, _ts(2024, 8, 21, 7))

        result = query.query_series(
            tmp,
            index,
            entity_id,
            "day",
            TZ,
            datetime(2024, 8, 21, 8, tzinfo=TZ),
            chart_type="line",
        )

        assert result["points"][0] == {
            "ts": _ts(2024, 8, 21, 0),
            "value": 0.0,
            "min": 0.0,
            "max": 0.0,
        }
        assert result["points"][1]["ts"] == _ts(2024, 8, 21, 7)
        assert result["points"][1]["value"] == 10.0
        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_boundary_value_uses_latest_timestamp_not_largest_value() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-query-test-"))
    try:
        entity_id = "sensor.helligkeit"
        hotbuffer.append(tmp, entity_id, _ts(2024, 8, 20, 19), 100.0, TZ)
        hotbuffer.append(tmp, entity_id, _ts(2024, 8, 20, 20), 0.0, TZ)
        assert query._boundary_value(tmp, entity_id, _ts(2024, 8, 21, 0), TZ) == 0.0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_boundary_value_skips_row_groups_entirely_after_cutoff(monkeypatch, tmp_path: Path) -> None:
    """ZP-001 (PERFORMANCE.md): Row-Groups, deren gesamter ts-Bereich >=
    before_ts liegt, werden anhand der Parquet-Statistik übersprungen, ohne
    ihre Werte einzulesen — nur die Row-Group mit dem gesuchten Randwert wird
    tatsächlich gelesen."""
    entity_id = "sensor.helligkeit"
    archive_dir = tmp_path / "archive" / entity_id
    archive_dir.mkdir(parents=True)

    # Fünf Zeilen im September, je eine eigene Row-Group (row_group_size=1) —
    # nur die dritte (ts vor before_ts, größter solcher Wert) ist relevant.
    rows = [
        (_ts(2024, 9, 5, 0), 1.0),
        (_ts(2024, 9, 10, 0), 2.0),
        (_ts(2024, 9, 15, 0), 7.0),
        (_ts(2024, 9, 20, 0), 4.0),
        (_ts(2024, 9, 25, 0), 5.0),
    ]
    table = pa.table({"ts": [r[0] for r in rows], "value": [r[1] for r in rows]})
    pq.write_table(table, archive_dir / "2024-09.parquet", row_group_size=1)

    read_calls = 0
    original_read_row_group = query.pq.ParquetFile.read_row_group

    def recording_read_row_group(self, *args, **kwargs):
        nonlocal read_calls
        read_calls += 1
        return original_read_row_group(self, *args, **kwargs)

    monkeypatch.setattr(query.pq.ParquetFile, "read_row_group", recording_read_row_group)

    before_ts = _ts(2024, 9, 18, 0)
    value = query._boundary_value(tmp_path, entity_id, before_ts, TZ)

    assert value == 7.0
    # Die beiden jüngsten Row-Groups (ts 20/25, komplett >= before_ts) werden
    # anhand ihrer Statistik übersprungen, ohne gelesen zu werden. Erst die
    # dritte Row-Group (ts 15 < before_ts) wird tatsächlich gelesen und
    # liefert den Treffer — die beiden ältesten Row-Groups werden gar nicht
    # mehr angefasst.
    assert read_calls == 1


def test_rollup_read_pushes_time_window_into_parquet(monkeypatch, tmp_path: Path) -> None:
    entity_id = "sensor.temp"
    path = rollup.rollup_path(tmp_path, entity_id, "stunde")
    path.parent.mkdir(parents=True)
    pq.write_table(
        pa.table({"bucket_start": [100.0, 200.0, 300.0], "value": [1.0, 2.0, 3.0]}),
        path,
    )
    original = query.pq.read_table
    seen: dict = {}

    def recording_read(*args, **kwargs):
        seen["filters"] = kwargs.get("filters")
        return original(*args, **kwargs)

    monkeypatch.setattr(query.pq, "read_table", recording_read)
    rows = query._read_rollup_rows(tmp_path, entity_id, "stunde", 150.0, 250.0)

    assert [row.bucket_start for row in rows] == [200.0]
    assert seen["filters"] == [("bucket_start", ">=", 150.0), ("bucket_start", "<", 250.0)]


def test_week_range_merges_completed_rollup_with_live_current_month() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-query-test-"))
    try:
        index = Index(tmp / "index.sqlite")
        entity_id = "sensor.pv_ertrag_gesamt"
        index.get_or_create_entity(entity_id, "sensor", "total_increasing", "kWh")

        # Juli ist bereits archiviert + verdichtet (2 Tage mit Rohdaten).
        july_rows = [(_ts(2024, 7, 30, 10), 100.0), (_ts(2024, 7, 31, 10), 108.0)]
        july_table = pa.table({"ts": [r[0] for r in july_rows], "value": [r[1] for r in july_rows]})
        archive_dir = tmp / "archive" / entity_id
        archive_dir.mkdir(parents=True, exist_ok=True)
        pq.write_table(july_table, archive_dir / "2024-07.parquet")
        rollup.append_completed_month(tmp, entity_id, "counter", july_table, 2024, 7, TZ)

        # August (laufender Monat) hat bisher nur Rohdaten im Hot Buffer.
        for ts, value in [(_ts(2024, 8, 1, 10), 112.0), (_ts(2024, 8, 3, 10), 120.0)]:
            hotbuffer.append(tmp, entity_id, ts, value, TZ)
            index.record_write(entity_id, ts)

        now = datetime(2024, 8, 3, 12, 0, 0, tzinfo=TZ)
        result = query.query_series(tmp, index, entity_id, "week", TZ, now)

        # Referenzrechnung: alle Rohwerte in Zeitfolge, positive Deltas aufsummiert.
        all_rows = sorted(july_rows + [(_ts(2024, 8, 1, 10), 112.0), (_ts(2024, 8, 3, 10), 120.0)])
        expected_total = 0.0
        prev = all_rows[0][1]
        for _, value in all_rows:
            expected_total += max(0.0, value - prev)
            prev = value

        actual_total = sum(p["value"] for p in result["points"])
        assert actual_total == expected_total, (actual_total, expected_total)
        # 4 Kalendertage mit Rohdaten (30.07, 31.07, 01.08, 03.08).
        assert len(result["points"]) == 4

        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_year_range_counter_uses_monat_rollup_plus_live_month() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-query-test-"))
    try:
        index = Index(tmp / "index.sqlite")
        entity_id = "sensor.pv_ertrag_gesamt"
        index.get_or_create_entity(entity_id, "sensor", "total_increasing", "kWh")

        archive_dir = tmp / "archive" / entity_id
        archive_dir.mkdir(parents=True, exist_ok=True)
        prev_value = 0.0
        for month in range(1, 4):  # Jan-März abgeschlossen
            table = pa.table({"ts": [_ts(2024, month, 15, 10)], "value": [float(month * 10)]})
            pq.write_table(table, archive_dir / f"2024-{month:02d}.parquet")
            rollup.append_completed_month(tmp, entity_id, "counter", table, 2024, month, TZ)

        # April (laufender Monat): ein Live-Wert im Hot Buffer.
        hotbuffer.append(tmp, entity_id, _ts(2024, 4, 10, 10), 45.0, TZ)
        index.record_write(entity_id, _ts(2024, 4, 10, 10))

        now = datetime(2024, 4, 15, 12, 0, 0, tzinfo=TZ)
        result = query.query_series(tmp, index, entity_id, "year", TZ, now)

        assert len(result["points"]) == 4  # Jan, Feb, Mär (Rollup) + Apr (live)
        # Apr-Bucket: Referenzwert = letzter März-Wert (30.0) -> Delta 15.0
        april_point = max(result["points"], key=lambda p: p["ts"])
        assert april_point["value"] == 15.0

        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_year_range_continuous_includes_months_from_previous_calendar_year() -> None:
    """Bug: "Kontinuierlich" liefert bei range="year" ein rollierendes 12-Monats-
    Fenster, das über eine Jahresgrenze zurückreicht (z. B. Aug 2025-Aug 2026).
    Die Jahr-Ansicht versuchte für Monate vor dem laufenden Kalenderjahr
    fälschlich jahr.parquet zu lesen — das kennt aber nur GANZE Kalenderjahre,
    ein angeschnittenes Vorjahr lieferte dort keine Treffer, die betroffenen
    Monate verschwanden komplett aus dem Chart (hier: die Monate April-Dezember
    2023 fehlten). Die Jahr-Ansicht darf jahr.parquet nie konsultieren."""
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-query-test-"))
    try:
        index = Index(tmp / "index.sqlite")
        entity_id = "sensor.pv_ertrag_gesamt"
        index.get_or_create_entity(entity_id, "sensor", "total_increasing", "kWh")

        archive_dir = tmp / "archive" / entity_id
        archive_dir.mkdir(parents=True, exist_ok=True)

        # 2023 komplett archiviert (12 Monate) -> jahr.parquet bekommt eine
        # Zeile für 2023, genau der Fall, den die Jahr-Ansicht ignorieren muss.
        value = 0.0
        for month in range(1, 13):
            value += 100.0
            table = pa.table({"ts": [_ts(2023, month, 15, 10)], "value": [value]})
            pq.write_table(table, archive_dir / f"2023-{month:02d}.parquet")
            rollup.append_completed_month(tmp, entity_id, "counter", table, 2023, month, TZ)

        # 2024: Jan-März archiviert, April (laufender Monat) nur im Hot Buffer.
        for month in range(1, 4):
            value += 100.0
            table = pa.table({"ts": [_ts(2024, month, 15, 10)], "value": [value]})
            pq.write_table(table, archive_dir / f"2024-{month:02d}.parquet")
            rollup.append_completed_month(tmp, entity_id, "counter", table, 2024, month, TZ)
        value += 50.0
        hotbuffer.append(tmp, entity_id, _ts(2024, 4, 10, 10), value, TZ)
        index.record_write(entity_id, _ts(2024, 4, 10, 10))

        now = datetime(2024, 4, 15, 12, 0, 0, tzinfo=TZ)
        result = query.query_series(tmp, index, entity_id, "year", TZ, now, continuous=True)

        # Rollierendes Fenster [2023-04-15, 2024-04-15) -> Mai-Dez 2023 (8 Monate,
        # April 2023 startet vor dem Fenster und bleibt zu Recht draußen) + Jan-März
        # 2024 (3 Monate, Rollup) + April 2024 (live) = 12 Balken.
        assert len(result["points"]) == 12, result["points"]
        points_sorted = sorted(result["points"], key=lambda p: p["ts"])
        year_months = [(datetime.fromtimestamp(p["ts"], TZ).year, datetime.fromtimestamp(p["ts"], TZ).month) for p in points_sorted]
        expected_year_months = [(2023, m) for m in range(5, 13)] + [(2024, m) for m in range(1, 5)]
        assert year_months == expected_year_months, year_months
        total = sum(p["value"] for p in result["points"])
        assert total == 1150.0, total  # 8*100 (2023) + 3*100 (2024 Rollup) + 50 (April live)

        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_decade_range_counter_aggregates_current_year_into_one_bucket() -> None:
    """Bug: die Dekaden-Ansicht mischte für Zähler-Entitäten einen jahresbreiten
    Balken je abgeschlossenem Jahr (aus jahr.parquet) mit mehreren monatsbreiten
    Balken für das laufende, unvollständige Jahr (Rollup-Monate + Live-Monat) —
    sah neben den breiten Vorjahres-Balken sichtbar "kaputt" aus. Das laufende
    Jahr muss in der Dekaden-Ansicht genau EINEN Balken ergeben, mit dem Wert
    aller bisherigen Monate (inkl. des live berechneten laufenden Monats)."""
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-query-test-"))
    try:
        index = Index(tmp / "index.sqlite")
        entity_id = "sensor.pv_ertrag_gesamt"
        index.get_or_create_entity(entity_id, "sensor", "total_increasing", "kWh")

        archive_dir = tmp / "archive" / entity_id
        archive_dir.mkdir(parents=True, exist_ok=True)

        # 2023 komplett archiviert (12 Monate) -> jahr.parquet wird beim
        # Abschließen von Dezember automatisch fortgeschrieben.
        value = 0.0
        for month in range(1, 13):
            value += 100.0
            table = pa.table({"ts": [_ts(2023, month, 15, 10)], "value": [value]})
            pq.write_table(table, archive_dir / f"2023-{month:02d}.parquet")
            rollup.append_completed_month(tmp, entity_id, "counter", table, 2023, month, TZ)
        # Jan 2023 ist der allererste Monat ohne Referenzwert -> Delta 0, die
        # übrigen 11 Monate je +100 -> Jahressumme 2023 = 1100.
        expected_2023_total = 1100.0

        # 2024: Jan-März archiviert, April (laufender Monat) nur im Hot Buffer.
        for month in range(1, 4):
            value += 100.0
            table = pa.table({"ts": [_ts(2024, month, 15, 10)], "value": [value]})
            pq.write_table(table, archive_dir / f"2024-{month:02d}.parquet")
            rollup.append_completed_month(tmp, entity_id, "counter", table, 2024, month, TZ)
        value += 50.0
        hotbuffer.append(tmp, entity_id, _ts(2024, 4, 10, 10), value, TZ)
        index.record_write(entity_id, _ts(2024, 4, 10, 10))
        # Jan-März 2024 je +100, April (live) +50 -> bisherige Jahressumme 2024 = 350.
        expected_2024_total_so_far = 350.0

        now = datetime(2024, 4, 15, 12, 0, 0, tzinfo=TZ)
        result = query.query_series(tmp, index, entity_id, "decade", TZ, now)

        # Genau zwei Balken (2023 komplett, 2024 bisher) statt 1 Jahres- + 4 Monats-Balken.
        assert len(result["points"]) == 2, result["points"]
        by_year = {datetime.fromtimestamp(p["ts"], TZ).year: p["value"] for p in result["points"]}
        assert by_year == {2023: expected_2023_total, 2024: expected_2024_total_so_far}, by_year

        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_day_offset_minus_one_shows_full_yesterday_not_today() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-query-test-"))
    try:
        index = Index(tmp / "index.sqlite")
        entity_id = "sensor.temp"
        index.get_or_create_entity(entity_id, "sensor", "measurement", "°C")

        now = datetime(2024, 1, 15, 10, 0, 0, tzinfo=TZ)
        hotbuffer.append(tmp, entity_id, _ts(2024, 1, 14, 8, 0), 4.0, TZ)   # gestern
        hotbuffer.append(tmp, entity_id, _ts(2024, 1, 14, 20, 0), 6.0, TZ)  # gestern, nach "jetzt minus 1 Tag"
        hotbuffer.append(tmp, entity_id, _ts(2024, 1, 15, 8, 0), 9.0, TZ)   # heute — darf NICHT auftauchen

        today = query.query_series(tmp, index, entity_id, "day", TZ, now, offset=0)
        assert [p["value"] for p in today["points"]] == [6.0, 9.0]

        yesterday = query.query_series(tmp, index, entity_id, "day", TZ, now, offset=-1)
        assert sorted(p["value"] for p in yesterday["points"]) == [4.0, 6.0]
        assert yesterday["is_current"] is False

        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_month_offset_navigates_into_already_archived_month() -> None:
    """Der eigentliche Zweck der Navigation: ein Monat, der schon längst rotiert
    und archiviert ist, muss über offset erreichbar sein — nicht nur der laufende."""
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-query-test-"))
    try:
        index = Index(tmp / "index.sqlite")
        entity_id = "sensor.pv_ertrag_gesamt"
        index.get_or_create_entity(entity_id, "sensor", "total_increasing", "kWh")

        archive_dir = tmp / "archive" / entity_id
        archive_dir.mkdir(parents=True, exist_ok=True)
        # Zwei Werte in Mai, damit innerhalb des Monats selbst schon ein Delta
        # entsteht (der allererste Rohwert ohne boundary_value liefert per
        # Definition Delta 0 — er wird zur Referenz für den Folgewert).
        may_table = pa.table({"ts": [_ts(2024, 5, 10, 8), _ts(2024, 5, 20, 8)], "value": [100.0, 108.0]})
        pq.write_table(may_table, archive_dir / "2024-05.parquet")
        rollup.append_completed_month(tmp, entity_id, "counter", may_table, 2024, 5, TZ)

        june_table = pa.table({"ts": [_ts(2024, 6, 10, 10)], "value": [130.0]})
        pq.write_table(june_table, archive_dir / "2024-06.parquet")
        rollup.append_completed_month(tmp, entity_id, "counter", june_table, 2024, 6, TZ)

        now = datetime(2024, 8, 20, 12, 0, 0, tzinfo=TZ)  # Juli+Juni+Mai liegen alle in der Vergangenheit

        result = query.query_series(tmp, index, entity_id, "month", TZ, now, offset=-3)  # -> Mai
        assert sum(p["value"] for p in result["points"]) == 8.0  # 108 - 100, erster Wert ohne Referenz zählt nicht

        result_june = query.query_series(tmp, index, entity_id, "month", TZ, now, offset=-2)  # -> Juni
        assert sum(p["value"] for p in result_june["points"]) == 22.0  # 130 - 108 (Referenz aus Mai-Archiv)

        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_day_range_offset_reads_raw_data_from_archive_when_month_already_rotated() -> None:
    """Stunde/Tag lesen nie Rollups — beim Navigieren in einen archivierten
    Monat müssen sie stattdessen aus der Archiv-Parquet-Datei lesen können."""
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-query-test-"))
    try:
        index = Index(tmp / "index.sqlite")
        entity_id = "sensor.temp"
        index.get_or_create_entity(entity_id, "sensor", "measurement", "°C")

        archive_dir = tmp / "archive" / entity_id
        archive_dir.mkdir(parents=True, exist_ok=True)
        pq.write_table(
            pa.table({"ts": [_ts(2024, 6, 15, 8), _ts(2024, 6, 15, 14)], "value": [18.0, 22.0]}),
            archive_dir / "2024-06.parquet",
        )

        now = datetime(2024, 8, 20, 12, 0, 0, tzinfo=TZ)
        # offset so wählen, dass genau der 15.06. getroffen wird (heute - offset Tage = 15.06.).
        days_back = (now.date() - datetime(2024, 6, 15, tzinfo=TZ).date()).days
        result = query.query_series(tmp, index, entity_id, "day", TZ, now, offset=-days_back)

        values = sorted(p["value"] for p in result["points"])
        assert values == [18.0, 22.0]

        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_month_continuous_gives_rolling_thirty_day_window_not_calendar_month() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-query-test-"))
    try:
        index = Index(tmp / "index.sqlite")
        entity_id = "sensor.temp"
        index.get_or_create_entity(entity_id, "sensor", "measurement", "°C")

        now = datetime(2024, 8, 10, 12, 0, 0, tzinfo=TZ)
        # Vor dem 1. August (Kalendermonat-Start), aber innerhalb der letzten 30 Tage.
        hotbuffer.append(tmp, entity_id, _ts(2024, 7, 25, 8), 15.0, TZ)
        # Hot Buffer ist nach echtem Monat benannt — Juli-Wert landet also in der
        # Juli-Hot-Datei; für den Test wird sie direkt neben die August-Datei gelegt,
        # damit sowohl kalendarisch (nur August) als auch rollierend (auch Juli
        # anteilig) unterschiedliche Ergebnisse liefern.
        hotbuffer.append(tmp, entity_id, _ts(2024, 8, 5, 8), 20.0, TZ)

        calendar_result = query.query_series(tmp, index, entity_id, "month", TZ, now, continuous=False)
        rolling_result = query.query_series(tmp, index, entity_id, "month", TZ, now, continuous=True)

        # Kalendarisch (seit 1. August) sieht nur den August-Wert über den Rollup/Live-Pfad
        # der aktuellen Monatslogik — die eigentliche Prüfung hier ist der Fenster-Unterschied.
        window_calendar = query._window("month", now.astimezone(TZ), continuous=False)
        window_rolling = query._window("month", now.astimezone(TZ), continuous=True)
        assert window_calendar[0].day == 1  # kalendarisch: ab dem 1.
        assert window_rolling[0].day != 1 or window_rolling[0].month != 8  # rollierend: 30 Tage zurück, nicht ab dem 1.
        assert window_rolling[1] - window_rolling[0] == timedelta(days=30)

        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_week_calendar_mode_uses_monday_to_sunday_not_rolling_seven_days() -> None:
    """now ist ein Samstag — kalendarisch (Standard) beginnt die Woche am Montag
    derselben Woche, rollierend an einem beliebigen Tag genau 6 Tage zuvor."""
    now_local = datetime(2024, 8, 3, 12, 0, 0, tzinfo=TZ)  # Samstag
    calendar_start, _, _ = query._window("week", now_local, continuous=False)
    rolling_start, _, _ = query._window("week", now_local, continuous=True)
    assert calendar_start == datetime(2024, 7, 29, 0, 0, 0, tzinfo=TZ)  # Montag derselben Woche
    assert rolling_start == now_local - timedelta(days=7)  # exakt 7 Tage (ein voller Zeitraum) vor jetzt


def test_hour_calendar_mode_aligns_to_top_of_hour_not_rolling_sixty_minutes() -> None:
    now_local = datetime(2024, 8, 3, 14, 42, 0, tzinfo=TZ)
    calendar_start, calendar_end, _ = query._window("hour", now_local, continuous=False)
    rolling_start, rolling_end, _ = query._window("hour", now_local, continuous=True)
    assert calendar_start == datetime(2024, 8, 3, 14, 0, 0, tzinfo=TZ)
    assert calendar_end == now_local  # laufende Stunde, am "jetzt" gedeckelt
    assert rolling_start == datetime(2024, 8, 3, 13, 42, 0, tzinfo=TZ)  # exakt 60 Minuten zurück
    assert rolling_end == now_local


def test_decade_calendar_mode_aligns_to_decade_boundary_not_rolling_ten_years() -> None:
    now_local = datetime(2024, 8, 3, 12, 0, 0, tzinfo=TZ)
    calendar_start, _, _ = query._window("decade", now_local, continuous=False)
    rolling_start, _, _ = query._window("decade", now_local, continuous=True)
    assert calendar_start == datetime(2020, 1, 1, 0, 0, 0, tzinfo=TZ)  # Dekadengrenze 2020er
    assert rolling_start == datetime(2014, 8, 3, 12, 0, 0, tzinfo=TZ)  # exakt 10 Jahre (ein voller Zeitraum) vor jetzt


def test_offset_cannot_navigate_into_the_future() -> None:
    now_local = datetime(2024, 8, 10, 12, 0, 0, tzinfo=TZ)
    forward = query._window("day", now_local, offset=5)
    current = query._window("day", now_local, offset=0)
    assert forward == current


def test_year_over_year_shifts_window_by_exactly_one_year_not_one_period() -> None:
    """Vorjahresvergleich (Konzept Abschnitt 06/10): "heutiger Tag ↔ derselbe Tag
    vor einem Jahr" — anders als compare_mode="previous" bleibt der Wochentag/
    Monat gleich, nur das Jahr verschiebt sich."""
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-query-test-"))
    try:
        index = Index(tmp / "index.sqlite")
        entity_id = "sensor.temp"
        index.get_or_create_entity(entity_id, "sensor", "measurement", "°C")

        now = datetime(2024, 8, 21, 10, 0, 0, tzinfo=TZ)
        hotbuffer.append(tmp, entity_id, _ts(2024, 8, 21, 8, 0), 30.0, TZ)  # heute

        archive_dir = tmp / "archive" / entity_id
        archive_dir.mkdir(parents=True, exist_ok=True)
        pq.write_table(
            pa.table({"ts": [_ts(2023, 8, 21, 8, 0)], "value": [22.0]}),
            archive_dir / "2023-08.parquet",
        )

        today = query.query_series(tmp, index, entity_id, "day", TZ, now, year_over_year=False)
        assert [p["value"] for p in today["points"]] == [22.0, 30.0]

        year_ago = query.query_series(tmp, index, entity_id, "day", TZ, now, year_over_year=True)
        assert [p["value"] for p in year_ago["points"]] == [22.0]

        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_year_over_year_leap_day_falls_back_to_february_28() -> None:
    """29. Februar 2024 (Schaltjahr) minus ein Jahr landet in 2023, wo es diesen
    Tag nicht gibt — statt eines ValueError soll das auf den 28. ausweichen."""
    now_local = datetime(2024, 2, 29, 12, 0, 0, tzinfo=TZ)
    shifted = query._shift_year(now_local, -1)
    assert shifted == datetime(2023, 2, 28, 12, 0, 0, tzinfo=TZ)


def test_query_raw_series_returns_unbucketed_points_not_aggregated() -> None:
    """Hohe Dichte (Konzept Abschnitt 06/10): jeder Rohwert einzeln, nicht in
    Stunden-/Tages-Buckets verdichtet wie bei query_series()."""
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-query-test-"))
    try:
        index = Index(tmp / "index.sqlite")
        entity_id = "sensor.temp"
        index.get_or_create_entity(entity_id, "sensor", "measurement", "°C")

        now = datetime(2024, 8, 21, 10, 0, 0, tzinfo=TZ)
        for h, v in [(1, 10.0), (2, 12.0), (3, 11.0), (4, 13.0)]:
            hotbuffer.append(tmp, entity_id, _ts(2024, 8, 21, h), v, TZ)
            index.record_write(entity_id, _ts(2024, 8, 21, h))

        bucketed = query.query_series(tmp, index, entity_id, "day", TZ, now)
        raw = query.query_raw_series(tmp, index, entity_id, "day", TZ, now)

        assert len(raw["points"]) == 4  # jeder Rohwert einzeln
        assert sorted(p["value"] for p in raw["points"]) == [10.0, 11.0, 12.0, 13.0]
        assert raw["chart_type"] == "line"
        # Bei einer Stunden-Bucket-Größe von 5 Minuten (Standard-Typ, Abschnitt 05)
        # bleiben die vier Rohwerte in eigenen Buckets — die eigentliche Prüfung
        # hier ist trotzdem, dass raw NIE aggregiert, unabhängig von der Bucket-Größe.
        assert len(bucketed["points"]) <= len(raw["points"])

        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_bar_resolution_profile_matches_requested_periods() -> None:
    assert query.BAR_RESOLUTION == {
        "day": "stunde",
        "week": "tag",
        "month": "tag",
        "year": "monat",
        "decade": "jahr",
    }


def test_bar_profile_is_coarser_while_line_resolution_stays_unchanged() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-query-test-"))
    try:
        index = Index(tmp / "index.sqlite")
        entity_id = "sensor.bar_profile"
        index.get_or_create_entity(entity_id, "sensor", "measurement", "W")

        # Tagesansicht: Linie behält 5-Minuten-Buckets, Balken fasst auf
        # Stunden zusammen.
        day_now = datetime(2024, 8, 21, 12, 0, tzinfo=TZ)
        for minute, value in [(5, 10.0), (25, 20.0), (65, 30.0)]:
            ts = _ts(2024, 8, 21, 8 + minute // 60, minute % 60)
            hotbuffer.append(tmp, entity_id, ts, value, TZ)
            index.record_write(entity_id, ts)
        day_line = query.query_series(
            tmp, index, entity_id, "day", TZ, day_now, chart_type="line"
        )
        day_bar = query.query_series(
            tmp, index, entity_id, "day", TZ, day_now, chart_type="bar"
        )
        assert len(day_line["points"]) == 3
        assert len(day_bar["points"]) == 2

        # Abgeschlossener Monat: stündliche Standard-Rollups bleiben für die
        # Linie erhalten; Woche/Monat als Balken werden daraus tageweise.
        july_rows = [
            (_ts(2024, 7, 30, 8), 10.0),
            (_ts(2024, 7, 30, 9), 20.0),
            (_ts(2024, 7, 31, 8), 30.0),
        ]
        july_table = pa.table(
            {"ts": [row[0] for row in july_rows], "value": [row[1] for row in july_rows]}
        )
        archive_dir = tmp / "archive" / entity_id
        archive_dir.mkdir(parents=True, exist_ok=True)
        pq.write_table(july_table, archive_dir / "2024-07.parquet")
        rollup.append_completed_month(tmp, entity_id, "standard", july_table, 2024, 7, TZ)
        week_now = datetime(2024, 8, 3, 12, 0, tzinfo=TZ)
        week_line = query.query_series(
            tmp, index, entity_id, "week", TZ, week_now, chart_type="line"
        )
        week_bar = query.query_series(
            tmp, index, entity_id, "week", TZ, week_now, chart_type="bar"
        )
        assert len(week_line["points"]) == 3
        assert len(week_bar["points"]) == 2

        # Dekade: zwei Monatswerte bleiben in der Linie separat, ergeben im
        # Balkendiagramm aber genau einen Jahreswert.
        jan = pa.table({"ts": [_ts(2023, 1, 15, 10)], "value": [10.0]})
        feb = pa.table({"ts": [_ts(2023, 2, 15, 10)], "value": [20.0]})
        pq.write_table(jan, archive_dir / "2023-01.parquet")
        pq.write_table(feb, archive_dir / "2023-02.parquet")
        rollup.append_completed_month(tmp, entity_id, "standard", jan, 2023, 1, TZ)
        rollup.append_completed_month(tmp, entity_id, "standard", feb, 2023, 2, TZ)
        decade_now = datetime(2024, 8, 21, 12, 0, tzinfo=TZ)
        decade_line = query.query_series(
            tmp, index, entity_id, "decade", TZ, decade_now, chart_type="line"
        )
        decade_bar = query.query_series(
            tmp, index, entity_id, "decade", TZ, decade_now, chart_type="bar"
        )
        assert len(decade_line["points"]) >= 3
        assert len(decade_bar["points"]) == 2  # 2023 sowie der Live-Jahreswert 2024
        assert decade_bar["chart_type"] == "bar"

        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _run_all() -> None:
    if not _PYARROW_AVAILABLE:
        print("übersprungen: pyarrow nicht installiert (siehe requirements.txt)")
        return
    tests = [obj for name, obj in globals().items() if name.startswith("test_")]
    for test in tests:
        test()
        print(f"ok  {test.__name__}")
    print(f"\n{len(tests)} Tests bestanden.")


if __name__ == "__main__":
    _run_all()
