"""Tests für app/storage/symcon_import.py — Scan/Vorschau, Plausibilitäts-
prüfung und den nie-destruktiven Import in bestehende Zeitarchiv-Entitäten.

Diese Tests laufen gegen selbst konstruierte CSV-Fixtures nach dem im Konzept
beschriebenen und gegen echte Symcon-Exporte verifizierten Rohdatenformat
(kommagetrennt, ts,value, Unix-Sekunden — siehe Konzept Abschnitt 04)."""

from __future__ import annotations

import shutil
import sys
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    import pyarrow as pa
    import pyarrow.parquet as pq

    from app.limits import MAX_IMPORT_ROWS_PER_ENTITY
    from app.storage import hotbuffer, symcon_import
    from app.storage.index import Index

    _PYARROW_AVAILABLE = True
except ImportError:
    _PYARROW_AVAILABLE = False

TZ = ZoneInfo("Europe/Berlin")


def test_import_limit_allows_ten_million_rows_per_entity() -> None:
    assert MAX_IMPORT_ROWS_PER_ENTITY == 10_000_000


def _ts(y, m, d, h=12, mi=0, s=0) -> float:
    return datetime(y, m, d, h, mi, s, tzinfo=TZ).timestamp()


def _write_csv(path: Path, rows: list[tuple]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(",".join(str(v) for v in row) + "\n")


def test_scan_source_groups_files_by_variable_id_and_ignores_non_numeric_names() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-symcon-test-"))
    try:
        _write_csv(tmp / "2024" / "07" / "24816.csv", [(_ts(2024, 7, 1), 21.0), (_ts(2024, 7, 2), 21.5)])
        _write_csv(tmp / "2024" / "08" / "24816.csv", [(_ts(2024, 8, 1), 22.0)])
        _write_csv(tmp / "config.csv", [("irrelevant", "nicht eine variable")])  # nicht-numerischer Name

        variables = symcon_import.scan_source(tmp)
        assert [v.variable_id for v in variables] == ["24816"]
        var = variables[0]
        assert len(var.files) == 2
        assert var.row_count == 3
        assert var.min_value == 21.0
        assert var.max_value == 22.0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_implausible_and_malformed_rows_are_skipped_not_the_whole_file() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-symcon-test-"))
    try:
        _write_csv(
            tmp / "31090.csv",
            [
                (_ts(2024, 7, 1), 4.2),
                ("nicht-numerisch", 5.0),  # kaputte Zeile
                (99999999999999, 6.0),  # unplausibler Zeitstempel (weit außerhalb 2000–2100)
                (_ts(2024, 7, 2), 6.1),
            ],
        )
        variables = symcon_import.scan_source(tmp)
        assert len(variables) == 1
        var = variables[0]
        assert var.readable is True
        assert var.row_count == 2  # nur die zwei plausiblen Zeilen
        assert var.min_value == 4.2
        assert var.max_value == 6.1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_bool_values_are_normalized_to_1_0_like_the_ha_integration_does() -> None:
    """"true"/"false" (und die deutschen Varianten) werden wie beim Live-
    Schreibpfad (custom_components/zeitarchiv/events.py) auf 1.0/0.0
    normalisiert, statt als unlesbare Zeile übersprungen zu werden."""
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-symcon-test-"))
    try:
        _write_csv(
            tmp / "555.csv",
            [
                (_ts(2024, 7, 1), "true"),
                (_ts(2024, 7, 2), "false"),
                (_ts(2024, 7, 3), "WAHR"),
                (_ts(2024, 7, 4), "Falsch"),
            ],
        )
        variables = symcon_import.scan_source(tmp)
        var = variables[0]
        assert var.readable is True
        assert var.row_count == 4
        assert var.min_value == 0.0
        assert var.max_value == 1.0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_variable_with_no_readable_rows_is_marked_unreadable() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-symcon-test-"))
    try:
        _write_csv(tmp / "1234.csv", [("kaputt", "auch kaputt")])
        variables = symcon_import.scan_source(tmp)
        assert len(variables) == 1
        assert variables[0].readable is False
        assert variables[0].error
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_symcon_import_reports_malformed_source_rows() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-symcon-test-"))
    try:
        index = Index(tmp / "index.sqlite")
        entity_id = "sensor.test"
        index.get_or_create_entity(entity_id, "sensor", "measurement", None)
        source = tmp / "symcon"
        _write_csv(source / "123.csv", [(_ts(2024, 1, 1), 1), ("kaputt", "wert")])
        variable = symcon_import.scan_source(source)[0]
        result = symcon_import.import_variable(tmp, index, variable, entity_id, TZ)
        assert variable.skipped_rows == 1
        assert result.skipped_rows == 1
        assert result.source_rows == 1
        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_scan_source_missing_directory_returns_empty_list() -> None:
    assert symcon_import.scan_source(Path("/nonexistent/zeitarchiv-symcon-path")) == []


def test_import_variable_writes_months_entirely_before_existing_first_ts() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-symcon-test-"))
    try:
        index = Index(tmp / "index.sqlite")
        entity_id = "sensor.wohnzimmer_temperatur"
        index.get_or_create_entity(entity_id, "sensor", "measurement", "°C")
        # Zeitarchiv hat schon Live-Daten ab August 2024 — Symcon-Import soll nur
        # die Monate DAVOR (Juni/Juli) auffüllen.
        index.record_write(entity_id, _ts(2024, 8, 5))

        source = tmp / "symcon"
        _write_csv(source / "2024" / "06" / "50001.csv", [(_ts(2024, 6, 10), 20.0), (_ts(2024, 6, 20), 20.5)])
        _write_csv(source / "2024" / "07" / "50001.csv", [(_ts(2024, 7, 10), 21.0)])

        variables = symcon_import.scan_source(source)
        var = variables[0]
        result = symcon_import.import_variable(tmp, index, var, entity_id, TZ)

        assert sorted(result.imported_months) == ["2024-06", "2024-07"]
        assert result.rows_imported == 3

        archive_dir = tmp / "archive" / entity_id
        assert (archive_dir / "2024-06.parquet").exists()
        assert (archive_dir / "2024-07.parquet").exists()

        table = pq.read_table(archive_dir / "2024-06.parquet")
        assert table.num_rows == 2

        entity = index.get_entity(entity_id)
        assert entity["first_ts"] == _ts(2024, 6, 10)
        assert entity["row_count"] == 1 + 3  # der ursprüngliche record_write + 3 importierte

        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_import_variable_applies_conversion_factor_to_all_values() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-symcon-test-"))
    try:
        index = Index(tmp / "index.sqlite")
        entity_id = "sensor.sonnenwert"
        index.get_or_create_entity(entity_id, "sensor", "measurement", "lx")
        index.record_write(entity_id, _ts(2024, 8, 5))
        source = tmp / "symcon"
        _write_csv(
            source / "2024" / "06" / "50002.csv",
            [(_ts(2024, 6, 10), 12.5), (_ts(2024, 6, 20), 20.0)],
        )
        variable = symcon_import.scan_source(source)[0]

        plan = symcon_import.plan_import(
            tmp, index, variable, entity_id, TZ, factor=1000
        )
        result = symcon_import.import_variable(
            tmp, index, variable, entity_id, TZ, factor=1000
        )

        assert plan.factor == 1000
        assert result.factor == 1000
        table = pq.read_table(tmp / "archive" / entity_id / "2024-06.parquet")
        assert table.column("value").to_pylist() == [12500.0, 20000.0]
        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_import_variable_rejects_zero_or_non_finite_factor() -> None:
    variable = symcon_import.SymconVariable(variable_id="1")
    for factor in (0, float("nan"), float("inf")):
        try:
            symcon_import._scaled_raw_rows(variable, factor)
            assert False, f"Faktor {factor} hätte abgelehnt werden müssen"
        except ValueError:
            pass


def test_import_variable_never_overwrites_a_month_with_an_existing_archive_file() -> None:
    """Die einzige harte Skip-Grenze, die bleibt: existiert schon eine
    Archivdatei für den Monat, wird sie nie angefasst — anders als beim
    laufenden Monat (siehe test_import_variable_merges_..._below) gibt es hier
    keine Zusammenführung, weil ein bereits archivierter Monat kein Hot-Buffer-
    Äquivalent zum Duplikat-Abgleich mehr hat."""
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-symcon-test-"))
    try:
        index = Index(tmp / "index.sqlite")
        entity_id = "sensor.pv_ertrag"
        index.get_or_create_entity(entity_id, "sensor", "total_increasing", "kWh")
        index.record_write(entity_id, _ts(2024, 9, 1))

        archive_dir = tmp / "archive" / entity_id
        archive_dir.mkdir(parents=True, exist_ok=True)
        (archive_dir / "2024-08.parquet").write_bytes(b"not-really-parquet-but-must-survive-untouched")

        source = tmp / "symcon"
        _write_csv(source / "777.csv", [(_ts(2024, 7, 15), 100.0), (_ts(2024, 8, 2), 105.0)])

        variables = symcon_import.scan_source(source)
        result = symcon_import.import_variable(tmp, index, variables[0], entity_id, TZ)

        assert result.imported_months == ["2024-07"]
        assert result.skipped_months == ["2024-08"]
        assert (archive_dir / "2024-08.parquet").read_bytes() == b"not-really-parquet-but-must-survive-untouched"

        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_import_rows_can_opt_in_to_filling_gaps_in_existing_archive_month() -> None:
    """Der HA-Import darf auf ausdrücklichen Wunsch archivierte Monate
    ergänzen, ohne einen vorhandenen Wert am selben Zeitstempel zu ersetzen."""
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-ha-existing-month-test-"))
    try:
        index = Index(tmp / "index.sqlite")
        entity_id = "sensor.temperatur"
        index.get_or_create_entity(entity_id, "sensor", "measurement", "°C")

        archive_dir = tmp / "archive" / entity_id
        archive_dir.mkdir(parents=True, exist_ok=True)
        existing_ts = _ts(2024, 7, 10)
        archive_path = archive_dir / "2024-07.parquet"
        pq.write_table(pa.table({
            "ts": [existing_ts],
            "value": [20.0],
            "event_id": ["existing-event"],
        }), archive_path)
        index.record_write(entity_id, _ts(2024, 8, 1))

        source_rows = [
            (existing_ts, 999.0),
            (_ts(2024, 7, 11), 21.0),
        ]
        default_plan = symcon_import.plan_import_rows(
            tmp, index, source_rows, entity_id, TZ
        )
        assert default_plan.months_to_skip == ["2024-07"]

        plan = symcon_import.plan_import_rows(
            tmp, index, source_rows, entity_id, TZ, include_existing_months=True
        )
        assert plan.months_to_update == ["2024-07"]
        assert plan.rows_to_update == 1

        result = symcon_import.import_rows(
            tmp, index, source_rows, entity_id, TZ, include_existing_months=True
        )
        assert result.updated_months == ["2024-07"]
        assert result.rows_updated == 1
        assert result.duplicate_rows == 1
        assert sorted(zip(
            pq.read_table(archive_path).column("ts").to_pylist(),
            pq.read_table(archive_path).column("value").to_pylist(),
        )) == [(existing_ts, 20.0), (_ts(2024, 7, 11), 21.0)]
        assert pq.read_table(archive_path).column("event_id").to_pylist() == [
            "existing-event", None
        ]

        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_import_variable_merges_current_month_and_skips_exact_duplicate_timestamps() -> None:
    """Der laufende Monat (keine Archivdatei, überlappt den ersten vorhandenen
    Wert) wird jetzt komplett importiert statt übersprungen — Zeilen, deren
    Zeitstempel exakt mit einer schon im Hot Buffer vorhandenen Zeile
    übereinstimmt, werden dabei als Duplikat ausgelassen."""
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-symcon-test-"))
    try:
        index = Index(tmp / "index.sqlite")
        entity_id = "sensor.pv_leistung"
        index.get_or_create_entity(entity_id, "sensor", "measurement", "W")

        now = datetime.now(TZ)
        year, month = now.year, now.month
        label = f"{year:04d}-{month:02d}"
        existing_ts = _ts(year, month, 20, 10)
        hotbuffer.append(tmp, entity_id, existing_ts, 200.0, TZ)
        index.record_write(entity_id, existing_ts)  # first_ts = 21.08., mitten im Monat

        # Symcon deckt den ganzen August ab: ein Wert exakt zur schon
        # vorhandenen Zeit (Duplikat, muss übersprungen werden), einer kurz
        # danach (neu) und mehrere Tage VOR dem bisherigen ersten Live-Wert
        # (der eigentliche Lückenfüller, den die alte Skip-ganzer-Monat-Logik
        # bisher verworfen hätte).
        source = tmp / "symcon"
        _write_csv(
            source / "999.csv",
            [
                (_ts(year, month, 1, 8), 150.0),  # vor dem bisherigen ersten Wert
                (existing_ts, 200.0),  # exaktes Duplikat -> übersprungen
                (_ts(year, month, 20, 11), 210.0),  # neu, nach dem Duplikat
            ],
        )

        variables = symcon_import.scan_source(source)
        var = variables[0]

        plan = symcon_import.plan_import(tmp, index, var, entity_id, TZ)
        assert plan.months_to_merge == [label]
        assert plan.rows_to_merge == 2  # Dry Run zählt das Duplikat schon korrekt nicht mit

        result = symcon_import.import_variable(tmp, index, var, entity_id, TZ)

        assert result.imported_months == []
        assert result.merged_months == [label]
        assert result.rows_merged == 2  # Duplikat nicht mitgezählt
        assert not (tmp / "archive" / entity_id / f"{label}.parquet").exists()  # laufender Monat bleibt im Hot Buffer

        merged_rows = sorted(hotbuffer.read_rows(hotbuffer.hot_path(tmp, entity_id, existing_ts, TZ)))
        assert merged_rows == sorted([(_ts(year, month, 1, 8), 150.0), (existing_ts, 200.0), (_ts(year, month, 20, 11), 210.0)])

        entity = index.get_entity(entity_id)
        assert entity["first_ts"] == _ts(year, month, 1, 8)  # nach vorne gezogen
        assert entity["row_count"] == 1 + 2  # ursprünglicher record_write + 2 neue (nicht 3 — Duplikat zählt nicht mit)

        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_current_month_archive_is_recovered_into_hot_buffer() -> None:
    """Eine durch eine ältere Importlogik erzeugte Archivdatei des laufenden
    Monats wird vollständig in den Hot Buffer zurückgeführt und entfernt."""
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-current-archive-repair-test-"))
    try:
        index = Index(tmp / "index.sqlite")
        entity_id = "sensor.current_repair"
        index.get_or_create_entity(entity_id, "sensor", "measurement", "W")
        now = datetime.now(TZ)
        year, month = now.year, now.month
        label = f"{year:04d}-{month:02d}"
        duplicate_ts = _ts(year, month, 2, 8)
        archive_only_ts = _ts(year, month, 3, 8)
        source_only_ts = _ts(year, month, 4, 8)

        hotbuffer.append(tmp, entity_id, duplicate_ts, 20.0, TZ, event_id="hot-event")
        index.record_write(entity_id, duplicate_ts)
        archive_dir = tmp / "archive" / entity_id
        archive_dir.mkdir(parents=True, exist_ok=True)
        archive_path = archive_dir / f"{label}.parquet"
        pq.write_table(pa.table({
            "ts": [duplicate_ts, archive_only_ts],
            "value": [999.0, 30.0],
            "event_id": ["archive-duplicate", "archive-event"],
        }), archive_path)
        index.add_size_bytes(entity_id, archive_path.stat().st_size)

        source_rows = [(duplicate_ts, 888.0), (source_only_ts, 40.0)]
        plan = symcon_import.plan_import_rows(tmp, index, source_rows, entity_id, TZ)
        assert plan.months_to_merge == [label]
        assert plan.months_to_update == []
        assert plan.current_archives_to_repair == [label]
        assert plan.rows_to_recover == 1
        assert plan.rows_to_merge == 1

        result = symcon_import.import_rows(tmp, index, source_rows, entity_id, TZ)
        assert result.repaired_current_months == [label]
        assert result.rows_recovered == 1
        assert result.rows_merged == 1
        assert not archive_path.exists()
        assert sorted(hotbuffer.read_rows(hotbuffer.hot_path(tmp, entity_id, duplicate_ts, TZ))) == [
            (duplicate_ts, 20.0),
            (archive_only_ts, 30.0),
            (source_only_ts, 40.0),
        ]
        records = sorted(
            hotbuffer.read_records(hotbuffer.hot_path(tmp, entity_id, duplicate_ts, TZ))
        )
        assert records == [
            (duplicate_ts, 20.0, "hot-event"),
            (archive_only_ts, 30.0, "archive-event"),
            (source_only_ts, 40.0, None),
        ]

        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_past_month_without_archive_is_archived_not_written_to_stale_hot_buffer() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-past-import-test-"))
    try:
        index = Index(tmp / "index.sqlite")
        entity_id = "sensor.past_gap"
        index.get_or_create_entity(entity_id, "sensor", "measurement", "°C")
        # first_ts liegt mitten in einem historischen Monat. Ohne Archivdatei
        # muss dieser Monat trotzdem als abgeschlossenes Archiv angelegt werden.
        index.record_write(entity_id, _ts(2024, 7, 20))
        rows = [(_ts(2024, 7, 1), 10.0), (_ts(2024, 7, 21), 11.0)]

        plan = symcon_import.plan_import_rows(tmp, index, rows, entity_id, TZ)
        assert plan.months_to_import == ["2024-07"]
        assert plan.months_to_merge == []
        result = symcon_import.import_rows(tmp, index, rows, entity_id, TZ)
        assert result.imported_months == ["2024-07"]
        assert (tmp / "archive" / entity_id / "2024-07.parquet").exists()

        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_plan_import_matches_what_import_variable_actually_does() -> None:
    """Dry Run und tatsächlicher Import teilen sich dieselbe Klassifizierung
    (_classify_months) — dieser Test stellt sicher, dass sie nicht auseinanderlaufen."""
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-symcon-test-"))
    try:
        index = Index(tmp / "index.sqlite")
        entity_id = "sensor.aussentemperatur"
        index.get_or_create_entity(entity_id, "sensor", "measurement", "°C")
        index.record_write(entity_id, _ts(2024, 9, 1))

        source = tmp / "symcon"
        _write_csv(source / "42.csv", [(_ts(2024, 7, 1), 10.0), (_ts(2024, 8, 1), 12.0)])
        variables = symcon_import.scan_source(source)
        var = variables[0]

        plan = symcon_import.plan_import(tmp, index, var, entity_id, TZ)
        result = symcon_import.import_variable(tmp, index, var, entity_id, TZ)

        assert plan.months_to_import == result.imported_months
        assert plan.months_to_merge == result.merged_months
        assert plan.months_to_skip == result.skipped_months
        assert plan.rows_to_import == result.rows_imported
        assert plan.rows_to_merge == result.rows_merged

        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_extract_zip_replaces_previous_contents() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-symcon-test-"))
    try:
        dest = tmp / "extracted"
        dest.mkdir()
        (dest / "stale.txt").write_text("alter Stand")

        zip_path = tmp / "upload.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("2024/07/1.csv", "1719792000,21.4\n")

        symcon_import.extract_zip(zip_path, dest)

        assert not (dest / "stale.txt").exists()
        assert (dest / "2024" / "07" / "1.csv").exists()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_extract_zip_rejects_path_traversal() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-symcon-test-"))
    try:
        dest = tmp / "extracted"
        zip_path = tmp / "evil.zip"
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("../../etc/passwd", "böswilliger Inhalt")

        try:
            symcon_import.extract_zip(zip_path, dest)
            assert False, "sollte ValueError werfen (Zip-Slip)"
        except ValueError:
            pass
        # Zielverzeichnis darf durch den Versuch nicht (teilweise) beschrieben worden sein.
        assert not (tmp / "etc").exists()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_extract_zip_rejects_member_count_limit() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-symcon-test-"))
    try:
        archive = tmp / "many.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("1.csv", "1700000000,1\n")
            zf.writestr("2.csv", "1700000001,2\n")
        try:
            symcon_import.extract_zip(archive, tmp / "dest", max_members=1)
            raise AssertionError("ZIP-Eintragslimit wurde nicht durchgesetzt")
        except ValueError as exc:
            assert "zu viele Einträge" in str(exc)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_extract_zip_rejects_uncompressed_size_limit() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-symcon-test-"))
    try:
        archive = tmp / "large.zip"
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED) as zf:
            zf.writestr("1.csv", "x" * 101)
        try:
            symcon_import.extract_zip(
                archive, tmp / "dest", max_uncompressed_bytes=100
            )
            raise AssertionError("ZIP-Größenlimit wurde nicht durchgesetzt")
        except ValueError as exc:
            assert "Größe" in str(exc)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_delete_source_removes_directory() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-symcon-test-"))
    try:
        target = tmp / "to_delete"
        target.mkdir()
        (target / "file.csv").write_text("1,2\n")
        symcon_import.delete_source(target)
        assert not target.exists()
        symcon_import.delete_source(target)  # zweiter Aufruf auf nicht-existente Datei: kein Fehler
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_import_variable_unknown_entity_raises() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-symcon-test-"))
    try:
        index = Index(tmp / "index.sqlite")
        source = tmp / "symcon"
        _write_csv(source / "1.csv", [(_ts(2024, 6, 1), 1.0)])
        variables = symcon_import.scan_source(source)
        try:
            symcon_import.import_variable(tmp, index, variables[0], "sensor.unbekannt", TZ)
            assert False, "sollte ValueError werfen"
        except ValueError:
            pass
        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_extract_variable_names_reads_id_prefixed_keys() -> None:
    """Fixture ist der vom Nutzer bereitgestellte echte settings.json-Auszug."""
    raw = """
    {
        "ID56436": {
            "data": {
                "attributes": {},
                "configuration": {
                    "ByteOrder": 0,
                    "DataType": 5,
                    "EmulateStatus": true,
                    "Factor": 0.1,
                    "Length": 0,
                    "Poller": 300000,
                    "ReadAddress": 102,
                    "ReadFunctionCode": 3,
                    "WriteAddress": 102,
                    "WriteFunctionCode": 16
                },
                "connectionID": 31365,
                "lastChange": 1703751030,
                "moduleID": "{CB197E50-273D-4535-8C91-BB35273E3CA5}",
                "moduleName": "ModBus Address",
                "moduleType": 3
            },
            "disabled": false,
            "hidden": false,
            "icon": "",
            "ident": "",
            "info": "",
            "name": "Temperatur Fortluft",
            "parentID": 52835,
            "position": 0,
            "readOnly": false,
            "type": 1
        },
        "ID99": {"name": ""},
        "ID100": {"disabled": false},
        "notAnId": {"name": "sollte ignoriert werden"}
    }
    """
    names = symcon_import.extract_variable_names(raw)
    # parentID 52835 kommt in diesem Auszug selbst nicht als Objekt vor -> kein
    # Parent-Name auflösbar, bleibt None statt eines Fehlers.
    assert names == {
        "56436": {"name": "Temperatur Fortluft", "parent": None, "unit": None}
    }


def test_extract_variable_names_returns_empty_dict_for_non_object_json() -> None:
    assert symcon_import.extract_variable_names("[1, 2, 3]") == {}


def test_extract_variable_names_reads_from_nested_objects_key() -> None:
    """Ein echter Symcon-Export ("Einstellungen exportieren") verschachtelt den
    Objektbaum unter einem Top-Level-Schlüssel "objects" (neben "compatibility"/
    "options"/"profiles") statt die ID-Einträge direkt auf oberster Ebene zu
    haben — gegen genau diese Form am echten, vom Nutzer bereitgestellten
    Export verifiziert."""
    raw = """
    {
        "compatibility": {"version": 1},
        "options": {},
        "profiles": {},
        "objects": {
            "ID10127": {
                "data": {"type": 2, "value": 22.1},
                "disabled": false,
                "hidden": false,
                "icon": "",
                "ident": "",
                "info": "",
                "name": "Temperatur IST",
                "parentID": 15607,
                "position": 0,
                "readOnly": false,
                "type": 2
            },
            "ID0": {"name": "Symcon"}
        }
    }
    """
    names = symcon_import.extract_variable_names(raw)
    # ID15607 (das parentID von ID10127) kommt in diesem Auszug nicht vor ->
    # kein Parent-Name auflösbar. ID0 hat gar kein parentID-Feld -> ebenfalls None.
    assert names == {
        "10127": {"name": "Temperatur IST", "parent": None, "unit": None},
        "0": {"name": "Symcon", "parent": None, "unit": None},
    }


def test_extract_variable_names_resolves_direct_parent_name() -> None:
    """Nachgebaut aus der echten Objekt-Kette eines Nutzer-Exports: die Variable
    "Temperatur IST" hängt unter der Kategorie "Bad", die wiederum unter
    "Heizung" hängt — nur EINE Ebene (der direkte Parent "Bad") wird aufgelöst,
    nicht der volle Pfad bis "Heizung"."""
    raw = """
    {
        "objects": {
            "ID10127": {"name": "Temperatur IST", "parentID": 15607, "type": 2},
            "ID15607": {"name": "Bad", "parentID": 23419, "type": 1},
            "ID23419": {"name": "Heizung", "parentID": 0, "type": 0},
            "ID99": {"name": "Ohne Eltern", "parentID": 0, "type": 2},
            "ID100": {"name": "Verwaistes Kind", "parentID": 404, "type": 2}
        }
    }
    """
    names = symcon_import.extract_variable_names(raw)
    assert names["10127"] == {"name": "Temperatur IST", "parent": "Bad", "unit": None}
    assert names["15607"] == {"name": "Bad", "parent": "Heizung", "unit": None}
    # parentID 0 heißt Wurzel -> kein Parent-Name, auch wenn "ID0" existieren würde.
    assert names["99"] == {"name": "Ohne Eltern", "parent": None, "unit": None}
    # parentID 404 verweist auf ein nicht existierendes Objekt -> None statt Fehler.
    assert names["100"] == {"name": "Verwaistes Kind", "parent": None, "unit": None}


def test_extract_variable_names_resolves_units_from_settings_profiles() -> None:
    raw = """
    {
        "objects": {
            "ID1": {
                "name": "Temperatur",
                "data": {"customProfile": "Temperature", "profile": "~Watt"}
            },
            "ID2": {
                "name": "Leistung",
                "data": {"customProfile": "", "profile": "~Watt.3680"}
            },
            "ID3": {
                "name": "Ohne Einheit",
                "data": {"customProfile": "Switch", "profile": ""}
            }
        },
        "profiles": {
            "Temperature": {"prefix": "", "suffix": " °C"},
            "Switch": {"prefix": "", "suffix": ""}
        }
    }
    """

    names = symcon_import.extract_variable_names(raw)
    assert names["1"]["unit"] == "°C"
    assert names["2"]["unit"] == "W"
    assert names["3"]["unit"] is None


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
