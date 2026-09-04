"""Tests für app/storage/csv_import.py — Trennzeichen-/Kopfzeilen-
Erkennung, Vorschau, und den Zeilen-Parser mit freier Spalten-/Format-
Zuordnung (Konzept "Offene Punkte": eigener CSV-Import neben Symcon)."""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.storage import csv_import

TZ = ZoneInfo("Europe/Berlin")


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_sniff_delimiter_picks_the_most_frequent_candidate() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-csv-test-"))
    try:
        comma = tmp / "comma.csv"
        _write(comma, "ts,value\n1,2\n")
        assert csv_import.sniff_delimiter(comma) == ","

        semicolon = tmp / "semi.csv"
        _write(semicolon, "ts;value\n1;2\n")
        assert csv_import.sniff_delimiter(semicolon) == ";"

        tab = tmp / "tab.csv"
        _write(tab, "ts\tvalue\n1\t2\n")
        assert csv_import.sniff_delimiter(tab) == "\t"
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_sniff_has_header_detects_label_row_vs_pure_data() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-csv-test-"))
    try:
        with_header = tmp / "with_header.csv"
        _write(with_header, "timestamp,value\n1700000000,21.5\n1700000060,21.6\n")
        assert csv_import.sniff_has_header(with_header, ",") is True

        without_header = tmp / "without_header.csv"
        _write(without_header, "1700000000,21.5\n1700000060,21.6\n")
        assert csv_import.sniff_has_header(without_header, ",") is False
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_preview_splits_header_from_sample_rows() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-csv-test-"))
    try:
        path = tmp / "data.csv"
        _write(path, "Zeit,Wert\n1700000000,21.5\n1700000060,21.6\n1700000120,21.7\n")

        result = csv_import.preview(path, ",", has_header=True, sample_size=2)
        assert result.columns == ["Zeit", "Wert"]
        assert result.total_lines == 3
        assert result.sample_rows == [["1700000000", "21.5"], ["1700000060", "21.6"]]

        result_no_header = csv_import.preview(path, ",", has_header=False, sample_size=10)
        assert result_no_header.columns == ["Spalte 1", "Spalte 2"]
        assert result_no_header.total_lines == 4
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_preview_of_empty_file_returns_defaults() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-csv-test-"))
    try:
        path = tmp / "empty.csv"
        _write(path, "")

        result = csv_import.preview(path, ",", has_header=True, sample_size=8)
        assert result.columns == []
        assert result.column_count == 0
        assert result.total_lines == 0
        assert result.sample_rows == []
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_preview_column_count_accounts_for_ragged_rows_beyond_the_sample() -> None:
    """column_count/total_lines müssen den ganzen Durchlauf abdecken, nicht
    nur die materialisierten sample_rows (siehe PERFORMANCE.md, ZP-013 —
    preview() liest jetzt streamend statt alles als Liste zu halten)."""
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-csv-test-"))
    try:
        path = tmp / "ragged.csv"
        lines = ["Zeit,Wert\n"]
        for i in range(20):
            lines.append(f"{1700000000 + i},{i}\n")
        # Eine breitere Zeile weit hinter dem sample_size-Fenster (sample_size=2).
        lines.append("1700000999,99,extra\n")
        _write(path, "".join(lines))

        result = csv_import.preview(path, ",", has_header=True, sample_size=2)
        assert result.total_lines == 21
        assert result.column_count == 3
        assert result.columns == ["Zeit", "Wert", "Spalte 3"]
        assert len(result.sample_rows) == 2
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_parse_rows_unix_seconds_and_milliseconds() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-csv-test-"))
    try:
        path = tmp / "unix.csv"
        _write(path, "1700000000,21.5\n1700000060000,21.6\n")  # zweite Zeile absichtlich in ms

        seconds = csv_import.parse_rows(path, ",", False, 0, 1, "unix_s", "", TZ)
        assert seconds.rows[0] == (1700000000.0, 21.5)

        ms_path = tmp / "ms.csv"
        _write(ms_path, "1700000000000,21.5\n")
        ms = csv_import.parse_rows(ms_path, ",", False, 0, 1, "unix_ms", "", TZ)
        assert ms.rows[0] == (1700000000.0, 21.5)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_parse_rows_iso_and_custom_format_assume_configured_timezone_when_naive() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-csv-test-"))
    try:
        iso_path = tmp / "iso.csv"
        _write(iso_path, "2024-01-15 10:00:00,21.5\n")
        iso = csv_import.parse_rows(iso_path, ",", False, 0, 1, "iso", "", TZ)
        from datetime import datetime

        expected = datetime(2024, 1, 15, 10, 0, 0, tzinfo=TZ).timestamp()
        assert iso.rows[0] == (expected, 21.5)

        custom_path = tmp / "custom.csv"
        _write(custom_path, "15.01.2024 10:00:00,21.5\n")
        custom = csv_import.parse_rows(custom_path, ",", False, 0, 1, "custom", "%d.%m.%Y %H:%M:%S", TZ)
        assert custom.rows[0] == (expected, 21.5)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_parse_rows_accepts_german_decimal_comma_and_bool_synonyms() -> None:
    """Deutsche CSVs trennen typischerweise mit Semikolon statt Komma — gerade
    WEIL das Komma dort als Dezimaltrennzeichen gebraucht wird, wie hier."""
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-csv-test-"))
    try:
        path = tmp / "de.csv"
        _write(path, "1700000000;21,5\n1700000060;true\n1700000120;falsch\n")
        result = csv_import.parse_rows(path, ";", False, 0, 1, "unix_s", "", TZ)
        assert result.rows == [
            (1700000000.0, 21.5),
            (1700000060.0, 1.0),
            (1700000120.0, 0.0),
        ]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_parse_rows_skips_malformed_lines_instead_of_failing_the_whole_file() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-csv-test-"))
    try:
        path = tmp / "messy.csv"
        _write(
            path,
            "1700000000,21.5\n"  # gültig
            "kaputt\n"  # zu wenige Spalten
            "not_a_timestamp,21.6\n"  # Zeitstempel nicht parsbar
            "1700000200,not_a_value\n"  # Wert nicht parsbar
            "1700000100,21.7\n",  # gültig
        )
        result = csv_import.parse_rows(path, ",", False, 0, 1, "unix_s", "", TZ)
        assert result.rows == [(1700000000.0, 21.5), (1700000100.0, 21.7)]
        assert result.skipped == 3
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_parse_rows_sorts_output_by_timestamp() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-csv-test-"))
    try:
        path = tmp / "unsorted.csv"
        _write(path, "1700000200,3\n1700000000,1\n1700000100,2\n")
        result = csv_import.parse_rows(path, ",", False, 0, 1, "unix_s", "", TZ)
        assert [r[0] for r in result.rows] == [1700000000.0, 1700000100.0, 1700000200.0]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_parse_rows_rejects_more_than_configured_row_limit() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-csv-test-"))
    try:
        path = tmp / "too-many.csv"
        _write(path, "1700000000,1\n1700000060,2\n1700000120,3\n")
        try:
            csv_import.parse_rows(
                path, ",", False, 0, 1, "unix_s", "", TZ, max_rows=2
            )
            assert False, "ValueError erwartet"
        except ValueError as exc:
            assert "2" in str(exc)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _run_all() -> None:
    tests = [obj for name, obj in globals().items() if name.startswith("test_")]
    for test in tests:
        test()
        print(f"ok  {test.__name__}")
    print(f"\n{len(tests)} Tests bestanden.")


if __name__ == "__main__":
    _run_all()
