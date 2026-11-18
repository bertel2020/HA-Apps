"""Tests für app/storage/hotbuffer.py — insbesondere Fehlertoleranz beim
Lesen: nach einem unsauberen Absturz kann das Dateisystem den Rest einer
noch nicht geflushten Zeile als Nullbytes zurückliefern (ext4 u. a. nach
Stromausfall/hartem Kill)."""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from _paths import ADDON  # noqa: F401  (stellt sys.path sicher)

from app.storage import hotbuffer


def test_a_corrupt_line_is_skipped_not_fatal(caplog) -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-hotbuffer-test-"))
    try:
        path = tmp / "sensor.test-2026-09.csv"
        # Realistisches Muster aus einem echten Vorfall: die Zeile hatte ein
        # Komma (sonst würde schon der bestehende len(parts)<2-Filter greifen,
        # ohne den neuen Fix zu prüfen), aber das erste Feld ist bis auf einen
        # überlebenden Zahlen-Rest am Ende mit Nullbytes überschrieben.
        kaputte_zeile = "\x00" * 200 + "1789930796.420404,abc"
        path.write_text(
            "1758470000.0,21.5\n"
            f"{kaputte_zeile}\n"
            "1758470060.0,21.7\n",
            encoding="utf-8",
        )

        with caplog.at_level(logging.WARNING, logger="app.storage.hotbuffer"):
            rows = hotbuffer.read_rows(path)

        # Die kaputte Zeile fehlt einfach — nicht rekonstruierbar —, die beiden
        # gültigen Nachbarzeilen bleiben aber lesbar statt dass die ganze Datei
        # (und jeder Aufrufer: Abfrage, Wartungsplaner, Crash-Reconciliation)
        # an einer einzigen Zeile scheitert.
        assert rows == [(1758470000.0, 21.5), (1758470060.0, 21.7)]
        assert "event=hotbuffer_corrupt_line" in caplog.text
        assert str(path) in caplog.text
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def test_leading_nul_bytes_are_stripped_and_the_value_is_rescued(caplog) -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-hotbuffer-test-"))
    try:
        path = tmp / "sensor.test-2026-11.csv"
        path.write_text(
            "1758470000.0,21.5\n"
            "\x00\x00\x00\x001758470030.5,26.9,7d\n"
            "1758470060.0,21.7\n",
            encoding="utf-8",
        )
        with caplog.at_level(logging.WARNING, logger="app.storage.hotbuffer"):
            rows = hotbuffer.read_rows(path)
        assert rows == [(1758470000.0, 21.5), (1758470030.5, 26.9), (1758470060.0, 21.7)]
        assert "event=hotbuffer_corrupt_line" not in caplog.text
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)


def test_a_file_of_only_corrupt_lines_yields_no_rows_and_no_crash() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-hotbuffer-test-"))
    try:
        path = tmp / "sensor.test-2026-09.csv"
        path.write_text("\x00" * 50 + ",1.0\n", encoding="utf-8")
        assert hotbuffer.read_rows(path) == []
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
