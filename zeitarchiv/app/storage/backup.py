"""Backup: exportiert den kompletten Datenbestand (Index + Archiv + Rollups +
Hot Buffer + Import-Reports) als eine herunterladbare ZIP-Datei — zusätzlich zu, nicht statt
der automatischen Snapshots des Home-Assistant-Supervisors (die /data ohnehin
mit erfassen, siehe Konzept "Backups"). Sinnvoll für eine explizite, portable
Kopie: Umzug auf eine neue HA-Instanz, Restore-Test, oder wenn
Supervisor-Snapshots nicht genutzt werden.

Bewusst NICHT im Backup enthalten: symcon_import/ und csv_import/ (temporäre
Einlese-Ordner für laufende Importe — potenziell groß, jederzeit neu
hochladbar, kein Teil des eigentlichen Archivs) sowie server.log (reine
Diagnose, kein Datenbestand).

Kein zusätzliches Komprimieren der Parquet-Dateien im ZIP (ZIP_STORED statt
ZIP_DEFLATED) — sie sind bereits zstd-komprimiert (Konzept Abschnitt 02), ein
zweiter Kompressionslauf kostet nur CPU-Zeit für kaum messbaren Größengewinn.
"""

from __future__ import annotations

import sqlite3
import hashlib
import json
import re
import shutil
import tempfile
import zipfile
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from .coordinator import StorageCoordinator
from .paths import entity_dir, storage_area_dir, validate_entity_id
from ..limits import (
    MAX_ZIP_COMPRESSION_RATIO,
    MAX_ZIP_MEMBERS,
    MAX_ZIP_UNCOMPRESSED_BYTES,
)

# Diese Top-Level-Einträge unter DATA_DIR gehören zum eigentlichen Datenbestand
# — alles andere (symcon_import/, csv_import/, server.log, .dev-token, ...)
# bleibt bewusst außen vor (siehe Moduldocstring).
BACKUP_ENTRIES = ["index.sqlite", "archive", "rollup", "hot", "reports", "symcon_names.json"]
BACKUP_MANIFEST_NAME = "zeitarchiv-manifest.json"
BACKUP_FORMAT_VERSION = 1
RESTORE_REQUEST_NAME = ".zeitarchiv-restore-request.json"
RESTORE_ROLLBACK_RE = re.compile(r"^\.zeitarchiv-restore-rollback-\d{8}T\d{6}Z$")
BACKUP_FILENAME_RE = re.compile(
    r"^zeitarchiv-backup-\d{4}-\d{2}-\d{2}-\d{6}\.zip$"
)
BACKUP_SOURCE_SNAPSHOT_RE = re.compile(r"^\.backup-source-\d+-[0-9a-f]{12}$")


def cleanup_stale_source_snapshots(backups_dir: Path) -> int:
    """Entfernt ausschließlich abgebrochene interne Backup-Snapshots."""
    if not backups_dir.is_dir():
        return 0
    removed = 0
    for path in backups_dir.iterdir():
        if path.is_dir() and BACKUP_SOURCE_SNAPSHOT_RE.fullmatch(path.name):
            shutil.rmtree(path)
            removed += 1
    return removed


def _copy_sqlite_database(source_path: Path, destination_path: Path) -> None:
    """Erzeugt über SQLite selbst einen transaktionskonsistenten Snapshot."""
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    source = sqlite3.connect(f"file:{source_path.resolve()}?mode=ro", uri=True)
    destination = sqlite3.connect(destination_path)
    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()


def _copy_tree(source: Path, destination: Path) -> None:
    if source.is_dir():
        shutil.copytree(source, destination, copy_function=shutil.copy2)


def create_source_snapshot(
    data_dir: Path,
    snapshot_dir: Path,
    entity_ids: list[str],
    coordinator: StorageCoordinator,
) -> None:
    """Kopiert eine stabile Backup-Quelle mit kurzen, gezielten Sperren.

    SQLite und die kleinen globalen Dateien werden unter einer kurzen globalen
    Sperre kopiert. Archiv, Rollups und Hot Buffer werden danach pro Entität
    unter deren eigener Sperre kopiert. So bleiben die Dateien einer Entität
    untereinander konsistent, während Writes anderer Entitäten weiterlaufen.

    Der SQLite-Index kann gegenüber später kopierten Entitäten einen etwas
    älteren Stand enthalten. Er ist ein abgeleiteter Cache und wird nach einem
    Restore beim Start ohnehin gegen die kopierten Dateien reconciliert.
    """
    if snapshot_dir.exists():
        raise ValueError("Backup-Snapshot-Verzeichnis existiert bereits")
    snapshot_dir.mkdir(parents=True)

    ids = sorted(set(entity_ids))
    for entity_id in ids:
        validate_entity_id(entity_id)

    # Diese Dateien sind global, aber klein. Nur für ihre kurze Kopie müssen
    # sämtliche Storage-Operationen pausieren.
    with coordinator.exclusive():
        index_path = data_dir / "index.sqlite"
        if index_path.is_file():
            _copy_sqlite_database(index_path, snapshot_dir / "index.sqlite")
        _copy_tree(data_dir / "reports", snapshot_dir / "reports")
        names_path = data_dir / "symcon_names.json"
        if names_path.is_file():
            shutil.copy2(names_path, snapshot_dir / names_path.name)

    hot_root = storage_area_dir(data_dir, "hot")
    for entity_id in ids:
        with coordinator.entity(entity_id):
            _copy_tree(
                entity_dir(data_dir, "archive", entity_id),
                snapshot_dir / "archive" / entity_id,
            )
            _copy_tree(
                entity_dir(data_dir, "rollup", entity_id),
                snapshot_dir / "rollup" / entity_id,
            )
            if hot_root.is_dir():
                hot_destination = snapshot_dir / "hot"
                for source in sorted(hot_root.glob(f"{entity_id}-*.csv")):
                    if not source.is_file():
                        continue
                    hot_destination.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, hot_destination / source.name)


def resolve_backup_path(backups_dir: Path, filename: str) -> Path | None:
    """Löst ausschließlich von Zeitarchiv erzeugte Backup-Dateinamen auf."""
    if Path(filename).name != filename or not BACKUP_FILENAME_RE.fullmatch(filename):
        return None
    path = backups_dir / filename
    try:
        if path.resolve().parent != backups_dir.resolve():
            return None
    except OSError:
        return None
    return path


def delete_backup(backups_dir: Path, filename: str) -> bool:
    """Löscht genau ein vorhandenes Backup; ungültige Namen bleiben No-op."""
    path = resolve_backup_path(backups_dir, filename)
    if path is None or not path.is_file():
        return False
    path.unlink()
    return True


def delete_all_backups(backups_dir: Path) -> int:
    """Löscht alle vorhandenen Backup-ZIPs (nicht Rollbacks/Ausführungsverlauf,
    siehe delete_restore_rollback()); gibt die Anzahl gelöschter Dateien zurück."""
    if not backups_dir.exists():
        return 0
    files = [p for p in backups_dir.glob("zeitarchiv-backup-*.zip") if p.is_file()]
    for path in files:
        path.unlink()
    return len(files)


def _iter_backup_files(data_dir: Path) -> list[Path]:
    files: list[Path] = []
    for name in BACKUP_ENTRIES:
        path = data_dir / name
        if path.is_file():
            files.append(path)
        elif path.is_dir():
            files.extend(sorted(p for p in path.rglob("*") if p.is_file()))
    return files


def estimate_file_count(data_dir: Path) -> int:
    return len(_iter_backup_files(data_dir))


def estimate_size_bytes(data_dir: Path) -> int:
    return sum(path.stat().st_size for path in _iter_backup_files(data_dir))


def create_backup(
    data_dir: Path,
    dest_path: Path,
    on_progress: Callable[[int, int], None] | None = None,
    *,
    consistent_sqlite: bool = False,
    metadata: dict | None = None,
) -> dict:
    """Schreibt ein ZIP mit dem kompletten Datenbestand nach dest_path.
    on_progress(done, total) wird nach jeder geschriebenen Datei aufgerufen —
    für die Fortschrittsanzeige im Einstellungen-Bereich, analog zum Symcon-
    Import (Konzept Abschnitt 04). Schreibt zuerst in eine .part-Datei und
    benennt erst bei Erfolg um, damit ein abgebrochener Lauf (z. B. Neustart
    mitten im Schreiben) nie ein halbfertiges, als vollständig aussehendes
    Backup hinterlässt."""
    files = _iter_backup_files(data_dir)
    total = len(files)
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = dest_path.with_suffix(dest_path.suffix + ".part")
    sqlite_snapshot = dest_path.with_suffix(dest_path.suffix + ".sqlite-snapshot")
    try:
        if consistent_sqlite and (data_dir / "index.sqlite").exists():
            _copy_sqlite_database(data_dir / "index.sqlite", sqlite_snapshot)

        manifest_files: list[dict] = []
        with zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_STORED) as zf:
            for done, file_path in enumerate(files, start=1):
                if consistent_sqlite and file_path == data_dir / "index.sqlite":
                    source_path = sqlite_snapshot
                    archive_name = "index.sqlite"
                else:
                    source_path = file_path
                    archive_name = file_path.relative_to(data_dir).as_posix()
                digest = hashlib.sha256()
                size = 0
                info = zipfile.ZipInfo.from_file(source_path, arcname=archive_name)
                with source_path.open("rb") as source, zf.open(info, "w") as destination:
                    for chunk in iter(lambda: source.read(1024 * 1024), b""):
                        destination.write(chunk)
                        digest.update(chunk)
                        size += len(chunk)
                manifest_files.append(
                    {
                        "path": archive_name,
                        "size_bytes": size,
                        "sha256": digest.hexdigest(),
                    }
                )
                if on_progress is not None:
                    on_progress(done, total)
            manifest = {
                "format": "zeitarchiv-portable-backup",
                "format_version": BACKUP_FORMAT_VERSION,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "consistent_sqlite": consistent_sqlite,
                "files": manifest_files,
                **(metadata or {}),
            }
            zf.writestr(
                BACKUP_MANIFEST_NAME,
                json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
            )
        validate_backup(tmp_path, check_sqlite=consistent_sqlite)
        tmp_path.replace(dest_path)
        return manifest
    finally:
        sqlite_snapshot.unlink(missing_ok=True)
        tmp_path.unlink(missing_ok=True)


def validate_backup(path: Path, *, check_sqlite: bool = True) -> dict:
    """Prüft ZIP-Struktur, Manifest, Prüfsummen und den SQLite-Schnappschuss.

    Ältere Zeitarchiv-ZIPs ohne Manifest bleiben lesbar und werden als
    Legacy-Backup ausgewiesen. Neu erzeugte Archive müssen das vollständige
    Manifest enthalten.
    """
    try:
        with zipfile.ZipFile(path) as zf:
            members = zf.infolist()
            if len(members) > MAX_ZIP_MEMBERS:
                raise ValueError("Backup enthält zu viele Dateien")
            uncompressed = sum(member.file_size for member in members)
            if uncompressed > MAX_ZIP_UNCOMPRESSED_BYTES:
                raise ValueError("Entpackter Backup-Inhalt ist zu groß")
            if any(
                member.file_size > 0
                and member.file_size / max(1, member.compress_size) > MAX_ZIP_COMPRESSION_RATIO
                for member in members
            ):
                raise ValueError("Backup überschreitet das erlaubte Kompressionsverhältnis")
            names = zf.namelist()
            if any(name.startswith("/") or ".." in Path(name).parts for name in names):
                raise ValueError("Backup enthält einen unsicheren Dateipfad")
            broken = zf.testzip()
            if broken is not None:
                raise ValueError(f"Beschädigte ZIP-Datei: {broken}")

            if BACKUP_MANIFEST_NAME in names:
                try:
                    manifest = json.loads(zf.read(BACKUP_MANIFEST_NAME))
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    raise ValueError("Backup-Manifest ist ungültig") from exc
                if manifest.get("format") != "zeitarchiv-portable-backup":
                    raise ValueError("Unbekanntes Backup-Format")
                entries = manifest.get("files")
                if not isinstance(entries, list):
                    raise ValueError("Backup-Manifest enthält keine Dateiliste")
                for entry in entries:
                    name = entry.get("path") if isinstance(entry, dict) else None
                    if not name or name not in names:
                        raise ValueError(f"Datei aus Manifest fehlt: {name or '?'}")
                    digest = hashlib.sha256()
                    size = 0
                    with zf.open(name) as source:
                        for chunk in iter(lambda: source.read(1024 * 1024), b""):
                            size += len(chunk)
                            digest.update(chunk)
                    if size != entry.get("size_bytes") or digest.hexdigest() != entry.get("sha256"):
                        raise ValueError(f"Prüfsumme stimmt nicht: {name}")
            else:
                manifest = {
                    "format": "zeitarchiv-portable-backup",
                    "format_version": 0,
                    "legacy": True,
                    "files": [{"path": name} for name in names],
                }

            if not check_sqlite:
                return manifest
            if "index.sqlite" not in names:
                raise ValueError("index.sqlite fehlt im Backup")
            with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as tmp:
                sqlite_path = Path(tmp.name)
                with zf.open("index.sqlite") as source:
                    shutil.copyfileobj(source, tmp)
        try:
            connection = sqlite3.connect(f"file:{sqlite_path}?mode=ro", uri=True)
            try:
                result = connection.execute("PRAGMA integrity_check").fetchone()
                if result is None or result[0] != "ok":
                    raise ValueError("SQLite-Integritätsprüfung fehlgeschlagen")
            finally:
                connection.close()
        except sqlite3.DatabaseError as exc:
            raise ValueError("index.sqlite ist keine gültige SQLite-Datenbank") from exc
        finally:
            sqlite_path.unlink(missing_ok=True)
        return manifest
    except zipfile.BadZipFile as exc:
        raise ValueError("Datei ist kein gültiges ZIP-Backup") from exc


def install_validated_backup(upload_path: Path, backups_dir: Path, created_at: datetime) -> Path:
    """Übernimmt einen bereits validierten Upload unter kollisionsfreiem Namen."""
    backups_dir.mkdir(parents=True, exist_ok=True)
    for offset in range(1000):
        timestamp = created_at.timestamp() + offset
        name = f"zeitarchiv-backup-{datetime.fromtimestamp(timestamp, created_at.tzinfo).strftime('%Y-%m-%d-%H%M%S')}.zip"
        destination = backups_dir / name
        if not destination.exists():
            upload_path.replace(destination)
            return destination
    raise ValueError("Kein freier Backup-Dateiname verfügbar")


def prepare_restore(data_dir: Path, backups_dir: Path, filename: str) -> dict:
    """Validiert ein lokales Backup und merkt es für den nächsten Start vor."""
    path = resolve_backup_path(backups_dir, filename)
    if path is None or not path.is_file():
        raise ValueError("Backup nicht gefunden")
    manifest = validate_backup(path)
    request_path = data_dir / RESTORE_REQUEST_NAME
    tmp_path = request_path.with_suffix(".tmp")
    tmp_path.write_text(json.dumps({"filename": filename}), encoding="utf-8")
    tmp_path.replace(request_path)
    return manifest


def apply_pending_restore(data_dir: Path, backups_dir: Path) -> dict | None:
    """Spielt einen bestätigten Restore vor dem Öffnen der Datenbank ein.

    Die bisherigen Daten werden nicht gelöscht, sondern in ein datiertes
    Rollback-Verzeichnis verschoben. Schlägt ein Schritt fehl, werden bereits
    verschobene Einträge sofort zurückgesetzt.
    """
    request_path = data_dir / RESTORE_REQUEST_NAME
    if not request_path.is_file():
        return None
    staging = data_dir / ".zeitarchiv-restore-staging"
    rollback = data_dir / f".zeitarchiv-restore-rollback-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    moved_old: list[tuple[Path, Path]] = []
    installed: list[Path] = []
    try:
        request = json.loads(request_path.read_text(encoding="utf-8"))
        filename = request.get("filename")
        path = resolve_backup_path(backups_dir, filename) if isinstance(filename, str) else None
        if path is None or not path.is_file():
            raise ValueError("Vorgemerktes Backup wurde nicht gefunden")
        manifest = validate_backup(path)
        shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True)
        with zipfile.ZipFile(path) as zf:
            for info in zf.infolist():
                parts = Path(info.filename).parts
                if not parts or parts[0] not in BACKUP_ENTRIES or info.is_dir():
                    continue
                target = staging.joinpath(*parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as source, target.open("wb") as destination:
                    shutil.copyfileobj(source, destination)

        restored_index = staging / "index.sqlite"
        connection = sqlite3.connect(f"file:{restored_index}?mode=ro", uri=True)
        try:
            if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("Wiederhergestellter SQLite-Index ist beschädigt")
        finally:
            connection.close()

        rollback.mkdir(parents=True)
        for name in BACKUP_ENTRIES:
            current = data_dir / name
            if current.exists():
                old_target = rollback / name
                old_target.parent.mkdir(parents=True, exist_ok=True)
                current.replace(old_target)
                moved_old.append((old_target, current))
        for name in BACKUP_ENTRIES:
            restored = staging / name
            if restored.exists():
                target = data_dir / name
                restored.replace(target)
                installed.append(target)
        request_path.unlink(missing_ok=True)
        shutil.rmtree(staging, ignore_errors=True)
        return {
            "success": True,
            "filename": filename,
            "format_version": manifest.get("format_version", 0),
            "rollback": rollback.name,
        }
    except Exception as exc:
        for target in reversed(installed):
            if target.is_dir():
                shutil.rmtree(target, ignore_errors=True)
            else:
                target.unlink(missing_ok=True)
        for old_target, original in reversed(moved_old):
            if old_target.exists():
                old_target.replace(original)
        request_path.unlink(missing_ok=True)
        shutil.rmtree(staging, ignore_errors=True)
        return {"success": False, "error": str(exc)[:2000] or exc.__class__.__name__}


def list_restore_rollbacks(data_dir: Path) -> list[str]:
    return sorted(
        (path.name for path in data_dir.glob(".zeitarchiv-restore-rollback-*")
         if path.is_dir() and RESTORE_ROLLBACK_RE.fullmatch(path.name)),
        reverse=True,
    )


def delete_restore_rollback(data_dir: Path, name: str) -> bool:
    if not RESTORE_ROLLBACK_RE.fullmatch(name) or Path(name).name != name:
        return False
    path = data_dir / name
    if not path.is_dir():
        return False
    shutil.rmtree(path)
    return True


def list_backups(backups_dir: Path) -> list[dict]:
    """Neueste zuerst — reines Verzeichnis-Listing statt eines eigenen
    Index-Eintrags, dieselbe Herangehensweise wie beim Archiv/Rollup (die
    Dateien selbst sind die Quelle der Wahrheit)."""
    if not backups_dir.exists():
        return []
    files = sorted(
        (p for p in backups_dir.glob("zeitarchiv-backup-*.zip") if p.is_file()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return [{"filename": p.name, "size_bytes": p.stat().st_size, "created_at": p.stat().st_mtime} for p in files]


def prune_backups(backups_dir: Path, keep_count: int | None, keep_days: int | None, now: float) -> int:
    """Entfernt ältere Backups nach den beiden unabhängig wirksamen Regeln
    (None = die jeweilige Regel ist aus): die neuesten keep_count Backups
    bleiben unabhängig vom Alter erhalten, zusätzlich fällt jedes Backup weg,
    das älter als keep_days ist — ein Backup verschwindet also, sobald
    IRGENDEINE der beiden aktiven Regeln zutrifft. Gibt die Anzahl der
    gelöschten Dateien zurück."""
    if keep_count is None and keep_days is None:
        return 0
    files = sorted(
        (p for p in backups_dir.glob("zeitarchiv-backup-*.zip") if p.is_file()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    removed = 0
    for i, path in enumerate(files):
        exceeds_count = keep_count is not None and i >= keep_count
        exceeds_age = keep_days is not None and (now - path.stat().st_mtime) / 86400 > keep_days
        if exceeds_count or exceeds_age:
            path.unlink()
            removed += 1
    return removed
