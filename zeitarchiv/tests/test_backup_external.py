"""Externes Backup-Ziel (app/storage/backup_external.py, background.py).

Kein echter Netzwerkspeicher: Mounts über eine nachgebaute mountinfo, das
Ziel ist ein Verzeichnis im tmp_path. Die Zusammenarbeit mit dem Backup-Worker
läuft über den echten BackgroundService."""

from __future__ import annotations

import fnmatch
import os
import threading
import time
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

try:
    from app import notices
    from app.background import BackgroundDependencies, BackgroundService
    from app.storage import backup, backup_external
    from app.storage.coordinator import StorageCoordinator
    from app.storage.index import Index

    _AVAILABLE = True
except ImportError:
    _AVAILABLE = False

pytestmark = pytest.mark.skipif(not _AVAILABLE, reason="pyarrow nicht installiert")

TZ = ZoneInfo("Europe/Berlin")


def _mountinfo(tmp: Path, mounted: list[Path]) -> Path:
    lines = [
        f"{100 + i} 1 0:{50 + i} / {p} rw - nfs 192.168.79.5:/Zeitarchiv-Backup rw"
        for i, p in enumerate(mounted)
    ]
    path = tmp / "mountinfo"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_parse_mountinfo_reads_type_source_and_escaped_paths() -> None:
    text = "36 1 0:30 / /share/Mein\\040NAS rw,noatime shared:1 - cifs //nas/backup rw\n"
    assert backup_external.parse_mountinfo(text) == {"/share/Mein NAS": ("cifs", "//nas/backup")}


def test_list_mounts_separates_real_mounts_from_plain_folders(tmp_path) -> None:
    share = tmp_path / "share"
    (share / "Zeitarchiv").mkdir(parents=True)
    (share / "lokal").mkdir()
    (share / ".versteckt").mkdir()
    entries = backup_external.list_mounts(
        (share,), _mountinfo(tmp_path, [share / "Zeitarchiv"])
    )
    assert [(e.name, e.mounted) for e in entries] == [("Zeitarchiv", True), ("lokal", False)]
    assert entries[0].fstype == "nfs" and entries[0].source == "192.168.79.5:/Zeitarchiv-Backup"
    assert entries[0].free_bytes is not None


def test_a_plain_folder_is_refused_as_target(tmp_path) -> None:
    share = tmp_path / "share"
    (share / "lokal").mkdir(parents=True)
    with pytest.raises(backup_external.ExternalTargetError):
        backup_external.resolve_target(str(share / "lokal"), "", (share,), _mountinfo(tmp_path, []))
    with pytest.raises(backup_external.ExternalTargetError):
        backup_external.resolve_target(str(tmp_path / "anderswo"), "", (share,), _mountinfo(tmp_path, []))


@pytest.mark.parametrize("value", ["../etc", "a/b", ".versteckt", "x" * 80, "ä"])
def test_subfolder_must_be_a_plain_name(value) -> None:
    with pytest.raises(ValueError):
        backup_external.validate_subfolder(value)


def test_subfolder_accepts_empty_and_simple_names() -> None:
    assert backup_external.validate_subfolder("") == ""
    assert backup_external.validate_subfolder(" zeitarchiv-1 ") == "zeitarchiv-1"


def test_transfer_copies_atomically_and_keeps_the_mtime(tmp_path) -> None:
    source = tmp_path / "zeitarchiv-backup-2026-10-11-030000.zip"
    source.write_bytes(os.urandom(300_000))
    os.utime(source, (1_700_000_000, 1_700_000_000))
    target = tmp_path / "ziel"
    seen: list[int] = []
    size = backup_external.transfer(source, target, on_progress=lambda done, total: seen.append(done))
    final = target / source.name
    assert size == source.stat().st_size == final.stat().st_size
    assert final.read_bytes() == source.read_bytes()
    assert int(final.stat().st_mtime) == 1_700_000_000
    assert not list(target.glob("*.part")) and not list(target.glob(".*part"))
    assert seen and seen[-1] == size


def test_a_late_old_transfer_does_not_corrupt_the_retry(tmp_path, monkeypatch) -> None:
    """Nach einem Watchdog-Timeout läuft der alte Versuch als Daemon weiter.
    Mit einem gemeinsamen .part-Namen schrieb er in die Datei, die der Retry
    gerade unter den Endnamen gelegt hatte — der Endname enthielt danach mehr
    Bytes als das Backup. Nachgestellt: mitten im ersten Versuch läuft ein
    zweiter vollständig durch, dann macht der erste weiter."""
    monkeypatch.setattr(backup_external, "COPY_CHUNK_BYTES", 1000)
    source = tmp_path / "zeitarchiv-backup-2026-10-11-030000.zip"
    source.write_bytes(os.urandom(5000))
    target = tmp_path / "ziel"
    retried = []

    def beat() -> None:
        if not retried:
            retried.append(True)
            backup_external.transfer(source, target)

    backup_external.transfer(source, target, beat)
    assert retried
    assert (target / source.name).read_bytes() == source.read_bytes()
    assert not list(target.glob(backup_external.PART_GLOB))


def test_every_attempt_gets_its_own_part_name() -> None:
    first = backup_external.webdav.unique_part_name("zeitarchiv-backup-x.zip")
    second = backup_external.webdav.unique_part_name("zeitarchiv-backup-x.zip")
    assert first != second
    assert fnmatch.fnmatch(first, backup_external.PART_GLOB)


def test_transfer_leaves_no_part_file_after_a_write_error(tmp_path, monkeypatch) -> None:
    source = tmp_path / "zeitarchiv-backup-x.zip"
    source.write_bytes(b"x" * 1000)
    target = tmp_path / "ziel"
    monkeypatch.setattr(os, "replace", lambda *a: (_ for _ in ()).throw(PermissionError("nein")))
    with pytest.raises(backup_external.ExternalTargetError, match="root_squash"):
        backup_external.transfer(source, target)
    assert list(target.iterdir()) == []


def test_prune_external_touches_only_backup_files(tmp_path) -> None:
    for i in range(4):
        path = tmp_path / f"zeitarchiv-backup-{i}.zip"
        path.write_bytes(b"x")
        os.utime(path, (1000 + i, 1000 + i))
    fremd = tmp_path / "urlaub.zip"
    fremd.write_bytes(b"x")
    os.utime(fremd, (1, 1))
    removed = backup_external.prune_external(tmp_path, 2, None, time.time())
    assert removed == 2
    assert fremd.exists()
    assert sorted(p.name for p in tmp_path.glob("zeitarchiv-backup-*.zip")) == [
        "zeitarchiv-backup-2.zip", "zeitarchiv-backup-3.zip",
    ]


def test_local_prune_keeps_protected_backups(tmp_path) -> None:
    for i in range(4):
        path = tmp_path / f"zeitarchiv-backup-{i}.zip"
        path.write_bytes(b"x")
        os.utime(path, (1000 + i, 1000 + i))
    removed = backup.prune_backups(tmp_path, 1, None, time.time(), {"zeitarchiv-backup-1.zip"})
    assert removed == 2
    assert sorted(p.name for p in tmp_path.glob("zeitarchiv-backup-*.zip")) == [
        "zeitarchiv-backup-1.zip", "zeitarchiv-backup-3.zip",
    ]


def test_watchdog_gives_up_on_a_hanging_target_instead_of_hanging_itself() -> None:
    release = threading.Event()

    def hangs(beat):
        release.wait(5)

    started = time.monotonic()
    with pytest.raises(backup_external.ExternalTargetError, match="antwortet nicht"):
        backup_external.run_with_watchdog(hangs, stall_seconds=0.2, poll_seconds=0.05)
    assert time.monotonic() - started < 2
    release.set()


def test_watchdog_passes_results_and_errors_through() -> None:
    assert backup_external.run_with_watchdog(lambda beat: 42, stall_seconds=1, poll_seconds=0.01) == 42
    with pytest.raises(KeyError):
        backup_external.run_with_watchdog(lambda beat: {}["x"], stall_seconds=1, poll_seconds=0.01)


@pytest.fixture()
def dienst(tmp_path):
    index = Index(tmp_path / "index.sqlite")
    service = BackgroundService(BackgroundDependencies(
        data_dir=tmp_path, tz=TZ, index=index, coordinator=StorageCoordinator(),
        base_dir=tmp_path, demo_mode_active=False,
        backups_dir=tmp_path / "backups", symcon_import_dir=tmp_path / "symcon",
        csv_import_dir=tmp_path / "csv", backup_default_time="03:00", backup_default_weekday=6,
        retention_default_time="04:00", retention_default_weekday=6,
        count_stale_entities=lambda: 0,
    ))
    try:
        yield service
    finally:
        index.close()


def _configure(dienst, tmp_path, monkeypatch, *, fail: bool = False) -> Path:
    target = tmp_path / "share" / "Zeitarchiv"
    target.mkdir(parents=True, exist_ok=True)
    index = dienst.index
    index.set_setting("backup_external_type", "dir")
    index.set_setting("backup_external_mount", str(target))

    def resolve(mount, subfolder, *args, **kwargs):
        if fail:
            raise backup_external.ExternalTargetError("Ziel nicht erreichbar")
        return target

    monkeypatch.setattr(backup_external, "resolve_target", resolve)
    return target


def _run_backup(dienst) -> None:
    assert dienst.run_backup()
    deadline = time.time() + 20
    while dienst.backup_progress.running and time.time() < deadline:
        time.sleep(0.05)
    assert not dienst.backup_progress.running


def test_a_backup_is_copied_to_the_external_target(dienst, tmp_path, monkeypatch) -> None:
    target = _configure(dienst, tmp_path, monkeypatch)
    _run_backup(dienst)
    (job,) = dienst.index.list_backup_jobs(1)
    assert job["status"] == "success" and job["external_status"] == "ok"
    assert (target / job["filename"]).stat().st_size == job["size_bytes"]
    assert notices.latest_backup_info(dienst.index)["external"]["status"] == "ok"


def test_a_failed_transfer_keeps_the_local_backup_valid_and_raises_a_notice(dienst, tmp_path, monkeypatch) -> None:
    _configure(dienst, tmp_path, monkeypatch, fail=True)
    _run_backup(dienst)
    (job,) = dienst.index.list_backup_jobs(1)
    assert job["status"] == "success" and job["external_status"] == "failed"
    assert "nicht erreichbar" in job["external_error"]
    assert (dienst.backups_dir / job["filename"]).is_file()
    assert notices.latest_backup_info(dienst.index)["external"]["status"] == "failed"
    assert dienst.index.get_setting("backup_external_type") == "dir"


def test_unsent_backups_survive_local_retention_and_are_sent_later(dienst, tmp_path, monkeypatch) -> None:
    dienst.index.set_setting("backup_keep_count", "1")
    dienst.index.set_setting("backup_external_keep", "never")  # sonst räumt „wie lokal“ am Ziel auf Stufe 1 ab
    target = _configure(dienst, tmp_path, monkeypatch, fail=True)
    _run_backup(dienst)
    time.sleep(1.1)  # Dateinamen tragen Sekunden
    _run_backup(dienst)
    assert len(list(dienst.backups_dir.glob("zeitarchiv-backup-*.zip"))) == 2  # nichts übertragen, nichts gelöscht

    _configure(dienst, tmp_path, monkeypatch)  # Ziel wieder da
    time.sleep(1.1)
    _run_backup(dienst)
    assert len(list(target.glob("zeitarchiv-backup-*.zip"))) == 3  # die zwei alten wurden nachgeholt
    assert len(list(dienst.backups_dir.glob("zeitarchiv-backup-*.zip"))) == 1  # erst jetzt greift Stufe „1“


def test_without_external_target_retention_behaves_as_before(dienst) -> None:
    dienst.index.set_setting("backup_keep_count", "1")
    _run_backup(dienst)
    time.sleep(1.1)
    _run_backup(dienst)
    assert len(list(dienst.backups_dir.glob("zeitarchiv-backup-*.zip"))) == 1
    (job, _older) = dienst.index.list_backup_jobs(2)
    assert job["external_status"] is None
    assert "external" not in notices.latest_backup_info(dienst.index)


def test_an_interrupted_transfer_is_marked_failed_on_restart(dienst) -> None:
    job_id = dienst.index.create_backup_job("manual")
    dienst.index.update_backup_job(job_id, status="success", filename="zeitarchiv-backup-x.zip", external_status="running")
    dienst.index.recover_interrupted_backup_jobs()
    row = dienst.index.get_backup_job_by_filename("zeitarchiv-backup-x.zip")
    assert row["external_status"] == "failed"
