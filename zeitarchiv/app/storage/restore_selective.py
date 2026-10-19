"""Selektiver Restore: einzelne Entitäten oder einzelne Einstellungen aus einem Backup.

Der Voll-Restore (backup.apply_pending_restore) tauscht beim Neustart alles aus.
Hier läuft der Austausch dagegen zur Laufzeit und nur für das, was gewählt wurde:

- Entitäten: Archiv-, Rollup- und Hot-Buffer-Dateien der gewählten Entitäten werden
  unter ``coordinator.exclusive()`` gegen die Dateien aus dem Backup getauscht. Die
  Entitäts-Einstellungen (Name, Aufbewahrung, Filter …) bleiben, es sei denn, die Entität
  existiert lokal nicht mehr: dann kommt ihre Zeile aus dem Backup.
- Einstellungen: Dashboards, Charts, Tabellen, Feldgruppen der Entitäten und einzelne
  Werte aus ``settings``. Standard ist „als Kopie hinzufügen“: das legt neue Zeilen mit
  neuen IDs an und schreibt die Verweise (Kacheln, Tabellenspalten) um. Nur auf Wunsch
  wird ein gleichnamiges Objekt an Ort und Stelle überschrieben.

Beides ist rückgängig zu machen. Dafür legt jeder Lauf ein Verzeichnis
``.zeitarchiv-selective-rollback-<UTC>`` mit ``selective-info.json`` an. Der Name hat
bewusst ein anderes Präfix als die Voll-Restore-Rollbacks: apply_pending_restore() würde
ein solches Teil-Verzeichnis sonst als vollständigen Stand einspielen und alles andere
löschen.

Die Quelle (BackupSource) liest die index.sqlite des ZIPs schreibgeschützt aus einer
temporären Kopie und prüft jede entpackte Datei gegen die SHA-256-Summe des Manifests.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import shutil
import sqlite3
import tempfile
import time
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from ..i18n import N_, tr
from . import reconcile
from .backup import BACKUP_MANIFEST_NAME
from .coordinator import StorageCoordinator
from .index import Index, _normalized_name
from .paths import entity_dir, validate_entity_id

ROLLBACK_PREFIX = ".zeitarchiv-selective-rollback-"
ROLLBACK_RE = re.compile(r"^\.zeitarchiv-selective-rollback-\d{8}T\d{6}Z$")
INFO_NAME = "selective-info.json"
# Teil-Rollbacks sind klein (Dateien weniger Entitäten, Einstellungszeilen); drei
# reichen, um einen Fehlgriff zu korrigieren, ohne dass sich Altlasten ansammeln.
ROLLBACK_KEEP_COUNT = 3

_HOT_RE = re.compile(r"^hot/(?P<id>[a-z][a-z0-9_]*\.[a-z0-9_]+)-\d{4}-\d{2}\.csv$")
_HOT_NAME_RE = r"^{id}-\d{{4}}-\d{{2}}\.csv$"

# Feldgruppen der Entitäts-Einstellungen. Spalten, die Zustand statt Konfiguration sind
# (counter_auto_since, counter_auto_halted_at, last_value, row_count …), fehlen bewusst.
ENTITY_GROUPS: dict[str, tuple[str, ...]] = {
    "name": ("custom_name",),
    "ret": ("retention", "resolution", "compact_target", "hourly_rollup"),
    "filter": ("value_filter", "ignore_values_enabled", "ignore_values", "gap_threshold", "outlier_threshold"),
    "counter": ("counter_decreases", "counter_auto_mark"),
    "disp": ("decimals", "display_mode", "chart_options", "is_favorite"),
}
ENTITY_GROUP_LABELS = {
    "name": N_("Name"),
    "ret": N_("Aufbewahrung"),
    "filter": N_("Filter"),
    "counter": N_("Zähler"),
    "disp": N_("Anzeige"),
}
_COLUMN_LABELS = {
    "custom_name": N_("Eigener Name"),
    "retention": N_("Aufbewahrung"),
    "resolution": N_("Auflösung"),
    "compact_target": N_("Verdichtung"),
    "hourly_rollup": N_("Stunden-Rollup"),
    "value_filter": N_("Wertfilter"),
    "ignore_values_enabled": N_("Werte ignorieren"),
    "ignore_values": N_("Zu ignorierende Werte"),
    "gap_threshold": N_("Lückengrenze"),
    "outlier_threshold": N_("Ausreißergrenze"),
    "counter_decreases": N_("Zählerrückgänge"),
    "counter_auto_mark": N_("Automatisch markieren"),
    "decimals": N_("Nachkommastellen"),
    "display_mode": N_("Anzeigeart"),
    "chart_options": N_("Chart-Optionen"),
    "is_favorite": N_("Favorit"),
}
# Reihenfolge der Aufbewahrungsstufen in Tagen; „unlimited“ ist am längsten.
_RETENTION_DAYS = {"30d": 30, "90d": 90, "365d": 365, "2y": 730, "5y": 1825}

# Allgemeine Einstellungen, die der Restore anbietet. Positivliste: Zugangsdaten
# (api_token, backup_external_webdav_*) und Betriebszustand (…_last_*, …_next_run,
# storage_clean_shutdown, demo_append_*) gehören zur laufenden Installation und werden
# nie übernommen.
GENERAL_SETTINGS: list[tuple[str, str]] = [
    ("language", N_("Sprache")),
    ("color_scheme", N_("Farbschema")),
    ("color_mode", N_("Darstellung")),
    ("font_scale", N_("Schriftgröße")),
    ("startseite", N_("Startseite")),
    ("dashboard_animation", N_("Dashboard-Animation")),
    ("entities_columns", N_("Spalten der Entitätenliste")),
    ("entity_chart_defaults", N_("Chart-Voreinstellungen je Entität")),
    ("default_resolution", N_("Standard-Auflösung")),
    ("default_retention", N_("Standard-Aufbewahrung")),
    ("default_decimals", N_("Standard-Nachkommastellen")),
    ("default_value_filter", N_("Standard-Wertfilter")),
    ("default_gap_threshold", N_("Standard-Lückengrenze")),
    ("default_outlier_threshold", N_("Standard-Ausreißergrenze")),
    ("default_compact_target", N_("Standard-Verdichtung")),
    ("backup_schedule", N_("Backup-Zeitplan")),
    ("backup_schedule_time", N_("Backup-Uhrzeit")),
    ("backup_schedule_weekday", N_("Backup-Wochentag")),
    ("backup_keep_count", N_("Backups behalten (Anzahl)")),
    ("backup_keep_days", N_("Backups behalten (Alter)")),
    ("retention_enforcement", N_("Automatische Aufbewahrung")),
    ("retention_enforcement_time", N_("Aufbewahrung: Uhrzeit")),
    ("retention_enforcement_weekday", N_("Aufbewahrung: Wochentag")),
    ("purge_auto_enabled", N_("Automatisch bereinigen")),
    ("purge_min_age_days", N_("Bereinigen ab Alter (Tage)")),
    ("compact_auto_enabled", N_("Automatisch verdichten")),
    ("compact_min_age_months", N_("Verdichten ab Alter (Monate)")),
]
_ENERGIE_KEYS = ("energiedashboard_enabled", "energiedashboard_config")
SYMCON_FILE = "symcon_names.json"
_SETTING_NEXT_RUN = {
    "backup_schedule": "backup_schedule_next_run",
    "backup_schedule_time": "backup_schedule_next_run",
    "backup_schedule_weekday": "backup_schedule_next_run",
    "retention_enforcement": "retention_enforcement_next_run",
    "retention_enforcement_time": "retention_enforcement_next_run",
    "retention_enforcement_weekday": "retention_enforcement_next_run",
}

_CHART_FIELD_LABELS = {
    "entity_ids": N_("Entitäten"),
    "hidden_entity_ids": N_("Entitäten"),
    "entity_names": N_("Entitäten"),
    "range_key": N_("Zeitraum"),
    "continuous": N_("Zeitraum"),
    "period_anchor": N_("Zeitraum"),
    "compare_settings": N_("Vergleich"),
    "chart_type": N_("Typ"),
    "resolution_preset": N_("Auflösung"),
}


# --------------------------------------------------------------------------- Quelle


class BackupSource:
    """Geöffnetes Backup-ZIP mit schreibgeschützter Kopie seiner index.sqlite."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.names: list[str] = []
        self.manifest: dict = {}
        self.db: sqlite3.Connection
        self._zip: zipfile.ZipFile | None = None
        self._sums: dict[str, dict] = {}
        self._db_path: Path | None = None
        self._db_open = False

    def __enter__(self) -> "BackupSource":
        try:
            self._zip = zipfile.ZipFile(self.path)
            self.names = self._zip.namelist()
            if any(n.startswith("/") or ".." in Path(n).parts for n in self.names):
                raise ValueError(tr("Backup enthält einen unsicheren Dateipfad"))
            if BACKUP_MANIFEST_NAME in self.names:
                try:
                    self.manifest = json.loads(self._zip.read(BACKUP_MANIFEST_NAME))
                except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                    raise ValueError(tr("Backup-Manifest ist ungültig")) from exc
                if self.manifest.get("format") != "zeitarchiv-portable-backup":
                    raise ValueError(tr("Unbekanntes Backup-Format"))
                self._sums = {
                    entry["path"]: entry for entry in self.manifest.get("files", [])
                    if isinstance(entry, dict) and "path" in entry
                }
            if "index.sqlite" not in self.names:
                raise ValueError(tr("index.sqlite fehlt im Backup"))
            with tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False) as tmp:
                self._db_path = Path(tmp.name)
                with self._zip.open("index.sqlite") as source:
                    shutil.copyfileobj(source, tmp)
            self.db = sqlite3.connect(f"file:{self._db_path}?mode=ro", uri=True)
            self._db_open = True
            self.db.row_factory = sqlite3.Row
            if self.db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise ValueError(tr("SQLite-Integritätsprüfung fehlgeschlagen"))
        except zipfile.BadZipFile as exc:
            self.close()
            raise ValueError(tr("Datei ist kein gültiges ZIP-Backup")) from exc
        except sqlite3.DatabaseError as exc:
            self.close()
            raise ValueError(tr("index.sqlite ist keine gültige SQLite-Datenbank")) from exc
        except Exception:
            self.close()
            raise
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        if self._db_open:
            self.db.close()
            self._db_open = False
        if self._zip is not None:
            self._zip.close()
            self._zip = None
        if self._db_path is not None:
            self._db_path.unlink(missing_ok=True)
            self._db_path = None

    def has_table(self, table: str) -> bool:
        return self.db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone() is not None

    def read_bytes(self, name: str) -> bytes | None:
        if self._zip is None or name not in self.names:
            return None
        return self._zip.read(name)

    def extract(self, name: str, dest: Path) -> None:
        """Entpackt ein Mitglied und prüft Größe und SHA-256 gegen das Manifest
        (Legacy-Backups ohne Manifest haben nur die ZIP-eigene CRC-Prüfung)."""
        if self._zip is None:
            raise ValueError(tr("Backup ist nicht geöffnet"))
        dest.parent.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        size = 0
        with self._zip.open(name) as source, dest.open("wb") as out:
            for chunk in iter(lambda: source.read(1024 * 1024), b""):
                out.write(chunk)
                digest.update(chunk)
                size += len(chunk)
        entry = self._sums.get(name)
        if entry and (size != entry.get("size_bytes") or digest.hexdigest() != entry.get("sha256")):
            dest.unlink(missing_ok=True)
            raise ValueError(tr("Prüfsumme stimmt nicht: {name}", name=name))


def group_members(names: list[str]) -> dict[str, list[str]]:
    """ZIP-Mitglieder je Entität (archive/, rollup/, hot/)."""
    out: dict[str, list[str]] = {}
    for name in names:
        parts = name.split("/")
        if name.endswith("/"):
            continue
        if len(parts) >= 3 and parts[0] in ("archive", "rollup"):
            entity_id = parts[1]
        else:
            match = _HOT_RE.match(name)
            if not match:
                continue
            entity_id = match.group("id")
        try:
            validate_entity_id(entity_id)
        except ValueError:
            continue
        out.setdefault(entity_id, []).append(name)
    return out


# --------------------------------------------------------------------------- Helfer


def _utc_stamp(offset: int = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=offset)).strftime("%Y%m%dT%H%M%SZ")


def _reserve_rollback_dir(data_dir: Path) -> Path:
    for offset in range(1000):
        candidate = data_dir / f"{ROLLBACK_PREFIX}{_utc_stamp(offset)}"
        if not candidate.exists():
            return candidate
    raise ValueError(tr("Kein freier Rollback-Verzeichnisname verfügbar"))


def _table_cols(conn: sqlite3.Connection, table: str) -> list[str]:
    return [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]


def _insert_row(conn: sqlite3.Connection, table: str, row: dict, *, drop: tuple[str, ...] = (),
                override: dict | None = None) -> int:
    """Fügt row in table ein; Spalten, die das Ziel nicht (mehr) kennt, entfallen. Spalten,
    die nur das Ziel kennt (neuere App-Version), bekommen ihren Tabellen-Default."""
    cols_here = set(_table_cols(conn, table))
    data = {k: v for k, v in row.items() if k in cols_here and k not in drop}
    if override:
        data.update({k: v for k, v in override.items() if k in cols_here})
    names = ", ".join(data)
    marks = ", ".join("?" for _ in data)
    cur = conn.execute(f"INSERT INTO {table} ({names}) VALUES ({marks})", tuple(data.values()))
    return int(cur.lastrowid or 0)


def _rows(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[dict]:
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def _cur_rows(index: Index, sql: str, params: tuple = ()) -> list[dict]:
    with index._lock:
        return _rows(index._conn, sql, params)


def _diff_cols(a: dict, b: dict, ignore: set[str]) -> list[str]:
    return [c for c in a if c in b and c not in ignore and a[c] != b[c]]


def _prettify(column: str, value: object) -> str:
    from .. import formatting  # spät: formatting importiert nichts aus storage, aber so bleibt der Import zyklenfrei

    if value is None or value == "":
        return "—"
    labels = {
        "retention": formatting.RETENTION_LABELS,
        "resolution": formatting.RESOLUTION_LABELS,
        "compact_target": formatting.COMPACT_TARGET_LABELS,
        "value_filter": formatting.VALUE_FILTER_LABELS,
        "decimals": formatting.DECIMALS_LABELS,
    }.get(column)
    if labels is not None and str(value) in labels:
        return tr(labels[str(value)])
    if column in ("hourly_rollup", "ignore_values_enabled", "counter_auto_mark", "is_favorite"):
        return tr("Ja") if int(value) else tr("Nein")
    if column == "chart_options":
        return tr("angepasst") if str(value) not in ("{}", "") else "—"
    return str(value)


def _fmt_month(ts: float | None, tz: ZoneInfo) -> str:
    return datetime.fromtimestamp(ts, tz).strftime("%m/%Y") if ts else "—"


# --------------------------------------------------------------------------- Entitäten


def _current_rels(data_dir: Path, entity_id: str) -> list[Path]:
    """Relative Pfade (zu data_dir), die aktuell zu entity_id gehören."""
    rels: list[Path] = []
    for area in ("archive", "rollup"):
        entity_dir(data_dir, area, entity_id)  # Pfadwächter: wirft bei unsicherem Pfad
        if (data_dir / area / entity_id).exists():
            rels.append(Path(area) / entity_id)
    hot_root = data_dir / "hot"
    if hot_root.is_dir():
        pattern = re.compile(_HOT_NAME_RE.format(id=re.escape(entity_id)))
        rels.extend(Path("hot") / p.name for p in sorted(hot_root.iterdir()) if pattern.fullmatch(p.name))
    return rels


def _tree_size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def estimate_rollback_bytes(data_dir: Path, entity_ids: list[str]) -> int:
    total = 0
    for entity_id in entity_ids:
        for rel in _current_rels(data_dir, entity_id):
            total += _tree_size(data_dir / rel)
    return total


def list_entity_diff(source: BackupSource, index: Index, tz: ZoneInfo) -> list[dict]:
    """Entitäten des Backups mit Vergleich zum aktuellen Stand."""
    members = group_members(source.names)
    backup_rows = {row["entity_id"]: dict(row) for row in source.db.execute("SELECT * FROM entities")}
    current = {row["entity_id"]: dict(row) for row in index.list_entities()}
    out = []
    for entity_id in sorted(members):
        b = backup_rows.get(entity_id)
        if b is None:
            continue
        c = current.get(entity_id)
        b_rows = max(int(b.get("row_count") or 0) - int(b.get("deleted_count") or 0), 0)
        if c is None:
            status = "new"
            cur = None
        else:
            c_rows = max(int(c["row_count"] or 0) - int(c["deleted_count"] or 0), 0)
            same = (
                c_rows == b_rows
                and abs((c["first_ts"] or 0) - (b.get("first_ts") or 0)) < 0.001
                and abs((c["last_ts"] or 0) - (b.get("last_ts") or 0)) < 0.001
            )
            status = "same" if same else "diff"
            cur = {"rows": c_rows, "first": _fmt_month(c["first_ts"], tz), "last": _fmt_month(c["last_ts"], tz)}
        out.append({
            "entity_id": entity_id,
            "name": b.get("custom_name") or b.get("friendly_name") or entity_id,
            "status": status,
            "cur": cur,
            "bak": {"rows": b_rows, "first": _fmt_month(b.get("first_ts"), tz), "last": _fmt_month(b.get("last_ts"), tz)},
        })
    return out


def _recount_deleted(conn: sqlite3.Connection, entity_id: str) -> None:
    conn.execute(
        "UPDATE entities SET deleted_count = "
        "(SELECT COUNT(*) FROM deleted_points WHERE entity_id = ?) WHERE entity_id = ?",
        (entity_id, entity_id),
    )


def _snapshot_entity_db(conn: sqlite3.Connection, entity_id: str) -> dict:
    return {
        "existed": conn.execute("SELECT 1 FROM entities WHERE entity_id = ?", (entity_id,)).fetchone() is not None,
        "deleted_points": [list(r) for r in conn.execute(
            "SELECT ts, deleted_at FROM deleted_points WHERE entity_id = ?", (entity_id,))],
        "counter_mark_ignored": [r[0] for r in conn.execute(
            "SELECT ts FROM counter_mark_ignored WHERE entity_id = ?", (entity_id,))],
        "compacted_months": [list(r) for r in conn.execute(
            "SELECT year, month, target_resolution, compacted_at FROM compacted_months WHERE entity_id = ?",
            (entity_id,))],
    }


def _clear_entity_db(conn: sqlite3.Connection, entity_id: str) -> None:
    for table in ("deleted_points", "counter_mark_ignored", "compacted_months"):
        conn.execute(f"DELETE FROM {table} WHERE entity_id = ?", (entity_id,))


def restore_entities(
    data_dir: Path,
    index: Index,
    coordinator: StorageCoordinator,
    source: BackupSource,
    entity_ids: list[str],
    tz: ZoneInfo,
    *,
    source_label: str,
) -> dict:
    """Spielt die gewählten Entitäten aus dem Backup ein (siehe Moduldoku)."""
    members = group_members(source.names)
    ids = list(dict.fromkeys(entity_ids))
    if not ids:
        raise ValueError(tr("Keine Entität ausgewählt"))
    for entity_id in ids:
        validate_entity_id(entity_id)
        if entity_id not in members:
            raise ValueError(tr("Entität nicht im Backup: {entity_id}", entity_id=entity_id))
    backup_rows = {
        row["entity_id"]: dict(row)
        for row in source.db.execute(
            f"SELECT * FROM entities WHERE entity_id IN ({','.join('?' for _ in ids)})", tuple(ids))
    }
    for entity_id in ids:
        if entity_id not in backup_rows:
            raise ValueError(tr("Entität nicht im Backup: {entity_id}", entity_id=entity_id))

    staging = data_dir / f".zeitarchiv-selective-staging-{secrets.token_hex(4)}"
    try:
        # Entpacken und prüfen, bevor die Schreiber angehalten werden: das ist der
        # langsame Teil, der Austausch selbst dauert nur Verschiebungen.
        for entity_id in ids:
            for name in members[entity_id]:
                source.extract(name, staging / name)
        with coordinator.exclusive():
            rollback = _reserve_rollback_dir(data_dir)
            return _swap_entities(data_dir, index, source, ids, backup_rows, staging, rollback, tz, source_label)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _swap_entities(
    data_dir: Path, index: Index, source: BackupSource, ids: list[str], backup_rows: dict[str, dict],
    staging: Path, rollback: Path, tz: ZoneInfo, source_label: str,
) -> dict:
    moved: list[tuple[Path, Path]] = []
    placed: list[Path] = []
    rollback.mkdir(parents=True)
    try:
        for entity_id in ids:
            for rel in _current_rels(data_dir, entity_id):
                target = rollback / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                (data_dir / rel).replace(target)
                moved.append((target, data_dir / rel))
        for entity_id in ids:
            staged_rels = [Path("archive") / entity_id, Path("rollup") / entity_id]
            hot_pattern = re.compile(_HOT_NAME_RE.format(id=re.escape(entity_id)))
            if (staging / "hot").is_dir():
                staged_rels += [Path("hot") / p.name for p in sorted((staging / "hot").iterdir())
                                if hot_pattern.fullmatch(p.name)]
            for rel in staged_rels:
                if not (staging / rel).exists():
                    continue
                (data_dir / rel).parent.mkdir(parents=True, exist_ok=True)
                (staging / rel).replace(data_dir / rel)
                placed.append(data_dir / rel)

        now = time.time()
        snapshots: dict[str, dict] = {}
        with index._lock, index._conn:
            conn = index._conn
            for entity_id in ids:
                snapshots[entity_id] = _snapshot_entity_db(conn, entity_id)
                _clear_entity_db(conn, entity_id)
                for table, where in (
                    ("deleted_points", "entity_id"), ("counter_mark_ignored", "entity_id"),
                    ("compacted_months", "entity_id"),
                ):
                    if not source.has_table(table):
                        continue
                    for row in _rows(source.db, f"SELECT * FROM {table} WHERE {where} = ?", (entity_id,)):
                        _insert_row(conn, table, row, drop=("id",))
                if not snapshots[entity_id]["existed"]:
                    _insert_row(conn, "entities", backup_rows[entity_id], override={"updated_at": now})
                _recount_deleted(conn, entity_id)
        # Statistik (row_count, size_bytes, first_ts, last_ts) neu aus den Dateien bilden.
        reconcile.audit_storage_metadata(data_dir, index, tz, entity_ids=ids, repair=True)

        info = {
            "kind": "entities",
            "created_at": now,
            "source": source_label,
            "entities": ids,
            "db": snapshots,
        }
        (rollback / INFO_NAME).write_text(json.dumps(info), encoding="utf-8")
    except Exception:
        for path in placed:
            if path.is_dir():
                shutil.rmtree(path, ignore_errors=True)
            else:
                path.unlink(missing_ok=True)
        for target, original in reversed(moved):
            if target.exists():
                original.parent.mkdir(parents=True, exist_ok=True)
                target.replace(original)
        shutil.rmtree(rollback, ignore_errors=True)
        raise
    prune_rollbacks(data_dir)
    return {
        "kind": "entities",
        "rollback": rollback.name,
        "entities": ids,
        "created": [e for e in ids if not snapshots[e]["existed"]],
        "rows": sum(max(int(backup_rows[e].get("row_count") or 0) - int(backup_rows[e].get("deleted_count") or 0), 0)
                    for e in ids),
    }


def _undo_entities(
    data_dir: Path, index: Index, coordinator: StorageCoordinator, rollback: Path, info: dict, tz: ZoneInfo,
) -> dict:
    ids: list[str] = info["entities"]
    trash = data_dir / f".zeitarchiv-selective-trash-{secrets.token_hex(4)}"
    with coordinator.exclusive():
        trashed: list[tuple[Path, Path]] = []
        returned: list[tuple[Path, Path]] = []
        try:
            for entity_id in ids:
                for rel in _current_rels(data_dir, entity_id):
                    target = trash / rel
                    target.parent.mkdir(parents=True, exist_ok=True)
                    (data_dir / rel).replace(target)
                    trashed.append((target, data_dir / rel))
            for entity_id in ids:
                candidates = [Path("archive") / entity_id, Path("rollup") / entity_id]
                hot_pattern = re.compile(_HOT_NAME_RE.format(id=re.escape(entity_id)))
                if (rollback / "hot").is_dir():
                    candidates += [Path("hot") / p.name for p in sorted((rollback / "hot").iterdir())
                                   if hot_pattern.fullmatch(p.name)]
                for rel in candidates:
                    if (rollback / rel).exists():
                        (data_dir / rel).parent.mkdir(parents=True, exist_ok=True)
                        (rollback / rel).replace(data_dir / rel)
                        returned.append((data_dir / rel, rollback / rel))
            with index._lock, index._conn:
                conn = index._conn
                for entity_id in ids:
                    snap = info["db"][entity_id]
                    _clear_entity_db(conn, entity_id)
                    for ts, deleted_at in snap["deleted_points"]:
                        conn.execute("INSERT INTO deleted_points (entity_id, ts, deleted_at) VALUES (?, ?, ?)",
                                     (entity_id, ts, deleted_at))
                    for ts in snap["counter_mark_ignored"]:
                        conn.execute("INSERT OR IGNORE INTO counter_mark_ignored (entity_id, ts) VALUES (?, ?)",
                                     (entity_id, ts))
                    for year, month, target, compacted_at in snap["compacted_months"]:
                        conn.execute(
                            "INSERT OR REPLACE INTO compacted_months "
                            "(entity_id, year, month, target_resolution, compacted_at) VALUES (?, ?, ?, ?, ?)",
                            (entity_id, year, month, target, compacted_at))
                    if not snap["existed"]:
                        conn.execute("DELETE FROM entities WHERE entity_id = ?", (entity_id,))
                    else:
                        _recount_deleted(conn, entity_id)
            existing = [e for e in ids if info["db"][e]["existed"]]
            if existing:
                reconcile.audit_storage_metadata(data_dir, index, tz, entity_ids=existing, repair=True)
        except Exception:
            for current, origin in reversed(returned):
                if current.exists():
                    origin.parent.mkdir(parents=True, exist_ok=True)
                    current.replace(origin)
            for target, original in reversed(trashed):
                if target.exists():
                    original.parent.mkdir(parents=True, exist_ok=True)
                    target.replace(original)
            raise
        finally:
            shutil.rmtree(trash, ignore_errors=True)
    shutil.rmtree(rollback, ignore_errors=True)
    return {"kind": "entities", "entities": ids}


# --------------------------------------------------------------------------- Einstellungen


def _name_map(rows: list[dict]) -> dict[int, str]:
    return {r["id"]: r["name"] for r in rows}


def _table_struct(conn: sqlite3.Connection, table_id: int) -> dict:
    cols = _rows(conn, "SELECT * FROM table_columns WHERE table_id = ? ORDER BY position, id", (table_id,))
    rows = _rows(conn, "SELECT * FROM table_rows WHERE table_id = ? ORDER BY position, id", (table_id,))
    strip = lambda items: [{k: v for k, v in r.items() if k not in ("id", "table_id")} for r in items]  # noqa: E731
    return {"columns": strip(cols), "rows": strip(rows)}


def _norm_pins(conn: sqlite3.Connection, dashboard_id: int, charts: dict[int, str], tables: dict[int, str]) -> list[dict]:
    out = []
    for r in _rows(conn, "SELECT * FROM dashboard_pins WHERE dashboard_id = ? ORDER BY position, id", (dashboard_id,)):
        kind = r["item_type"]
        if kind == "chart":
            ref = _normalized_name(charts.get(r["item_id"], ""))
        elif kind == "table":
            ref = _normalized_name(tables.get(r["item_id"], ""))
        else:
            ref = r.get("item_entity_id") or ""
        data = {k: v for k, v in r.items() if k not in ("id", "dashboard_id", "item_id")}
        data["ref"] = ref
        out.append(data)
    return out


def _chart_detail(row: dict, changed: list[str] | None) -> str:
    if changed:
        labels = []
        for col in changed:
            label = tr(_CHART_FIELD_LABELS.get(col, N_("Darstellung")))
            if label not in labels:
                labels.append(label)
        return tr("Geändert: {fields}", fields=", ".join(labels))
    try:
        count = len(json.loads(row.get("entity_ids") or "[]"))
    except (json.JSONDecodeError, TypeError):
        count = 0
    return tr("Entitäten: {n}", n=count)


def load_settings_overview(source: BackupSource, index: Index, data_dir: Path) -> dict:
    """Vergleicht die Einstellungen des Backups mit dem aktuellen Stand."""
    bconn = source.db
    with index._lock:
        cconn = index._conn
        c_charts = _rows(cconn, "SELECT * FROM saved_charts")
        c_tables = _rows(cconn, "SELECT * FROM saved_tables")
        c_dashes = _rows(cconn, "SELECT * FROM dashboards")
        c_chart_names, c_table_names = _name_map(c_charts), _name_map(c_tables)
        c_struct = {t["id"]: _table_struct(cconn, t["id"]) for t in c_tables}
        c_pins = {d["id"]: _norm_pins(cconn, d["id"], c_chart_names, c_table_names) for d in c_dashes}
        c_entities = {r["entity_id"]: r for r in _rows(cconn, "SELECT * FROM entities")}
    by_name = lambda rows: {_normalized_name(r["name"]): r for r in rows}  # noqa: E731
    cc, ct, cd = by_name(c_charts), by_name(c_tables), by_name(c_dashes)

    b_charts = _rows(bconn, "SELECT * FROM saved_charts") if source.has_table("saved_charts") else []
    b_tables = _rows(bconn, "SELECT * FROM saved_tables") if source.has_table("saved_tables") else []
    b_dashes = _rows(bconn, "SELECT * FROM dashboards") if source.has_table("dashboards") else []
    b_chart_names, b_table_names = _name_map(b_charts), _name_map(b_tables)

    charts = []
    chart_status: dict[int, str] = {}
    for row in b_charts:
        cur = cc.get(_normalized_name(row["name"]))
        if cur is None:
            status, changed = "new", None
        else:
            changed = _diff_cols(row, cur, {"id", "name", "created_at", "updated_at", "is_favorite"})
            status = "diff" if changed else "same"
        chart_status[row["id"]] = status
        charts.append({"key": f"c:{row['id']}", "id": row["id"], "name": row["name"], "st": status,
                       "detail": _chart_detail(row, changed if status == "diff" else None)})

    tables = []
    table_status: dict[int, str] = {}
    for row in b_tables:
        struct = _table_struct(bconn, row["id"])
        cur = ct.get(_normalized_name(row["name"]))
        if cur is None:
            status, detail = "new", tr("Spalten: {c} · Zeilen: {r}", c=len(struct["columns"]), r=len(struct["rows"]))
        else:
            parts = []
            cstruct = c_struct[cur["id"]]
            if struct["columns"] != cstruct["columns"]:
                parts.append(tr("Spalten {a} → {b}", a=len(cstruct["columns"]), b=len(struct["columns"])))
            if struct["rows"] != cstruct["rows"]:
                parts.append(tr("Zeilen {a} → {b}", a=len(cstruct["rows"]), b=len(struct["rows"])))
            if row.get("style_json") != cur.get("style_json"):
                parts.append(tr("Darstellung"))
            status = "diff" if parts else "same"
            detail = " · ".join(parts) if parts else tr("Spalten: {c} · Zeilen: {r}", c=len(struct["columns"]), r=len(struct["rows"]))
        table_status[row["id"]] = status
        tables.append({"key": f"t:{row['id']}", "id": row["id"], "name": row["name"], "st": status, "detail": detail})

    dashboards = []
    for row in b_dashes:
        pins = _norm_pins(bconn, row["id"], b_chart_names, b_table_names)
        raw_pins = _rows(bconn, "SELECT * FROM dashboard_pins WHERE dashboard_id = ?", (row["id"],))
        needs = []
        missing = 0
        for pin in raw_pins:
            if pin["item_type"] == "chart" and pin["item_id"] in b_chart_names:
                needs.append(f"c:{pin['item_id']}")
            elif pin["item_type"] == "table" and pin["item_id"] in b_table_names:
                needs.append(f"t:{pin['item_id']}")
            elif pin["item_type"] == "entity" and pin.get("item_entity_id") not in c_entities:
                missing += 1
        needs = list(dict.fromkeys(needs))
        dep = {"same": 0, "new": 0, "diff": 0}
        for key in needs:
            dep[(chart_status if key[0] == "c" else table_status).get(int(key[2:]), "same")] += 1
        cur = cd.get(_normalized_name(row["name"]))
        n_pins = sum(1 for p in raw_pins)
        if cur is None:
            status, detail = "new", tr("Kacheln: {n}", n=n_pins)
        else:
            cur_pins = c_pins[cur["id"]]
            parts = []
            if pins != cur_pins:
                parts.append(tr("Kacheln {a} → {b}", a=len(cur_pins), b=len(pins)))
            if any(row.get(k) != cur.get(k) for k in ("precise_mode", "fill_gaps")):
                parts.append(tr("Einstellungen"))
            status = "diff" if parts else "same"
            detail = " · ".join(parts) if parts else tr("Kacheln: {n}", n=n_pins)
        dashboards.append({"key": f"d:{row['id']}", "id": row["id"], "name": row["name"], "st": status,
                           "detail": detail, "needs": needs, "dep": dep, "missing_entities": missing})

    entity_cfg = []
    if source.has_table("entities"):
        for brow in _rows(bconn, "SELECT * FROM entities ORDER BY entity_id"):
            crow = c_entities.get(brow["entity_id"])
            if crow is None:
                continue
            groups: dict[str, dict] = {}
            for group, cols in ENTITY_GROUPS.items():
                changed = [c for c in cols if c in brow and c in crow and brow[c] != crow[c]]
                if not changed:
                    continue
                lines = [
                    f"{tr(_COLUMN_LABELS[c])}: {_prettify(c, crow[c])} → {_prettify(c, brow[c])}" for c in changed
                ]
                short = False
                if "retention" in changed:
                    short = _retention_days(brow["retention"]) < _retention_days(crow["retention"])
                groups[group] = {"lines": lines, "short": short}
            if groups:
                entity_cfg.append({
                    "entity_id": brow["entity_id"],
                    "name": crow.get("custom_name") or crow.get("friendly_name") or brow["entity_id"],
                    "groups": groups,
                })

    brow_settings = {r["key"]: r["value"] for r in _rows(bconn, "SELECT key, value FROM settings")} \
        if source.has_table("settings") else {}
    with index._lock:
        crow_settings = {r["key"]: r["value"] for r in _rows(index._conn, "SELECT key, value FROM settings")}
    general = []
    for key, label in GENERAL_SETTINGS:
        if key in brow_settings and brow_settings[key] != crow_settings.get(key):
            warn = key == "default_retention" and _retention_days(brow_settings[key]) < _retention_days(crow_settings.get(key, "unlimited"))
            general.append({"key": f"g:{key}", "name": key, "label": tr(label),
                            "old": crow_settings.get(key, "—"), "new": brow_settings[key], "warn": warn})

    extra = []
    energie_changed = any(k in brow_settings and brow_settings[k] != crow_settings.get(k) for k in _ENERGIE_KEYS)
    if any(k in brow_settings for k in _ENERGIE_KEYS):
        extra.append({"key": "x:energie", "label": tr("Energiedashboard-Konfiguration"),
                      "st": "diff" if energie_changed else "same", "detail": tr("Speicher, Netzanschluss, Tarife")})
    b_symcon = source.read_bytes(SYMCON_FILE)
    if b_symcon is not None:
        current = (data_dir / SYMCON_FILE)
        same = current.is_file() and current.read_bytes() == b_symcon
        try:
            count = len(json.loads(b_symcon))
        except (json.JSONDecodeError, UnicodeDecodeError):
            count = 0
        extra.append({"key": "x:symcon", "label": tr("Symcon-Namenszuordnung"),
                      "st": "same" if same else "diff", "detail": tr("Namen: {n}", n=count)})

    return {
        "dashboards": dashboards, "charts": charts, "tables": tables,
        "entities": entity_cfg, "general": general, "extra": extra,
        "status": {"c": chart_status, "t": table_status},
    }


def _retention_days(value: str | None) -> float:
    return _RETENTION_DAYS.get(value or "unlimited", float("inf"))


def make_plan(overview: dict, selected: set[str], strategies: dict[str, str]) -> dict:
    """Aus Auswahl und Vorgehen je Objekt der Plan, den Zusammenfassung und Anwendung teilen."""
    items = {i["key"]: i for kind in ("dashboards", "charts", "tables") for i in overview[kind]}
    pick = lambda kind: [i for i in overview[kind] if i["key"] in selected and i["st"] != "same"]  # noqa: E731
    dashes, charts, tables = pick("dashboards"), pick("charts"), pick("tables")
    manual = {i["key"] for i in charts + tables}
    needed = list(dict.fromkeys(k for d in dashes for k in d["needs"]))
    auto = [items[k] for k in needed if k not in manual and items[k]["st"] != "same"]
    auto_c = [i for i in auto if i["key"].startswith("c:")]
    auto_t = [i for i in auto if i["key"].startswith("t:")]
    over = lambda i: i["st"] == "diff" and strategies.get(i["key"]) == "over"  # noqa: E731
    split = lambda lst: ([i for i in lst if not over(i)], [i for i in lst if over(i)])  # noqa: E731
    d_add, d_over = split(dashes)
    c_add, c_over = split(charts)
    t_add, t_over = split(tables)
    entities = {e["entity_id"]: e for e in overview["entities"]}
    groups = []
    for key in sorted(selected):
        if key.startswith("e:"):
            entity_id, _, group = key[2:].partition("|")
            if entity_id in entities and group in entities[entity_id]["groups"]:
                groups.append((entity_id, group))
    general = [g for g in overview["general"] if g["key"] in selected]
    extra = [x for x in overview["extra"] if x["key"] in selected and x["st"] != "same"]
    short = [(entities[e], entities[e]["groups"]["ret"]) for e, g in groups if g == "ret" and entities[e]["groups"]["ret"]["short"]]
    warnings = []
    if short:
        warnings.append(tr(
            "Aufbewahrung wird kürzer bei {names}. Der nächste Retention-Lauf löscht dort ältere Daten endgültig.",
            names=", ".join(f"„{e['name']}“" for e, _ in short)))
    if any(g["warn"] for g in general):
        warnings.append(tr("Die Standard-Aufbewahrung wird kürzer. Das gilt für neu angelegte Entitäten."))
    missing = sum(d["missing_entities"] for d in dashes)
    if missing:
        warnings.append(tr("Kacheln auf fehlende Entitäten werden übersprungen: {n}.", n=missing))
    n = {
        "d": len(dashes), "c": len(charts) + len(auto_c), "t": len(tables) + len(auto_t),
        "e": len(groups), "g": len(general) + len(extra),
    }
    return {
        "dashboards_add": d_add, "dashboards_over": d_over,
        "charts_add": c_add + auto_c, "charts_over": c_over,
        "tables_add": t_add + auto_t, "tables_over": t_over,
        "auto_charts": auto_c, "auto_tables": auto_t,
        "groups": groups, "entity_count": len({e for e, _ in groups}),
        "group_names": sorted({tr(ENTITY_GROUP_LABELS[g]) for _, g in groups}),
        "general": general, "extra": extra, "warnings": warnings,
        "auto_keys": {i["key"] for i in auto},
        "n": n, "total": sum(n.values()),
    }


def apply_settings(
    data_dir: Path, index: Index, source: BackupSource, overview: dict, plan: dict, *, source_label: str,
) -> dict:
    """Wendet den Plan in einer Transaktion an und schreibt das Rückgängig-Journal."""
    if not plan["total"]:
        raise ValueError(tr("Nichts ausgewählt"))
    bconn = source.db
    rollback = _reserve_rollback_dir(data_dir)
    journal: list[dict] = []
    skipped_pins = 0
    now = time.time()
    rollback.mkdir(parents=True)
    try:
        with index._lock, index._conn:
            conn = index._conn
            b_charts = {r["id"]: r for r in _rows(bconn, "SELECT * FROM saved_charts")} if source.has_table("saved_charts") else {}
            b_tables = {r["id"]: r for r in _rows(bconn, "SELECT * FROM saved_tables")} if source.has_table("saved_tables") else {}
            cur_charts = {_normalized_name(r["name"]): r for r in _rows(conn, "SELECT * FROM saved_charts")}
            cur_tables = {_normalized_name(r["name"]): r for r in _rows(conn, "SELECT * FROM saved_tables")}
            chart_map: dict[int, int] = {}
            table_map: dict[int, int] = {}
            # Verweise auf unveränderte (gleichnamige) Objekte
            for bid, row in b_charts.items():
                cur = cur_charts.get(_normalized_name(row["name"]))
                if cur is not None:
                    chart_map[bid] = cur["id"]
            for bid, row in b_tables.items():
                cur = cur_tables.get(_normalized_name(row["name"]))
                if cur is not None:
                    table_map[bid] = cur["id"]

            def new_name(table: str, name: str, exists: bool) -> str:
                return index._copy_name_locked(table, name) if exists else name

            for item in plan["charts_add"]:
                row = b_charts[item["id"]]
                exists = _normalized_name(row["name"]) in cur_charts
                new_id = _insert_row(conn, "saved_charts", row, drop=("id",), override={
                    "name": new_name("saved_charts", row["name"], exists), "created_at": now, "updated_at": now})
                chart_map[item["id"]] = new_id
                journal.append({"op": "delete_row", "table": "saved_charts", "id": new_id})
            for item in plan["charts_over"]:
                row = b_charts[item["id"]]
                cur = cur_charts[_normalized_name(row["name"])]
                journal.append({"op": "put_row", "table": "saved_charts", "row": cur})
                sets = {k: v for k, v in row.items() if k not in ("id", "name", "created_at")
                        and k in cur}
                sets["updated_at"] = now
                conn.execute(f"UPDATE saved_charts SET {', '.join(f'{k} = ?' for k in sets)} WHERE id = ?",
                             (*sets.values(), cur["id"]))
                chart_map[item["id"]] = cur["id"]

            def copy_struct(bid: int, new_id: int) -> None:
                for table in ("table_columns", "table_rows"):
                    for r in _rows(bconn, f"SELECT * FROM {table} WHERE table_id = ? ORDER BY position, id", (bid,)):
                        _insert_row(conn, table, r, drop=("id",), override={"table_id": new_id})

            for item in plan["tables_add"]:
                row = b_tables[item["id"]]
                exists = _normalized_name(row["name"]) in cur_tables
                new_id = _insert_row(conn, "saved_tables", row, drop=("id",), override={
                    "name": new_name("saved_tables", row["name"], exists), "created_at": now, "updated_at": now})
                copy_struct(item["id"], new_id)
                table_map[item["id"]] = new_id
                journal.append({"op": "delete_children", "table": "table_columns", "col": "table_id", "val": new_id})
                journal.append({"op": "delete_children", "table": "table_rows", "col": "table_id", "val": new_id})
                journal.append({"op": "delete_row", "table": "saved_tables", "id": new_id})
            for item in plan["tables_over"]:
                row = b_tables[item["id"]]
                cur = cur_tables[_normalized_name(row["name"])]
                journal.append({"op": "put_row", "table": "saved_tables", "row": cur})
                for table in ("table_columns", "table_rows"):
                    journal.append({"op": "replace_children", "table": table, "col": "table_id", "val": cur["id"],
                                    "rows": _rows(conn, f"SELECT * FROM {table} WHERE table_id = ?", (cur["id"],))})
                    conn.execute(f"DELETE FROM {table} WHERE table_id = ?", (cur["id"],))
                conn.execute("UPDATE saved_tables SET style_json = ?, updated_at = ? WHERE id = ?",
                             (row.get("style_json", "{}"), now, cur["id"]))
                copy_struct(item["id"], cur["id"])
                table_map[item["id"]] = cur["id"]

            b_dashes = {r["id"]: r for r in _rows(bconn, "SELECT * FROM dashboards")} if source.has_table("dashboards") else {}
            cur_dashes = {_normalized_name(r["name"]): r for r in _rows(conn, "SELECT * FROM dashboards")}
            local_entities = {r[0] for r in conn.execute("SELECT entity_id FROM entities")}
            temp_ids = iter(range(-1, -100000, -1))

            def copy_pins(bid: int, dashboard_id: int) -> int:
                skipped = 0
                for pin in _rows(bconn, "SELECT * FROM dashboard_pins WHERE dashboard_id = ? ORDER BY position, id", (bid,)):
                    kind = pin["item_type"]
                    if kind == "chart":
                        target = chart_map.get(pin["item_id"])
                    elif kind == "table":
                        target = table_map.get(pin["item_id"])
                    elif kind == "entity":
                        target = 0 if pin.get("item_entity_id") in local_entities else None
                    else:
                        target = 0
                    if target is None:
                        skipped += 1
                        continue
                    own = kind in ("entity", "section") or target == 0
                    pin_id = _insert_row(conn, "dashboard_pins", pin, drop=("id",), override={
                        "dashboard_id": dashboard_id, "item_id": next(temp_ids) if own else target})
                    if own:
                        conn.execute("UPDATE dashboard_pins SET item_id = ? WHERE id = ?", (pin_id, pin_id))
                return skipped

            for item in plan["dashboards_add"]:
                row = b_dashes[item["id"]]
                exists = _normalized_name(row["name"]) in cur_dashes
                position = (conn.execute("SELECT MAX(position) FROM dashboards").fetchone()[0] or 0) + 1
                new_id = _insert_row(conn, "dashboards", row, drop=("id",), override={
                    "name": new_name("dashboards", row["name"], exists), "position": position,
                    "is_default": 0, "locked": 0, "is_favorite": 0})
                skipped_pins += copy_pins(item["id"], new_id)
                journal.append({"op": "delete_children", "table": "dashboard_pins", "col": "dashboard_id", "val": new_id})
                journal.append({"op": "delete_row", "table": "dashboards", "id": new_id})
            for item in plan["dashboards_over"]:
                row = b_dashes[item["id"]]
                cur = cur_dashes[_normalized_name(row["name"])]
                journal.append({"op": "put_row", "table": "dashboards", "row": cur})
                journal.append({"op": "replace_children", "table": "dashboard_pins", "col": "dashboard_id",
                                "val": cur["id"], "rows": _rows(conn, "SELECT * FROM dashboard_pins WHERE dashboard_id = ?", (cur["id"],))})
                conn.execute("DELETE FROM dashboard_pins WHERE dashboard_id = ?", (cur["id"],))
                conn.execute("UPDATE dashboards SET precise_mode = ?, fill_gaps = ? WHERE id = ?",
                             (row.get("precise_mode", 0), row.get("fill_gaps", 0), cur["id"]))
                skipped_pins += copy_pins(item["id"], cur["id"])

            b_entities = {r["entity_id"]: r for r in _rows(bconn, "SELECT * FROM entities")}
            for entity_id, group in plan["groups"]:
                cols = [c for c in ENTITY_GROUPS[group] if c in b_entities[entity_id]]
                old = conn.execute(f"SELECT {', '.join(cols)} FROM entities WHERE entity_id = ?", (entity_id,)).fetchone()
                if old is None:
                    continue
                journal.append({"op": "update_entity", "entity_id": entity_id, "values": dict(old)})
                conn.execute(
                    f"UPDATE entities SET {', '.join(f'{c} = ?' for c in cols)}, updated_at = ? WHERE entity_id = ?",
                    (*(b_entities[entity_id][c] for c in cols), now, entity_id))

            b_settings = {r["key"]: r["value"] for r in _rows(bconn, "SELECT key, value FROM settings")}

            def put_setting(key: str, value: str) -> None:
                old = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
                journal.append({"op": "set_setting", "key": key, "value": old["value"] if old else None})
                conn.execute("INSERT INTO settings (key, value) VALUES (?, ?) "
                             "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (key, value))

            reset_runs: set[str] = set()
            for item in plan["general"]:
                put_setting(item["name"], b_settings[item["name"]])
                if item["name"] in _SETTING_NEXT_RUN:
                    reset_runs.add(_SETTING_NEXT_RUN[item["name"]])
            for item in plan["extra"]:
                if item["key"] == "x:energie":
                    for key in _ENERGIE_KEYS:
                        if key in b_settings:
                            put_setting(key, b_settings[key])
                elif item["key"] == "x:symcon":
                    target = data_dir / SYMCON_FILE
                    if target.is_file():
                        shutil.copy2(target, rollback / SYMCON_FILE)
                    journal.append({"op": "symcon", "had_file": target.is_file()})
                    data = source.read_bytes(SYMCON_FILE)
                    if data is not None:
                        tmp = target.with_suffix(".json.tmp")
                        tmp.write_bytes(data)
                        tmp.replace(target)
            # Geänderte Zeitpläne müssen ihren nächsten Lauf neu berechnen.
            for key in reset_runs:
                put_setting(key, "")

            (rollback / INFO_NAME).write_text(json.dumps({
                "kind": "settings", "created_at": now, "source": source_label, "journal": journal,
            }), encoding="utf-8")
    except Exception:
        shutil.rmtree(rollback, ignore_errors=True)
        raise
    prune_rollbacks(data_dir)
    return {"kind": "settings", "rollback": rollback.name, "total": plan["total"], "skipped_pins": skipped_pins, "n": plan["n"]}


def _undo_settings(data_dir: Path, index: Index, rollback: Path, info: dict) -> dict:
    with index._lock, index._conn:
        conn = index._conn
        for op in reversed(info["journal"]):
            kind = op["op"]
            if kind == "delete_row":
                conn.execute(f"DELETE FROM {op['table']} WHERE id = ?", (op["id"],))
            elif kind == "delete_children":
                conn.execute(f"DELETE FROM {op['table']} WHERE {op['col']} = ?", (op["val"],))
            elif kind == "put_row":
                _put_row(conn, op["table"], op["row"])
            elif kind == "replace_children":
                conn.execute(f"DELETE FROM {op['table']} WHERE {op['col']} = ?", (op["val"],))
                for row in op["rows"]:
                    _put_row(conn, op["table"], row)
            elif kind == "update_entity":
                values = op["values"]
                conn.execute(f"UPDATE entities SET {', '.join(f'{c} = ?' for c in values)} WHERE entity_id = ?",
                             (*values.values(), op["entity_id"]))
            elif kind == "set_setting":
                if op["value"] is None:
                    conn.execute("DELETE FROM settings WHERE key = ?", (op["key"],))
                else:
                    conn.execute("INSERT INTO settings (key, value) VALUES (?, ?) "
                                 "ON CONFLICT(key) DO UPDATE SET value = excluded.value", (op["key"], op["value"]))
            elif kind == "symcon":
                target = data_dir / SYMCON_FILE
                saved = rollback / SYMCON_FILE
                if op["had_file"] and saved.is_file():
                    shutil.copy2(saved, target)
                elif not op["had_file"]:
                    target.unlink(missing_ok=True)
    shutil.rmtree(rollback, ignore_errors=True)
    return {"kind": "settings"}


def _put_row(conn: sqlite3.Connection, table: str, row: dict) -> None:
    cols = set(_table_cols(conn, table))
    data = {k: v for k, v in row.items() if k in cols}
    conn.execute(
        f"INSERT OR REPLACE INTO {table} ({', '.join(data)}) VALUES ({', '.join('?' for _ in data)})",
        tuple(data.values()),
    )


# --------------------------------------------------------------------------- Rollbacks


def _read_info(data_dir: Path, name: str) -> tuple[Path, dict]:
    if not ROLLBACK_RE.fullmatch(name) or Path(name).name != name:
        raise ValueError(tr("Ungültiger Rollback-Name"))
    rollback = data_dir / name
    info_path = rollback / INFO_NAME
    if not info_path.is_file():
        raise ValueError(tr("Rollback nicht gefunden"))
    return rollback, json.loads(info_path.read_text(encoding="utf-8"))


def undo(data_dir: Path, index: Index, coordinator: StorageCoordinator, name: str, tz: ZoneInfo) -> dict:
    """Macht einen selektiven Restore rückgängig und löscht danach dessen Rollback."""
    rollback, info = _read_info(data_dir, name)
    if info["kind"] == "entities":
        return _undo_entities(data_dir, index, coordinator, rollback, info, tz)
    return _undo_settings(data_dir, index, rollback, info)


def list_rollbacks(data_dir: Path) -> list[dict]:
    out = []
    for path in sorted(data_dir.glob(f"{ROLLBACK_PREFIX}*"), reverse=True):
        if not path.is_dir() or not ROLLBACK_RE.fullmatch(path.name):
            continue
        try:
            info = json.loads((path / INFO_NAME).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        out.append({
            "name": path.name, "kind": info.get("kind"), "created_at": info.get("created_at"),
            "source": info.get("source", ""), "size_bytes": _tree_size(path),
            "entities": info.get("entities", []),
        })
    return out


def delete_rollback(data_dir: Path, name: str) -> bool:
    if not ROLLBACK_RE.fullmatch(name) or Path(name).name != name:
        return False
    path = data_dir / name
    if not path.is_dir():
        return False
    shutil.rmtree(path)
    return True


def prune_rollbacks(data_dir: Path, keep_count: int = ROLLBACK_KEEP_COUNT) -> int:
    names = [r["name"] for r in list_rollbacks(data_dir)]
    removed = 0
    for name in names[keep_count:]:
        removed += int(delete_rollback(data_dir, name))
    return removed
