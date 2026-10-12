"""Zurücksetzen des gesamten Datenbestands (Einstellungen → Zurücksetzen).

Zwei Stufen: nur die Messwerte, oder alles wie bei einer frischen
Installation. Backups bleiben in beiden Fällen liegen — sie sind der einzige
Rückweg, und ein Reset, der sie mitnimmt, wäre keiner mehr, den man sich
traut.
"""

from __future__ import annotations

from pathlib import Path

from .entity_removal import _remove_path
from .index import Index
from .paths import storage_area_dir

SCOPE_VALUES = "values"
SCOPE_FULL = "full"
SCOPES = (SCOPE_VALUES, SCOPE_FULL)

#: Einstellungsschlüssel, die ein Komplett-Reset behält. api_token: ohne ihn
#: verliert die Home-Assistant-Integration die Verbindung und müsste neu
#: eingerichtet werden. backup_*: Zeitplan und externes Ziel gehören zur
#: Datensicherung, nicht zu den Daten. storage_clean_shutdown: Laufzeitmarke
#: des Speichers, kein Nutzerwert.
KEEP_SETTING_PREFIXES = ("api_token", "backup_", "storage_clean_shutdown")

#: Zusätzliche Verzeichnisse/Dateien unter data_dir, die nur ein
#: Komplett-Reset leert (Importreste, Berichte, Symcon-Namenszuordnung).
_FULL_ONLY_DIRS = ("reports", "symcon_import")
_FULL_ONLY_FILES = ("symcon_names.json",)


def _empty_dir(path: Path) -> None:
    if not path.exists():
        return
    for child in path.iterdir():
        _remove_path(child)


def preview(index: Index) -> dict:
    """Zahlen für den Bestätigungsdialog — aus dem Index, ohne Dateizugriff."""
    entities = index.list_entities()
    return {
        "entities": len(entities),
        "rows": sum(int(e["row_count"] or 0) for e in entities),
        "size_bytes": sum(int(e["size_bytes"] or 0) for e in entities),
    }


def reset(data_dir: Path, index: Index, scope: str) -> dict:
    """Führt den Reset aus. Der Aufrufer hält die globale Wartungssperre
    (StorageCoordinator.exclusive()); hier wird nicht gesperrt."""
    if scope not in SCOPES:
        raise ValueError(f"Unbekannter Reset-Umfang: {scope!r}")
    before = preview(index)
    # Dateien zuerst, Index danach: bricht der Lauf mittendrin ab, zeigt der
    # Index noch Werte, die es nicht mehr gibt — das repariert die Index-
    # Prüfung (Speicherplatz). Umgekehrt gäbe es Dateien, die der Index nicht
    # kennt und die nie jemand aufräumt.
    for area in ("hot", "archive", "rollup"):
        _empty_dir(storage_area_dir(data_dir, area))
    if scope == SCOPE_FULL:
        for name in _FULL_ONLY_DIRS:
            _empty_dir(data_dir / name)
        for name in _FULL_ONLY_FILES:
            target = data_dir / name
            if target.exists() or target.is_symlink():
                _remove_path(target)
        index.reset_everything(KEEP_SETTING_PREFIXES)
    else:
        index.clear_all_values()
    return before
