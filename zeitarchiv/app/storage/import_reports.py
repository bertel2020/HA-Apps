"""Dauerhafte, maschinenlesbare Berichte ausgefuehrter Datenimporte."""

from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path


FORMAT = "zeitarchiv-import-report"
FORMAT_VERSION = 2
_REPORT_ID_RE = re.compile(r"^\d{8}T\d{6}Z-(?:symcon|csv|ha)-[0-9a-f]{12}$")


def create(
    data_dir: Path,
    *,
    source_type: str,
    started_at: datetime,
    source: dict,
    configuration: dict,
    results: list[dict],
    errors: list[str],
    reconciliation: dict | None = None,
) -> dict:
    """Schreibt einen abgeschlossenen Report atomar unter reports/import/Jahr."""
    if source_type not in {"symcon", "csv", "ha"}:
        raise ValueError("Unbekannter Importtyp")
    finished_at = datetime.now(timezone.utc)
    started_utc = started_at.astimezone(timezone.utc)
    report_id = (
        f"{finished_at.strftime('%Y%m%dT%H%M%SZ')}-{source_type}-"
        f"{uuid.uuid4().hex[:12]}"
    )
    rows_written = sum(
        int(row.get("rows_imported", 0))
        + int(row.get("rows_merged", 0))
        + int(row.get("rows_updated", 0))
        + int(row.get("rows_recovered", 0))
        for row in results
    )
    incomplete = bool(errors) or any(int(row.get("skipped_rows", 0)) for row in results)
    if errors and not results:
        status = "failed"
    elif incomplete:
        status = "partial"
    elif rows_written == 0:
        status = "no_changes"
    else:
        status = "success"
    report = {
        "format": FORMAT,
        "format_version": FORMAT_VERSION,
        "id": report_id,
        "source_type": source_type,
        "status": status,
        "started_at": started_utc.isoformat(),
        "finished_at": finished_at.isoformat(),
        "duration_seconds": round(max(0.0, (finished_at - started_utc).total_seconds()), 3),
        "source": source,
        "configuration": configuration,
        "summary": {
            "targets": len({row.get("entity_id") for row in results if row.get("entity_id")}),
            "rows_read": sum(int(row.get("source_rows", 0)) for row in results),
            "rows_imported": sum(int(row.get("rows_imported", 0)) for row in results),
            "rows_merged": sum(int(row.get("rows_merged", 0)) for row in results),
            "rows_updated": sum(int(row.get("rows_updated", 0)) for row in results),
            "rows_recovered": sum(int(row.get("rows_recovered", 0)) for row in results),
            "rows_duplicate": sum(int(row.get("duplicate_rows", 0)) for row in results),
            "rows_invalid": sum(int(row.get("skipped_rows", 0)) for row in results),
            "months_imported": sum(len(row.get("imported_months", [])) for row in results),
            "months_merged": sum(len(row.get("merged_months", [])) for row in results),
            "months_skipped": sum(len(row.get("skipped_months", [])) for row in results),
            "errors": len(errors),
        },
        "results": results,
        "errors": errors,
        "reconciliation": reconciliation,
    }
    directory = data_dir / "reports" / "import" / finished_at.strftime("%Y")
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / f"{report_id}.json"
    temporary = destination.with_suffix(".json.part")
    try:
        temporary.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return report


def _path(data_dir: Path, report_id: str) -> Path | None:
    if not _REPORT_ID_RE.fullmatch(report_id):
        return None
    year = report_id[:4]
    path = data_dir / "reports" / "import" / year / f"{report_id}.json"
    root = (data_dir / "reports" / "import").resolve()
    try:
        return path if path.resolve().is_relative_to(root) else None
    except OSError:
        return None


def load(data_dir: Path, report_id: str) -> dict | None:
    path = _path(data_dir, report_id)
    if path is None or not path.is_file():
        return None
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if report.get("format") != FORMAT or report.get("id") != report_id:
        return None
    return report


def list_all(data_dir: Path) -> list[dict]:
    root = data_dir / "reports" / "import"
    if not root.exists():
        return []
    reports = []
    for path in root.glob("*/*.json"):
        report = load(data_dir, path.stem)
        if report is not None:
            reports.append(report)
    return sorted(reports, key=lambda item: item.get("finished_at", ""), reverse=True)


def delete(data_dir: Path, report_id: str) -> bool:
    path = _path(data_dir, report_id)
    if path is None or not path.is_file():
        return False
    path.unlink()
    try:
        path.parent.rmdir()
    except OSError:
        pass
    return True


def delete_all(data_dir: Path) -> int:
    """Löscht alle gültigen Import-Reports und liefert deren Anzahl zurück."""
    reports = list_all(data_dir)
    deleted = 0
    for report in reports:
        if delete(data_dir, report["id"]):
            deleted += 1
    try:
        (data_dir / "reports" / "import").rmdir()
    except OSError:
        pass
    return deleted


def download_path(data_dir: Path, report_id: str) -> Path | None:
    path = _path(data_dir, report_id)
    return path if path is not None and path.is_file() else None
