"""Wächter: kein neues Umbenennen von Dateien ohne fsync (Issue #13).

Ein ``Path.replace`` / ``os.replace`` direkt nach dem Schreiben lässt nach hartem Kill
(Stromausfall, OOM-Kill) das Ziel als leere Datei zurück: Der Rename steht schon auf der
Platte, die Daten nicht. So entstand die 0-Byte-Rollup-Datei. Dateien, die Daten tragen,
schreiben deshalb über ``storage.atomic`` (``write_file_atomically`` / ``replace_durably``).

Der Test zählt die direkten Umbenennungen je Datei und vergleicht mit dem geprüften Stand.
Eine neue oder zusätzliche Stelle lässt ihn scheitern. Dann entweder ``replace_durably``
nehmen — oder, wenn es um ein Verzeichnis oder eine Wegwerf-/Zustandsdatei geht, die
Stelle hier bewusst eintragen.
"""

from __future__ import annotations

import ast

from _paths import APP

#: Datei (relativ zu app/) -> Zahl der direkten Umbenennungen, die bewusst ohne fsync bleiben.
REVIEWED = {
    # JSON-Zustands- und Hilfsdateien; Leser behandeln eine leere Datei nicht geprüft als Fehler.
    "import_routes.py": 3,  # Symcon-Meta, Symcon-Namen, Staging-Verzeichnis
    "route_support.py": 1,  # Upload-Teildatei
    "selection_catalog.py": 1,
    "selection_jobs.py": 1,
    "storage/import_last.py": 1,
    "storage/import_reports.py": 1,
    # Backup-Anforderungen (winzig, nur beim Start gelesen), Upload, Verzeichnis-Rollback beim Restore.
    "storage/backup.py": 6,
    "storage/restore_selective.py": 7,
    # Verzeichniswechsel (Segment-Migration, Neuaufbau, Staging); Dateien darin schreibt atomic.
    "storage/rollup.py": 6,
    "storage/symcon_import.py": 1,
    # Kopiert mit eigenem fsync vor dem Umbenennen.
    "storage/backup_external.py": 1,
}


def _renames(path) -> int:
    count = 0
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        name, owner = node.func.attr, node.func.value
        if name == "replace" and len(node.args) == 1 and not node.keywords:
            # datetime.replace(...) nimmt Schlüsselwörter, str.replace zwei Argumente;
            # dataclasses.replace(obj) ist kein Dateizugriff.
            if not (isinstance(owner, ast.Name) and owner.id == "dataclasses"):
                count += 1
        elif name == "rename" and len(node.args) == 1:
            count += 1
        elif name in ("replace", "rename") and isinstance(owner, ast.Name) and owner.id == "os":
            count += 1
    return count


def test_no_new_rename_without_fsync() -> None:
    found = {}
    for path in sorted(APP.rglob("*.py")):
        relative = path.relative_to(APP).as_posix()
        if relative == "storage/atomic.py":
            continue
        count = _renames(path)
        if count:
            found[relative] = count
    assert found == REVIEWED, (
        "Direkte Umbenennung ohne fsync gefunden oder entfernt. Dateien mit Daten über "
        "storage.atomic (replace_durably / write_file_atomically) schreiben; sonst die Stelle "
        f"in REVIEWED eintragen.\ngefunden: {found}"
    )
