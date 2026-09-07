"""Die Eintragsgrenze für ZIPs muss auslösen können — und rechtzeitig (ZG-26).

`MAX_ZIP_MEMBERS` stand auf 500.000.000 und konnte damit nie greifen: ein
ZIP-Eintrag kostet auch leer 87,6 Byte, in die Uploadgrenze von 2 GiB passen
also höchstens rund 24,5 Millionen. Der Wert lag eine Zwanzigerpotenz darüber.

Wichtiger war aber, wo die Prüfung stand. Beide Aufrufer schrieben

    with zipfile.ZipFile(path) as zf:
        members = zf.infolist()
        if len(members) > MAX_ZIP_MEMBERS:

und der Konstruktor liest das komplette Zentralverzeichnis ein — gemessen
619 Byte je Eintrag, vollständig dort, `infolist()` danach kostet nichts mehr.
Ein bis an die Uploadgrenze gefülltes Archiv bräuchte damit rund 14,2 GiB
allein dafür, die Liste aufzubauen, die die Grenze anschließend zählen soll.
Der Prozess ist tot, bevor die Zeile mit dem Limit erreicht ist.

Diese Datei hält beides fest: dass die Grenze erreichbar ist, und dass sie
greift, bevor das Verzeichnis im Speicher landet.
"""

from __future__ import annotations

import tempfile
import zipfile
from pathlib import Path

import pytest

import _paths  # noqa: F401
from app.limits import MAX_ZIP_MEMBERS, MAX_ZIP_UPLOAD_BYTES
from app.storage import backup, symcon_import, zip_guard

#: Kleinstmögliche Kosten eines ZIP-Eintrags: lokaler Header (30) plus
#: Zentralverzeichniseintrag (46), dazu zweimal mindestens ein Zeichen Name.
MINIMUM_BYTES_PER_ENTRY = 78


def _zip_with(path: Path, entries: int) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        for number in range(entries):
            archive.writestr(str(number), b"")
    return path


def test_the_limit_is_low_enough_to_be_reachable_at_all() -> None:
    """Eine Grenze, die in kein zulässiges Archiv passt, ist keine Grenze.

    Genau das war der Zustand: 500.000.000 Einträge bräuchten mindestens 37 GiB,
    hochladen darf man 2.
    """
    passt_in_den_upload = MAX_ZIP_UPLOAD_BYTES // MINIMUM_BYTES_PER_ENTRY
    assert MAX_ZIP_MEMBERS < passt_in_den_upload, (
        f"{MAX_ZIP_MEMBERS:,} Einträge passen nicht in {MAX_ZIP_UPLOAD_BYTES:,} Byte — "
        f"die Grenze kann nie auslösen (Platz für höchstens {passt_in_den_upload:,})"
    )


def test_the_limit_still_admits_real_archives() -> None:
    """Gegenprobe zur Zusage darüber: nicht so eng, dass echte Bestände fallen.

    Gemessene Wirklichkeit: ein Zeitarchiv-Backup von 9 Entitäten und 3,6 GB
    enthält 1.395 Einträge, ein echter Symcon-Export 47.494.
    """
    assert MAX_ZIP_MEMBERS >= 10 * 47_494, (
        "unter einer Größenordnung Luft über dem größten gemessenen echten Bestand"
    )


@pytest.mark.parametrize("entries", [0, 1, 100, 65_535, 65_536, 70_000])
def test_the_entry_count_is_readable_without_opening_the_archive(entries: int) -> None:
    """Auch jenseits von 65.535, wo das Format auf Zip64 wechselt.

    Diese Zusage bewacht zugleich die private Schnittstelle, über die gezählt
    wird (`zipfile._EndRecData`): verschwindet sie in einer künftigen
    Python-Version, fällt hier ein Test um, statt dass die Prüfung still auf
    den langsamen Weg zurückfällt.
    """
    with tempfile.TemporaryDirectory() as tmp:
        path = _zip_with(Path(tmp) / "probe.zip", entries)
        with zipfile.ZipFile(path) as archive:
            tatsaechlich = len(archive.infolist())
        assert zip_guard.declared_entry_count(path) == tatsaechlich == entries


def test_an_unreadable_file_falls_back_instead_of_failing() -> None:
    """Keine Zahl heißt „weiß nicht", nicht „abgelehnt".

    Der Aufrufer prüft danach ohnehin noch einmal nach dem Öffnen; eine
    kaputte Datei soll dort ihren richtigen Fehler bekommen, nicht hier einen
    falschen.
    """
    with tempfile.TemporaryDirectory() as tmp:
        kein_zip = Path(tmp) / "kaputt.zip"
        kein_zip.write_bytes(b"das ist kein ZIP")
        assert zip_guard.declared_entry_count(kein_zip) is None
        assert zip_guard.declared_entry_count(Path(tmp) / "gibtsnicht.zip") is None
        # Und nichts davon wirft:
        zip_guard.ensure_entry_count_allowed(kein_zip, 1, "egal")


def test_too_many_entries_are_refused_before_the_directory_is_built(monkeypatch) -> None:
    """Die eigentliche Zusage — und sie prüft die Reihenfolge, nicht die Zahl.

    Statt ein 14-GiB-Archiv zu bauen, wird `zipfile.ZipFile` unbrauchbar
    gemacht. Läuft die Ablehnung trotzdem durch, kann sie den Konstruktor nicht
    angefasst haben. Dasselbe Mittel wie beim No-scan-Test der Aufnahme.
    """
    def kein_oeffnen(*args, **kwargs):
        raise AssertionError("ZipFile wurde geöffnet, obwohl die Grenze schon fiel")

    with tempfile.TemporaryDirectory() as tmp:
        path = _zip_with(Path(tmp) / "zu_viele.zip", 20)
        monkeypatch.setattr(zipfile, "ZipFile", kein_oeffnen)

        with pytest.raises(ValueError, match="zu viele"):
            zip_guard.ensure_entry_count_allowed(path, 5, "Backup enthält zu viele Dateien")


def test_both_callers_check_before_they_open(monkeypatch) -> None:
    """Beide Wege ins Archiv, nicht nur einer: Backup-Prüfung und Symcon-Upload."""
    def kein_oeffnen(*args, **kwargs):
        raise AssertionError("ZipFile wurde geöffnet, obwohl die Grenze schon fiel")

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        path = _zip_with(tmp_path / "zu_viele.zip", 20)
        monkeypatch.setattr(zipfile, "ZipFile", kein_oeffnen)
        monkeypatch.setattr("app.limits.MAX_ZIP_MEMBERS", 5)
        monkeypatch.setattr("app.storage.backup.MAX_ZIP_MEMBERS", 5)

        with pytest.raises(ValueError, match="zu viele"):
            backup.validate_backup(path)
        with pytest.raises(ValueError, match="zu viele"):
            symcon_import.extract_zip(path, tmp_path / "ziel", max_members=5)


def test_an_ordinary_archive_still_passes_both_checks() -> None:
    """Gegenprobe: der Wächter darf nichts ablehnen, was durchgehen soll."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        path = _zip_with(tmp_path / "normal.zip", 20)
        zip_guard.ensure_entry_count_allowed(path, MAX_ZIP_MEMBERS, "egal")
        symcon_import.extract_zip(path, tmp_path / "ziel", max_members=100)
        assert len(list((tmp_path / "ziel").iterdir())) == 20
