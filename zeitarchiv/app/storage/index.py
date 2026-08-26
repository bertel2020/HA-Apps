"""SQLite-Index: ein Datensatz pro Entität — Typ, Auflösung, Aufbewahrung, Statistiken.

Genau die Felder, die die Entitäten-Tabelle (Konzept Abschnitt 03) braucht:
Datensätze, erster/letzter Wert, Größe — plus Typ-Ableitung nach derselben
Tabelle (state_class → Standard/Zähler, Domain → Schalter).
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from collections.abc import Callable
from pathlib import Path

from .paths import validate_entity_id

SWITCH_DOMAINS = {"binary_sensor", "switch", "input_boolean"}
COUNTER_STATE_CLASSES = {"total", "total_increasing"}

DEFAULT_RESOLUTION = "raw"
DEFAULT_RETENTION = "unlimited"
VALUE_FILTER_HEARTBEAT_SECONDS = 6 * 60 * 60

_RESOLUTION_SECONDS = {
    "30s": 30,
    "1min": 60,
    "5min": 5 * 60,
    "15min": 15 * 60,
    "1h": 60 * 60,
}


def should_accept_write(
    resolution: str,
    last_ts: float | None,
    new_ts: float,
) -> bool:
    """Prüft den Mindestabstand zum letzten tatsächlich gespeicherten Wert.

    ``raw`` speichert jedes Event. Unbekannte Werte werden ebenfalls wie
    ``raw`` behandelt, damit eine beschädigte oder zukünftige Einstellung
    nicht unbemerkt Messwerte verwirft. Die Intervalle laufen relativ zum
    letzten akzeptierten Zeitstempel und sind nicht an Uhrzeit-Buckets gebunden.
    """
    interval = _RESOLUTION_SECONDS.get(resolution)
    if interval is None or last_ts is None:
        return True
    return new_ts - last_ts >= interval

def should_accept_value(
    value_filter: str,
    decimals: str,
    last_value: float | None,
    last_ts: float | None,
    new_value: float,
    new_ts: float,
) -> bool:
    """Prüft den optionalen Wertänderungsfilter einer Entität.

    ``decimals`` entspricht der sichtbaren Genauigkeit: ``auto`` zeigt höchstens
    drei Nachkommastellen und wird deshalb wie ``3`` behandelt. Auch bei
    unverändertem gerundetem Wert wird spätestens alle sechs Stunden ein
    Lebenszeichen gespeichert, damit lange konstante Verläufe und ``last_ts``
    nicht vollständig stehen bleiben.
    """
    if value_filter != "decimals" or last_value is None or last_ts is None:
        return True
    try:
        precision = 3 if decimals == "auto" else max(0, min(int(decimals), 12))
    except (TypeError, ValueError):
        precision = 3
    if round(last_value, precision) != round(new_value, precision):
        return True
    return new_ts - last_ts >= VALUE_FILTER_HEARTBEAT_SECONDS

# Allowlist für ORDER BY — Spaltennamen lassen sich in SQLite nicht parametrisieren,
# also nie direkt einen Request-Parameter in die Query interpolieren. Die Werte
# sind vollständige SQL-Ausdrücke (kein Freitext), deshalb auch "entity_id" hier
# als COALESCE-Ausdruck: die Tabelle zeigt primär den Anzeigenamen an, also soll
# die Sortierung "Entität" auch danach gehen, nicht nach der rohen entity_id.
_DISPLAY_NAME_EXPR = "COALESCE(friendly_name, entity_id) COLLATE NOCASE"
SORTABLE_COLUMNS = {
    "entity_id": _DISPLAY_NAME_EXPR,
    "friendly_name": _DISPLAY_NAME_EXPR,
    "type": "aggregation_type",
    "resolution": "resolution",
    "retention": "retention",
    "unit": "unit",
    "rows": "MAX(row_count - (SELECT COUNT(*) FROM deleted_points d WHERE d.entity_id = entities.entity_id), 0)",
    "first_ts": "first_ts",
    "last_ts": "last_ts",
    "size": "size_bytes",
}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS entities (
    entity_id TEXT PRIMARY KEY,
    aggregation_type TEXT NOT NULL,
    resolution TEXT NOT NULL DEFAULT 'raw',
    retention TEXT NOT NULL DEFAULT 'unlimited',
    decimals TEXT NOT NULL DEFAULT 'auto',
    value_filter TEXT NOT NULL DEFAULT 'off',
    gap_threshold TEXT NOT NULL DEFAULT '15',
    outlier_threshold TEXT NOT NULL DEFAULT '25',
    unit TEXT,
    state_class TEXT,
    friendly_name TEXT,
    first_ts REAL,
    last_ts REAL,
    last_value REAL,
    row_count INTEGER NOT NULL DEFAULT 0,
    size_bytes INTEGER NOT NULL DEFAULT 0,
    is_favorite INTEGER NOT NULL DEFAULT 0,
    display_mode TEXT NOT NULL DEFAULT 'onoff',
    updated_at REAL NOT NULL
);

-- Soft-Delete für die Bereinigungs-GUI (Konzept Abschnitt 04): "nie destruktiv" —
-- Zeitstempel landen hier statt physisch aus Hot-Buffer/Parquet entfernt zu werden.
-- Eine Zeile pro gelöschtem VORKOMMEN, kein Set eindeutiger Zeitstempel: bei
-- einem Duplikat (zwei Rohwerte mit exakt demselben Zeitstempel) muss sich
-- gezielt nur eines der beiden Vorkommen entfernen lassen, ohne das andere
-- gleich mit zu löschen.
CREATE TABLE IF NOT EXISTS deleted_points (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id TEXT NOT NULL,
    ts REAL NOT NULL,
    deleted_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_deleted_points_entity_ts
    ON deleted_points(entity_id, ts);
CREATE INDEX IF NOT EXISTS idx_deleted_points_entity_deleted_at
    ON deleted_points(entity_id, deleted_at);

-- Archiv-weite Schnappschüsse für die Statistik-Übersicht (Konzept Abschnitt 03,
-- "Verlaufs-Sparkline"/"Allgemeine Statistik-Übersicht"). Der interne
-- Wartungsplaner schreibt sie unabhängig von Seitenaufrufen stündlich fort.
CREATE TABLE IF NOT EXISTS stats_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    entity_count INTEGER NOT NULL,
    total_rows INTEGER NOT NULL,
    total_size_bytes INTEGER NOT NULL
);

-- App-eigene globale Einstellungen (Konzept Abschnitt 03, "Einstellungen"-
-- Bereich) — bewusst eine eigene Tabelle statt der Supervisor-options.json:
-- die schreibt/verwaltet der Supervisor selbst, ein Zugriff von hier aus würde
-- mit dessen eigener Zustandsverwaltung kollidieren. Aktuell genutzt für die
-- globalen Auflösungs-/Aufbewahrungs-Standardwerte neu erkannter Entitäten.
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- Dauerhafte Historie manueller und geplanter Sicherungen. Im Gegensatz zu
-- einem reinen "last_run" bleibt damit auch nach einem Neustart sichtbar, ob
-- ein Lauf erfolgreich, fehlgeschlagen oder mitten im Schreiben abgebrochen ist.
CREATE TABLE IF NOT EXISTS backup_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trigger TEXT NOT NULL,
    scheduled_for REAL,
    started_at REAL,
    finished_at REAL,
    status TEXT NOT NULL,
    filename TEXT,
    size_bytes INTEGER,
    error TEXT,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_backup_jobs_created_at
    ON backup_jobs(created_at DESC);

-- Ausführungsverlauf der endgültigen Daten-Retention. Die gelöschten Mengen
-- werden mitgespeichert, damit ein automatischer Lauf später nachvollziehbar
-- bleibt und nicht nur als anonymer Zeitstempel erscheint.
CREATE TABLE IF NOT EXISTS retention_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trigger TEXT NOT NULL,
    scheduled_for REAL,
    started_at REAL,
    finished_at REAL,
    status TEXT NOT NULL,
    rows_deleted INTEGER,
    bytes_freed INTEGER,
    months_deleted INTEGER,
    entities_affected INTEGER,
    error TEXT,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_retention_jobs_created_at
    ON retention_jobs(created_at DESC);

-- Persistente Idempotenz für den Live-Schreibpfad. "processing" wird vor
-- dem Dateianhang gespeichert; "done" wird gemeinsam mit den Entitäts-
-- Metadaten committed. Nach einem Crash lässt sich über die Event-ID in Hot-
-- CSV/Parquet eindeutig feststellen, ob nur der DB-Abschluss nachzuholen ist.
CREATE TABLE IF NOT EXISTS ingested_events (
    event_id TEXT PRIMARY KEY,
    entity_id TEXT NOT NULL,
    ts REAL NOT NULL,
    status TEXT NOT NULL,
    recorded INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    completed_at REAL
);
CREATE INDEX IF NOT EXISTS idx_ingested_events_status
    ON ingested_events(status);
CREATE INDEX IF NOT EXISTS idx_ingested_events_status_completed
    ON ingested_events(status, completed_at);

-- Abgelegte Charts (Konzept "Offene Punkte": eigener Bereich zum Erstellen und
-- "Ablegen" von Charts, inkl. Multi-Entitäts-Charts). entity_ids als
-- JSON-Array statt einer eigenen Verknüpfungstabelle — es gibt keine
-- Notwendigkeit, gespeicherte Charts nach einzelnen Entitäten zu durchsuchen,
-- eine eigene m:n-Tabelle wäre hier nur zusätzliche Komplexität ohne Nutzen.
-- Ein gespeichertes Chart ist eine gespeicherte ABFRAGE (Entitäten + Zeitraum-
-- Einstellungen), kein eingefrorener Schnappschuss — beim Aufruf werden die
-- Daten immer live neu geladen, wie bei der Entität-eigenen Chart-Seite auch.
CREATE TABLE IF NOT EXISTS saved_charts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    entity_ids TEXT NOT NULL,
    range_key TEXT NOT NULL DEFAULT 'day',
    continuous INTEGER NOT NULL DEFAULT 0,
    resolution_preset TEXT NOT NULL DEFAULT 'auto',
    dynamic_y_axis INTEGER NOT NULL DEFAULT 1,
    dashboard_animation INTEGER NOT NULL DEFAULT 1,
    chart_stats INTEGER NOT NULL DEFAULT 1,
    is_favorite INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

-- Vergleichstabellen (Konzept "Offene Punkte": Vorbild Symcon-Archiv-
-- Vergleichstabellen — Zeilen = Größen, Spalten = Zeiträume). Wie
-- saved_charts eine gespeicherte ABFRAGE, kein eingefrorener Datenstand: die
-- Zellenwerte werden bei jedem Aufruf live über /api/query-multi neu gebildet
-- (table_editor.html), hier steht nur die STRUKTUR. Spalten und Zeilen als
-- eigene Tabellen statt JSON-Spalten auf saved_tables — anders als bei
-- saved_charts' entity_ids gibt es hier mehrere strukturierte Felder pro
-- Element (Zeile: Typ/Formel/fett, Spalte: Zeitraum/Offset/Vorjahr), die als
-- flaches JSON-Array unhandlich zu validieren/migrieren wären.
-- style_json: rein optische Darstellung (Zebra-Streifen, Rahmen, Dichte,
-- hervorgehobene Kopfzeile) — bewusst getrennt von Spalten/Zeilen (Struktur/
-- Berechnung), damit ein Layout-Wechsel nie die Abfrage-Definition berührt.
-- JSON statt eigener Spalten je Option: reine Präsentationsdaten, die das
-- Frontend unverändert durchreicht (main.py validiert nur grob, siehe
-- _validate_table_style()), kein Feld davon fließt in eine Berechnung ein.
CREATE TABLE IF NOT EXISTS saved_tables (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    style_json TEXT NOT NULL DEFAULT '{}',
    is_favorite INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

-- Dashboard-Kacheln (Konzept "Offene Punkte", jetzt erweitert): eine einzige
-- Anheft-Tabelle für BEIDE anheftbaren Objekttypen (Charts und
-- Vergleichstabellen) statt je einer eigenen dashboard_position-Spalte auf
-- saved_charts/saved_tables — sonst wäre die Reihenfolge zwischen einem
-- Chart an Position 1 und einer Tabelle an Position 1 nicht eindeutig
-- vergleichbar. item_type/item_id statt einer Fremdschlüssel-Spalte pro Typ,
-- weil hier zwei unterschiedliche Quelltabellen gemeinsam sortiert werden.
CREATE TABLE IF NOT EXISTS dashboard_pins (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_type TEXT NOT NULL,
    item_id INTEGER NOT NULL,
    position INTEGER NOT NULL,
    grid_cols INTEGER NOT NULL DEFAULT 1,
    grid_rows INTEGER NOT NULL DEFAULT 1,
    UNIQUE(item_type, item_id)
);

-- Eine Spalte = ein Zeitraum ("2026", "Aug VJ", "Heute", …) — label ist frei
-- wählbarer Text (Konzept: deckt sich NICHT 1:1 mit range_key, "Aug VJ" ist
-- eine Beschriftung, keine Berechnungsvorschrift), range_key/offset/
-- year_over_year sind die tatsächliche Abfrage (dieselbe Perioden-Logik wie
-- Charts, query._window()).
CREATE TABLE IF NOT EXISTS table_columns (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    table_id INTEGER NOT NULL,
    position INTEGER NOT NULL,
    label TEXT NOT NULL,
    range_key TEXT NOT NULL DEFAULT 'month',
    offset INTEGER NOT NULL DEFAULT 0,
    year_over_year INTEGER NOT NULL DEFAULT 0
);

-- Eine Zeile ist eine von drei Arten (Konzept: v1 entity, v2 group, v3
-- formula — hier direkt alle drei gemeinsam umgesetzt, nicht nacheinander):
-- "entity" (eine einzelne Entität, entity_ids hat genau 1 Element),
-- "group" (mehrere Entitäten zu einer Summen-Zeile zusammengefasst,
-- entity_ids beliebig viele Elemente — dieselbe Bedeutung wie "group" bei
-- entity_ids, nur ohne eigene entity_groups-Tabelle: eine Gruppe ist hier
-- nur innerhalb EINER Tabellenzeile gültig, kein eigenständiges,
-- wiederverwendbares Objekt, siehe Kommentar dazu in main.py),
-- "formula" (formula referenziert andere Zeilen dieser Tabelle über deren
-- Buchstaben-Kürzel, z. B. "A / B * 100", ausgewertet client-seitig).
-- bold hebt eine Zeile optisch hervor (Konzept: "fett hervorgehobene
-- Summen-Zeilen").
CREATE TABLE IF NOT EXISTS table_rows (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    table_id INTEGER NOT NULL,
    position INTEGER NOT NULL,
    label TEXT NOT NULL,
    row_type TEXT NOT NULL DEFAULT 'entity',
    entity_ids TEXT NOT NULL DEFAULT '[]',
    formula TEXT NOT NULL DEFAULT '',
    formula_unit TEXT NOT NULL DEFAULT '',
    bold INTEGER NOT NULL DEFAULT 0
);
"""


def filter_deleted_occurrences(
    rows: list[tuple[float, float]], deleted_counts: dict[float, int]
) -> list[tuple[float, float]]:
    """Entfernt aus rows genau so viele Vorkommen je Zeitstempel wie in
    deleted_counts hinterlegt — NICHT pauschal alle Zeilen mit diesem
    Zeitstempel. rows muss in einer stabilen, deterministischen Reihenfolge
    vorliegen (z. B. sortiert), sonst würde bei einem Duplikat mal die eine,
    mal die andere Zeile verschwinden. Gemeinsam von cleanup.py und query.py
    genutzt, damit Bereinigungs-Tabelle und Chart-Anzeige nach dem Löschen
    einer einzelnen Duplikat-Zeile konsistent bleiben."""
    remaining = dict(deleted_counts)
    kept: list[tuple[float, float]] = []
    for ts, value in rows:
        skip = remaining.get(ts, 0)
        if skip > 0:
            remaining[ts] = skip - 1
            continue
        kept.append((ts, value))
    return kept


def derive_type(domain: str, state_class: str | None) -> str:
    """Leitet den Zeitarchiv-Typ aus Domain/state_class ab (Konzept Abschnitt 03)."""
    if domain in SWITCH_DOMAINS:
        return "switch"
    if state_class in COUNTER_STATE_CLASSES:
        return "counter"
    return "standard"


class Index:
    """Dünner Wrapper um die SQLite-Datenbank. Ein Lock, weil sqlite3 hier
    aus mehreren FastAPI-Requests parallel angesprochen werden kann."""

    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock, self._conn:
            self._conn.executescript(_SCHEMA)
            self._migrate()

    def _migrate(self) -> None:
        """Fügt Spalten nach, die es in einer schon laufenden Datenbank noch nicht gibt
        (z. B. friendly_name, nachträglich in Phase 2 ergänzt) — CREATE TABLE IF NOT EXISTS
        allein reicht dafür nicht, das legt nur neue Tabellen an."""
        columns = {row["name"] for row in self._conn.execute("PRAGMA table_info(entities)")}
        if "friendly_name" not in columns:
            self._conn.execute("ALTER TABLE entities ADD COLUMN friendly_name TEXT")
        if "decimals" not in columns:
            self._conn.execute("ALTER TABLE entities ADD COLUMN decimals TEXT NOT NULL DEFAULT 'auto'")
        if "value_filter" not in columns:
            self._conn.execute("ALTER TABLE entities ADD COLUMN value_filter TEXT NOT NULL DEFAULT 'off'")
        if "last_value" not in columns:
            self._conn.execute("ALTER TABLE entities ADD COLUMN last_value REAL")
        if "gap_threshold" not in columns:
            self._conn.execute("ALTER TABLE entities ADD COLUMN gap_threshold TEXT NOT NULL DEFAULT '15'")
        if "outlier_threshold" not in columns:
            self._conn.execute("ALTER TABLE entities ADD COLUMN outlier_threshold TEXT NOT NULL DEFAULT '25'")
        if "is_favorite" not in columns:
            # Favoriten (Konzept-Erweiterung) — Entitäten lassen sich markieren,
            # um sie in der Übersicht/Liste immer oben zu finden.
            self._conn.execute("ALTER TABLE entities ADD COLUMN is_favorite INTEGER NOT NULL DEFAULT 0")
        if "display_mode" not in columns:
            # Nur für aggregation_type "switch" relevant (binary_sensor/switch/
            # input_boolean): steuert, ob Charts die Bucket-Werte (on_seconds)
            # als Dauer (h/m) oder als Rohwert/AN-Anteil anzeigen. 'onoff' erhält
            # das bisherige Verhalten für alle bestehenden Entitäten bei.
            self._conn.execute("ALTER TABLE entities ADD COLUMN display_mode TEXT NOT NULL DEFAULT 'onoff'")

        dp_columns = {row["name"] for row in self._conn.execute("PRAGMA table_info(deleted_points)")}
        if "id" not in dp_columns:
            # Ältere Version hatte PRIMARY KEY (entity_id, ts) und konnte deshalb nur
            # EINEN gelöschten Zustand pro Zeitstempel abbilden — bei Duplikaten
            # (zwei Rohwerte mit demselben Zeitstempel) ließ sich so nie nur eines
            # der beiden Vorkommen entfernen. ALTER TABLE kann in SQLite keine
            # PRIMARY-KEY-Beschränkung entfernen, deshalb Tabelle neu aufbauen.
            self._conn.execute("ALTER TABLE deleted_points RENAME TO deleted_points_old")
            self._conn.execute(
                """CREATE TABLE deleted_points (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    entity_id TEXT NOT NULL,
                    ts REAL NOT NULL,
                    deleted_at REAL NOT NULL
                )"""
            )
            self._conn.execute(
                "INSERT INTO deleted_points (entity_id, ts, deleted_at) "
                "SELECT entity_id, ts, deleted_at FROM deleted_points_old"
            )
            self._conn.execute("DROP TABLE deleted_points_old")

        # Beim Neuaufbau der alten deleted_points-Tabelle werden deren Indizes
        # zusammen mit der umbenannten Alttabelle entfernt. Deshalb hier nach
        # sämtlichen Schema-Migrationen idempotent erneut sicherstellen.
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_deleted_points_entity_ts "
            "ON deleted_points(entity_id, ts)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_deleted_points_entity_deleted_at "
            "ON deleted_points(entity_id, deleted_at)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_ingested_events_status_completed "
            "ON ingested_events(status, completed_at)"
        )

        sc_columns = {row["name"] for row in self._conn.execute("PRAGMA table_info(saved_charts)")}
        if "entity_names" not in sc_columns:
            # Optionale, individuelle Anzeigenamen je Entität innerhalb eines
            # Charts (Konzept "Offene Punkte") — JSON-Objekt {entity_id: name},
            # nur Einträge mit tatsächlicher Überschreibung, fehlende Entitäten
            # fallen weiterhin auf ihren friendly_name zurück.
            self._conn.execute("ALTER TABLE saved_charts ADD COLUMN entity_names TEXT NOT NULL DEFAULT '{}'")
        if "dashboard_position" not in sc_columns:
            # Historische Zwischenlösung (siehe Migration unten) — Dashboard-
            # Kacheln leben inzwischen in der typübergreifenden dashboard_pins-
            # Tabelle, diese Spalte wird nur noch für eine einmalige
            # Übernahme alter Daten gebraucht, dann nie wieder beschrieben.
            self._conn.execute("ALTER TABLE saved_charts ADD COLUMN dashboard_position INTEGER")
        if "is_favorite" not in sc_columns:
            self._conn.execute("ALTER TABLE saved_charts ADD COLUMN is_favorite INTEGER NOT NULL DEFAULT 0")
        if "resolution_preset" not in sc_columns:
            self._conn.execute(
                "ALTER TABLE saved_charts ADD COLUMN resolution_preset TEXT NOT NULL DEFAULT 'auto'"
            )
        if "dynamic_y_axis" not in sc_columns:
            self._conn.execute(
                "ALTER TABLE saved_charts ADD COLUMN dynamic_y_axis INTEGER NOT NULL DEFAULT 1"
            )
        if "dashboard_animation" not in sc_columns:
            self._conn.execute(
                "ALTER TABLE saved_charts ADD COLUMN dashboard_animation INTEGER NOT NULL DEFAULT 1"
            )
        if "chart_stats" not in sc_columns:
            self._conn.execute(
                "ALTER TABLE saved_charts ADD COLUMN chart_stats INTEGER NOT NULL DEFAULT 1"
            )

        if self._conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='saved_tables'").fetchone()[0]:
            st_columns = {row["name"] for row in self._conn.execute("PRAGMA table_info(saved_tables)")}
            if "style_json" not in st_columns:
                # Rein optische Darstellung (Zebra-Streifen/Rahmen/Dichte/Kopfzeile,
                # siehe CREATE TABLE-Kommentar oben) — nachträglich für
                # Vergleichstabellen ergänzt, die schon vor dieser Option
                # angelegt wurden.
                self._conn.execute("ALTER TABLE saved_tables ADD COLUMN style_json TEXT NOT NULL DEFAULT '{}'")
            if "is_favorite" not in st_columns:
                self._conn.execute("ALTER TABLE saved_tables ADD COLUMN is_favorite INTEGER NOT NULL DEFAULT 0")

        # Leer bedeutet: Einheit automatisch von den referenzierten
        # Ausgangswerten übernehmen. Bestehende Tabellen bleiben dadurch
        # kompatibel; bei Bedarf kann eine Formel eine eigene Einheit tragen.
        table_row_columns = {row["name"] for row in self._conn.execute("PRAGMA table_info(table_rows)")}
        if "formula_unit" not in table_row_columns:
            self._conn.execute("ALTER TABLE table_rows ADD COLUMN formula_unit TEXT NOT NULL DEFAULT ''")

        dashboard_columns = {row["name"] for row in self._conn.execute("PRAGMA table_info(dashboard_pins)")}
        if "grid_cols" not in dashboard_columns:
            self._conn.execute("ALTER TABLE dashboard_pins ADD COLUMN grid_cols INTEGER NOT NULL DEFAULT 1")
        if "grid_rows" not in dashboard_columns:
            self._conn.execute("ALTER TABLE dashboard_pins ADD COLUMN grid_rows INTEGER NOT NULL DEFAULT 1")

        # Einmalige Übernahme alter Dashboard-Kacheln (saved_charts.dashboard_
        # position) in die neue, typübergreifende dashboard_pins-Tabelle — nur
        # nötig, wenn dashboard_pins noch komplett leer ist UND es tatsächlich
        # etwas zu übernehmen gibt; ein zweiter Programmstart überschreibt hier
        # nichts mehr (dashboard_pins ist dann nicht mehr leer).
        pins_count = self._conn.execute("SELECT COUNT(*) FROM dashboard_pins").fetchone()[0]
        if pins_count == 0:
            old_pins = self._conn.execute(
                "SELECT id, dashboard_position FROM saved_charts WHERE dashboard_position IS NOT NULL ORDER BY dashboard_position ASC"
            ).fetchall()
            if old_pins:
                self._conn.executemany(
                    "INSERT INTO dashboard_pins (item_type, item_id, position) VALUES ('chart', ?, ?)",
                    [(row["id"], row["dashboard_position"]) for row in old_pins],
                )

    def get_or_create_entity(
        self,
        entity_id: str,
        domain: str,
        state_class: str | None,
        unit: str | None,
        friendly_name: str | None = None,
        on_type_change: Callable[[str, str], None] | None = None,
    ) -> str:
        """Gibt den Aggregationstyp zurück; legt die Entität bei Bedarf neu an.

        Metadaten werden bei jedem Aufruf nachgezogen. Ändert sich der daraus
        abgeleitete Aggregationstyp, muss ``on_type_change`` zuerst die
        persistierten Rollups migrieren; ohne Handler wird der potenziell
        inkonsistente Typwechsel bewusst abgelehnt.
        """
        validate_entity_id(entity_id)
        aggregation_type = derive_type(domain, state_class)
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT aggregation_type, state_class, friendly_name, unit "
                "FROM entities WHERE entity_id = ?",
                (entity_id,),
            ).fetchone()
            if row is not None:
                old_type = row["aggregation_type"]
                if aggregation_type != old_type:
                    if on_type_change is None:
                        raise ValueError(
                            f"Aggregationstyp von {entity_id} änderte sich von "
                            f"{old_type} zu {aggregation_type}; Rollup-Migration erforderlich"
                        )
                    on_type_change(old_type, aggregation_type)
                self._conn.execute(
                    """UPDATE entities
                       SET aggregation_type = ?, state_class = ?,
                           friendly_name = CASE WHEN ? IS NOT NULL AND ? != '' THEN ? ELSE friendly_name END,
                           unit = CASE WHEN ? IS NOT NULL AND ? != '' THEN ? ELSE unit END,
                           updated_at = ?
                       WHERE entity_id = ?""",
                    (
                        aggregation_type,
                        state_class,
                        friendly_name,
                        friendly_name,
                        friendly_name,
                        unit,
                        unit,
                        unit,
                        time.time(),
                        entity_id,
                    ),
                )
                return aggregation_type

            # Globale Standardwerte kommen aus der settings-Tabelle (Einstellungen-
            # Bereich, "Archivierung") statt der Modulkonstante — self.get_setting()
            # kann hier nicht aufgerufen werden, self._lock ist nicht reentrant.
            default_resolution_row = self._conn.execute(
                "SELECT value FROM settings WHERE key = 'default_resolution'"
            ).fetchone()
            default_retention_row = self._conn.execute(
                "SELECT value FROM settings WHERE key = 'default_retention'"
            ).fetchone()
            resolution = default_resolution_row["value"] if default_resolution_row else DEFAULT_RESOLUTION
            retention = default_retention_row["value"] if default_retention_row else DEFAULT_RETENTION
            self._conn.execute(
                """
                INSERT INTO entities
                    (entity_id, aggregation_type, resolution, retention,
                     unit, state_class, friendly_name, row_count, size_bytes, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0, ?)
                """,
                (
                    entity_id,
                    aggregation_type,
                    resolution,
                    retention,
                    unit,
                    state_class,
                    friendly_name,
                    time.time(),
                ),
            )
            return aggregation_type

    def record_write(self, entity_id: str, ts: float, value: float | None = None) -> None:
        """Aktualisiert Datensatzanzahl sowie ersten/letzten Zeitstempel nach einem Schreibvorgang."""
        with self._lock, self._conn:
            self._conn.execute(
                """
                UPDATE entities
                SET row_count = row_count + 1,
                    first_ts = COALESCE(first_ts, ?),
                    last_ts = ?,
                    last_value = COALESCE(?, last_value),
                    updated_at = ?
                WHERE entity_id = ?
                """,
                (ts, ts, value, time.time(), entity_id),
            )

    def claim_ingest_event(self, event_id: str, entity_id: str, ts: float) -> dict:
        """Reserviert eine Event-ID oder liefert ihren bestehenden Zustand."""
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "INSERT OR IGNORE INTO ingested_events "
                "(event_id, entity_id, ts, status, created_at) VALUES (?, ?, ?, 'processing', ?)",
                (event_id, entity_id, ts, time.time()),
            )
            row = self._conn.execute(
                "SELECT entity_id, ts, status, recorded FROM ingested_events WHERE event_id = ?",
                (event_id,),
            ).fetchone()
            return {**dict(row), "is_new": cursor.rowcount == 1}

    def list_processing_ingest_events(self) -> list[dict]:
        """Offene Event-Claims für die Crash-Recovery beim App-Start."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT event_id, entity_id, ts FROM ingested_events "
                "WHERE status = 'processing' ORDER BY created_at"
            ).fetchall()
            return [dict(row) for row in rows]

    def prune_ingested_events(self, completed_before: float) -> int:
        """Begrenzt die Idempotenz-Tabelle auf das relevante Retry-Fenster."""
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "DELETE FROM ingested_events WHERE status = 'done' AND completed_at < ?",
                (completed_before,),
            )
            return cursor.rowcount

    def complete_ingest_event(
        self, event_id: str, entity_id: str, ts: float, *, recorded: bool,
        value: float | None = None,
    ) -> None:
        """Committed Event-Abschluss und Metadaten atomar in SQLite."""
        with self._lock, self._conn:
            state = self._conn.execute(
                "SELECT status FROM ingested_events WHERE event_id = ?", (event_id,)
            ).fetchone()
            if state is None:
                raise ValueError("Unbekannte Event-ID kann nicht abgeschlossen werden")
            if state["status"] == "done":
                return
            if recorded:
                self._conn.execute(
                    """
                    UPDATE entities
                    SET row_count = row_count + 1,
                        first_ts = MIN(COALESCE(first_ts, ?), ?),
                        last_value = CASE
                            WHEN ? IS NOT NULL AND (last_ts IS NULL OR ? >= last_ts) THEN ?
                            ELSE last_value
                        END,
                        last_ts = MAX(COALESCE(last_ts, ?), ?),
                        updated_at = ?
                    WHERE entity_id = ?
                    """,
                    (ts, ts, value, ts, value, ts, ts, time.time(), entity_id),
                )
            self._conn.execute(
                "UPDATE ingested_events SET status = 'done', recorded = ?, completed_at = ? "
                "WHERE event_id = ? AND status = 'processing'",
                (int(recorded), time.time(), event_id),
            )

    def set_first_ts_and_add_rows(self, entity_id: str, first_ts: float, additional_rows: int) -> None:
        """Für den Symcon-Import (Konzept Abschnitt 03): setzt first_ts explizit
        auf einen älteren Wert (statt nur COALESCE wie record_write, das den
        vorhandenen first_ts nie überschreibt) und zählt die importierten
        Datensätze dazu — last_ts bleibt unangetastet, der Import bringt nie
        neuere Werte als der laufende Live-Betrieb."""
        with self._lock, self._conn:
            self._conn.execute(
                """
                UPDATE entities
                SET row_count = row_count + ?, first_ts = ?, updated_at = ?
                WHERE entity_id = ?
                """,
                (additional_rows, first_ts, time.time(), entity_id),
            )

    def add_row_count(self, entity_id: str, additional_rows: int) -> None:
        """Für den Symcon-Import: zählt importierte Datensätze dazu, ohne
        first_ts/last_ts anzufassen (Import lag komplett innerhalb des
        ohnehin schon bekannten Zeitraums)."""
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE entities SET row_count = row_count + ?, updated_at = ? WHERE entity_id = ?",
                (additional_rows, time.time(), entity_id),
            )

    def set_config(
        self,
        entity_id: str,
        resolution: str | None = None,
        retention: str | None = None,
        decimals: str | None = None,
        value_filter: str | None = None,
        gap_threshold: str | None = None,
        outlier_threshold: str | None = None,
        display_mode: str | None = None,
    ) -> None:
        """Ändert Auflösung, Aufbewahrung, Nachkommastellen und/oder die Lücken-/
        Ausreißer-Schwellwerte einer Entität (Konzept Abschnitt 03/04). Alle
        Parameter optional, damit ein Aufrufer auch nur eines davon ändern kann.
        Gültigkeitsprüfung der Werte liegt bewusst beim Aufrufer (main.py) — der
        Index ist hier bewusst dünn und kennt die Anzeige-Labels nicht."""
        updates: list[str] = []
        params: list[str] = []
        if resolution is not None:
            updates.append("resolution = ?")
            params.append(resolution)
        if retention is not None:
            updates.append("retention = ?")
            params.append(retention)
        if decimals is not None:
            updates.append("decimals = ?")
            params.append(decimals)
        if value_filter is not None:
            updates.append("value_filter = ?")
            params.append(value_filter)
        if gap_threshold is not None:
            updates.append("gap_threshold = ?")
            params.append(gap_threshold)
        if outlier_threshold is not None:
            updates.append("outlier_threshold = ?")
            params.append(outlier_threshold)
        if display_mode is not None:
            updates.append("display_mode = ?")
            params.append(display_mode)
        if not updates:
            return
        updates.append("updated_at = ?")
        params.append(time.time())
        params.append(entity_id)
        with self._lock, self._conn:
            self._conn.execute(
                f"UPDATE entities SET {', '.join(updates)} WHERE entity_id = ?", params
            )

    def add_size_bytes(self, entity_id: str, delta: int) -> None:
        """Zählt Bytes auf die Größe der Entität — aufgerufen nach jeder Parquet-Rotation."""
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE entities SET size_bytes = size_bytes + ?, updated_at = ? WHERE entity_id = ?",
                (delta, time.time(), entity_id),
            )

    def replace_entity_storage_stats(self, rows: list[dict]) -> None:
        """Ersetzt abgeleitete Dateikennzahlen für mehrere Entitäten atomar."""
        if not rows:
            return
        now = time.time()
        values = []
        for row in rows:
            entity_id = validate_entity_id(row["entity_id"])
            values.append((
                int(row["actual_row_count"]),
                int(row["actual_size_bytes"]),
                row["actual_first_ts"],
                row["actual_last_ts"],
                now,
                entity_id,
            ))
        with self._lock, self._conn:
            self._conn.executemany(
                """UPDATE entities
                   SET row_count = ?, size_bytes = ?, first_ts = ?, last_ts = ?, updated_at = ?
                   WHERE entity_id = ?""",
                values,
            )

    def set_first_ts(self, entity_id: str, first_ts: float | None) -> None:
        """Setzt first_ts explizit (anders als record_write()'s COALESCE, das einen
        vorhandenen Wert nie überschreibt) — für storage/retention.py: nach dem
        Löschen der ältesten Archiv-Monate zeigt first_ts sonst weiter auf
        längst entfernte Daten."""
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE entities SET first_ts = ?, updated_at = ? WHERE entity_id = ?",
                (first_ts, time.time(), entity_id),
            )

    def bump_ts_bounds(self, entity_id: str, ts: float, value: float | None = None) -> None:
        """Erweitert first_ts/last_ts, falls ts außerhalb des bisher bekannten
        Bereichs liegt — für den Bearbeitungsbereich (nachträglich hinzugefügte
        Werte, Konzept-Erweiterung): anders als ein regulärer Live-Write über
        record_write() (der ausschließlich ans Ende anfügt) kann ein manuell
        eingefügter Wert vor first_ts oder zwischen first_ts und last_ts liegen.
        MIN/MAX hier sind SQLites 2-argumentige Skalarfunktionen (kleinerer/
        größerer der beiden Werte), nicht die 1-spaltigen Aggregatfunktionen."""
        with self._lock, self._conn:
            self._conn.execute(
                """UPDATE entities
                   SET first_ts = MIN(COALESCE(first_ts, ?), ?),
                       last_value = CASE
                           WHEN ? IS NOT NULL AND (last_ts IS NULL OR ? >= last_ts) THEN ?
                           ELSE last_value
                       END,
                       last_ts = MAX(COALESCE(last_ts, ?), ?),
                       updated_at = ?
                   WHERE entity_id = ?""",
                (ts, ts, value, ts, value, ts, ts, time.time(), entity_id),
            )

    def list_entities(
        self,
        search: str | None = None,
        type_filter: str | list[str] | None = None,
        unit_filter: str | None = None,
        sort: str = "entity_id",
        direction: str = "asc",
        favorites_only: bool = False,
    ) -> list[sqlite3.Row]:
        """search filtert per LIKE auf entity_id/friendly_name, type_filter auf
        aggregation_type — ein einzelner Typ (Rückwärtskompatibilität), eine Liste
        von Typen (Mehrfachauswahl, ODER-verknüpft) oder "all"/None/leere Liste für
        "kein Filter". unit_filter filtert exakt auf unit — der Sentinel "__none__"
        steht für Entitäten ohne Einheit (unit IS NULL), None/"all" für "kein Filter".
        sort kommt aus SORTABLE_COLUMNS (Allowlist statt direkter Interpolation —
        Spaltennamen lassen sich in SQLite nicht parametrisieren). Anders als bei
        Charts/Tabellen stehen Favoriten hier NICHT automatisch zuerst — die Liste
        bleibt beim gewählten sort (i. d. R. alphabetisch), favorites_only blendet
        stattdessen den Rest ganz aus (eigene Ansicht statt Umsortierung)."""
        column = SORTABLE_COLUMNS.get(sort, "entity_id")
        direction_sql = "DESC" if direction == "desc" else "ASC"

        if isinstance(type_filter, str):
            type_filter = [type_filter]
        types = [t for t in (type_filter or []) if t and t != "all"]

        query = (
            "SELECT entities.*, "
            "(SELECT COUNT(*) FROM deleted_points d WHERE d.entity_id = entities.entity_id) AS deleted_count "
            "FROM entities"
        )
        conditions: list[str] = []
        params: list[str] = []
        if search:
            conditions.append("(entity_id LIKE ? OR friendly_name LIKE ?)")
            like = f"%{search}%"
            params += [like, like]
        if types:
            placeholders = ", ".join("?" for _ in types)
            conditions.append(f"aggregation_type IN ({placeholders})")
            params += types
        if unit_filter and unit_filter != "all":
            if unit_filter == "__none__":
                conditions.append("unit IS NULL")
            else:
                conditions.append("unit = ?")
                params.append(unit_filter)
        if favorites_only:
            conditions.append("is_favorite = 1")
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += f" ORDER BY {column} {direction_sql}, entity_id ASC"

        with self._lock, self._conn:
            return self._conn.execute(query, params).fetchall()

    def set_entity_favorite(self, entity_id: str, favorite: bool) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE entities SET is_favorite = ?, updated_at = ? WHERE entity_id = ?",
                (int(favorite), time.time(), entity_id),
            )

    def list_distinct_units(self) -> list[str | None]:
        """Alle tatsächlich vorkommenden Einheiten (inkl. None für "ohne Einheit"),
        sortiert — Grundlage für den Einheit-Filter im CSV-Export (Konzept
        Abschnitt 09)."""
        with self._lock, self._conn:
            rows = self._conn.execute("SELECT DISTINCT unit FROM entities ORDER BY unit ASC").fetchall()
        return [row["unit"] for row in rows]

    def get_setting(self, key: str, default: str | None = None) -> str | None:
        with self._lock, self._conn:
            row = self._conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
            return row["value"] if row else default

    def set_setting(self, key: str, value: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    def create_backup_job(self, trigger: str, scheduled_for: float | None = None) -> int:
        now = time.time()
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT INTO backup_jobs (trigger, scheduled_for, status, created_at) "
                "VALUES (?, ?, 'queued', ?)",
                (trigger, scheduled_for, now),
            )
            return int(cur.lastrowid)

    def update_backup_job(self, job_id: int, **values) -> None:
        allowed = {"started_at", "finished_at", "status", "filename", "size_bytes", "error"}
        updates = {key: value for key, value in values.items() if key in allowed}
        if not updates:
            return
        assignments = ", ".join(f"{key} = ?" for key in updates)
        with self._lock, self._conn:
            self._conn.execute(
                f"UPDATE backup_jobs SET {assignments} WHERE id = ?",
                [*updates.values(), job_id],
            )

    def list_backup_jobs(self, limit: int = 20) -> list[sqlite3.Row]:
        safe_limit = max(1, min(int(limit), 100))
        with self._lock, self._conn:
            return self._conn.execute(
                "SELECT * FROM backup_jobs ORDER BY created_at DESC, id DESC LIMIT ?",
                (safe_limit,),
            ).fetchall()

    def recover_interrupted_backup_jobs(self, now: float | None = None) -> int:
        finished_at = time.time() if now is None else now
        with self._lock, self._conn:
            cur = self._conn.execute(
                "UPDATE backup_jobs SET status = 'interrupted', finished_at = ?, "
                "error = COALESCE(error, 'App wurde während der Sicherung beendet') "
                "WHERE status IN ('queued', 'running')",
                (finished_at,),
            )
            return cur.rowcount

    def create_retention_job(self, trigger: str, scheduled_for: float | None = None) -> int:
        now = time.time()
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT INTO retention_jobs (trigger, scheduled_for, status, created_at) "
                "VALUES (?, ?, 'queued', ?)",
                (trigger, scheduled_for, now),
            )
            return int(cur.lastrowid)

    def update_retention_job(self, job_id: int, **values) -> None:
        allowed = {
            "started_at", "finished_at", "status", "rows_deleted", "bytes_freed",
            "months_deleted", "entities_affected", "error",
        }
        updates = {key: value for key, value in values.items() if key in allowed}
        if not updates:
            return
        assignments = ", ".join(f"{key} = ?" for key in updates)
        with self._lock, self._conn:
            self._conn.execute(
                f"UPDATE retention_jobs SET {assignments} WHERE id = ?",
                [*updates.values(), job_id],
            )

    def list_retention_jobs(self, limit: int = 10) -> list[sqlite3.Row]:
        safe_limit = max(1, min(int(limit), 100))
        with self._lock, self._conn:
            return self._conn.execute(
                "SELECT * FROM retention_jobs ORDER BY created_at DESC, id DESC LIMIT ?",
                (safe_limit,),
            ).fetchall()

    def get_retention_job_totals(self, since_ts: float = 0.0) -> dict:
        """Aggregiert erfolgreiche, endgültige Retention-Löschungen."""
        with self._lock, self._conn:
            row = self._conn.execute(
                """
                SELECT COUNT(*) AS job_count,
                       COALESCE(SUM(rows_deleted), 0) AS rows_deleted,
                       COALESCE(SUM(bytes_freed), 0) AS bytes_freed,
                       COALESCE(SUM(months_deleted), 0) AS months_deleted,
                       COALESCE(SUM(entities_affected), 0) AS entities_affected
                FROM retention_jobs
                WHERE status = 'success'
                  AND COALESCE(finished_at, created_at) >= ?
                """,
                (since_ts,),
            ).fetchone()
            return dict(row)

    def recover_interrupted_retention_jobs(self, now: float | None = None) -> int:
        finished_at = time.time() if now is None else now
        with self._lock, self._conn:
            cur = self._conn.execute(
                "UPDATE retention_jobs SET status = 'interrupted', finished_at = ?, "
                "error = COALESCE(error, 'App wurde während der Retention beendet') "
                "WHERE status IN ('queued', 'running')",
                (finished_at,),
            )
            return cur.rowcount

    def create_saved_chart(
        self,
        name: str,
        entity_ids: list[str],
        range_key: str,
        continuous: bool,
        entity_names: dict[str, str] | None = None,
        resolution_preset: str = "auto",
        dynamic_y_axis: bool = True,
        dashboard_animation: bool = True,
        chart_stats: bool = True,
    ) -> int:
        now = time.time()
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT INTO saved_charts "
                "(name, entity_ids, range_key, continuous, entity_names, resolution_preset, "
                "dynamic_y_axis, dashboard_animation, chart_stats, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    name, json.dumps(entity_ids), range_key, int(continuous),
                    json.dumps(entity_names or {}), resolution_preset,
                    int(dynamic_y_axis), int(dashboard_animation), int(chart_stats), now, now,
                ),
            )
            return cur.lastrowid

    def update_saved_chart(
        self,
        chart_id: int,
        name: str,
        entity_ids: list[str],
        range_key: str,
        continuous: bool,
        entity_names: dict[str, str] | None = None,
        resolution_preset: str = "auto",
        dynamic_y_axis: bool = True,
        dashboard_animation: bool = True,
        chart_stats: bool = True,
    ) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE saved_charts SET name = ?, entity_ids = ?, range_key = ?, continuous = ?, "
                "entity_names = ?, resolution_preset = ?, dynamic_y_axis = ?, dashboard_animation = ?, "
                "chart_stats = ?, updated_at = ? WHERE id = ?",
                (
                    name, json.dumps(entity_ids), range_key, int(continuous),
                    json.dumps(entity_names or {}), resolution_preset,
                    int(dynamic_y_axis), int(dashboard_animation), int(chart_stats), time.time(), chart_id,
                ),
            )

    def _row_to_saved_chart(self, row: sqlite3.Row) -> dict:
        d = dict(row)
        d["entity_ids"] = json.loads(d["entity_ids"])
        d["continuous"] = bool(d["continuous"])
        d["dynamic_y_axis"] = bool(d.get("dynamic_y_axis", 1))
        d["dashboard_animation"] = bool(d.get("dashboard_animation", 1))
        d["chart_stats"] = bool(d.get("chart_stats", 1))
        d["entity_names"] = json.loads(d["entity_names"]) if d.get("entity_names") else {}
        d["is_favorite"] = bool(d["is_favorite"])
        return d

    def list_saved_charts(self) -> list[dict]:
        # Favoriten zuerst, sonst neueste zuerst — dieselbe Konvention wie
        # list_entities() (is_favorite DESC vor dem eigentlichen Sortierkriterium).
        with self._lock, self._conn:
            rows = self._conn.execute("SELECT * FROM saved_charts ORDER BY is_favorite DESC, created_at DESC").fetchall()
            return [self._row_to_saved_chart(row) for row in rows]

    def count_saved_charts(self) -> int:
        with self._lock, self._conn:
            return self._conn.execute("SELECT COUNT(*) FROM saved_charts").fetchone()[0]

    def get_saved_chart(self, chart_id: int) -> dict | None:
        with self._lock, self._conn:
            row = self._conn.execute("SELECT * FROM saved_charts WHERE id = ?", (chart_id,)).fetchone()
            return self._row_to_saved_chart(row) if row else None

    def set_chart_favorite(self, chart_id: int, favorite: bool) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE saved_charts SET is_favorite = ?, updated_at = ? WHERE id = ?",
                (int(favorite), time.time(), chart_id),
            )

    def delete_saved_chart(self, chart_id: int) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM saved_charts WHERE id = ?", (chart_id,))
            self._conn.execute("DELETE FROM dashboard_pins WHERE item_type = 'chart' AND item_id = ?", (chart_id,))

    # -- Dashboard-Kacheln (Konzept "Offene Punkte", typübergreifend: Charts
    # UND Vergleichstabellen teilen sich dieselbe dashboard_pins-Tabelle und
    # damit denselben Kachel-Grenzwert, siehe DASHBOARD_TILE_LIMIT). ----------

    DASHBOARD_TILE_LIMIT = 18

    def list_dashboard_pins(self) -> list[dict]:
        """Angeheftete Kacheln in Reihenfolge — item_type ist 'chart' oder
        'table', der Aufrufer (main.py _dashboard_tiles_context()) löst jeden
        Eintrag dann gegen die passende Tabelle auf. Liefert auch verwaiste
        Einträge zurück (sollte durch die Bereinigung in delete_saved_chart()/
        delete_saved_table() praktisch nie vorkommen) — das Herausfiltern
        macht main.py, da nur dort bekannt ist, wie ein Chart von einer
        Tabelle unterschieden und geladen wird."""
        with self._lock, self._conn:
            rows = self._conn.execute("SELECT * FROM dashboard_pins ORDER BY position ASC").fetchall()
            return [dict(row) for row in rows]

    def count_dashboard_pins(self) -> int:
        with self._lock, self._conn:
            return self._conn.execute("SELECT COUNT(*) FROM dashboard_pins").fetchone()[0]

    def is_pinned(self, item_type: str, item_id: int) -> bool:
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT 1 FROM dashboard_pins WHERE item_type = ? AND item_id = ?", (item_type, item_id)
            ).fetchone()
            return row is not None

    def pin_item_to_dashboard(self, item_type: str, item_id: int) -> bool:
        """Heftet ein Chart oder eine Vergleichstabelle als neue letzte Kachel
        an — False, wenn das Limit von 18 gleichzeitigen Kacheln (Konzept
        "Offene Punkte": Performance, viele ECharts-Instanzen/Tabellen auf der
        meistbesuchten Seite) schon erreicht ist, dann bleibt alles
        unverändert. UNIQUE(item_type, item_id) verhindert nebenbei ein
        doppeltes Anheften desselben Objekts."""
        with self._lock, self._conn:
            count = self._conn.execute("SELECT COUNT(*) FROM dashboard_pins").fetchone()[0]
            if count >= self.DASHBOARD_TILE_LIMIT:
                return False
            if self._conn.execute(
                "SELECT 1 FROM dashboard_pins WHERE item_type = ? AND item_id = ?", (item_type, item_id)
            ).fetchone():
                return True  # schon angeheftet — kein Fehler, einfach nichts weiter tun
            max_pos = self._conn.execute("SELECT MAX(position) FROM dashboard_pins").fetchone()[0]
            self._conn.execute(
                "INSERT INTO dashboard_pins (item_type, item_id, position) VALUES (?, ?, ?)",
                (item_type, item_id, (max_pos or 0) + 1),
            )
            return True

    def set_dashboard_pin_size(self, item_type: str, item_id: int, grid_cols: int, grid_rows: int) -> bool:
        """Speichert die Rastergröße einer angehefteten Dashboard-Kachel.

        Die Validierung lebt zusätzlich zur API auch hier, damit kein anderer
        Aufrufer ungültige CSS-Grid-Spannen persistieren kann. False bedeutet:
        Die angegebene Kachel ist nicht (mehr) angeheftet.
        """
        if item_type not in {"chart", "table"}:
            raise ValueError("Ungültiger Dashboard-Kacheltyp")
        if not 1 <= int(grid_cols) <= 3 or not 1 <= int(grid_rows) <= 3:
            raise ValueError("Dashboard-Kachelgröße muss zwischen 1 und 3 liegen")
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "UPDATE dashboard_pins SET grid_cols = ?, grid_rows = ? "
                "WHERE item_type = ? AND item_id = ?",
                (int(grid_cols), int(grid_rows), item_type, item_id),
            )
            return cursor.rowcount > 0

    def unpin_item_from_dashboard(self, item_type: str, item_id: int) -> None:
        # Absichtlich keine Neu-Nummerierung der verbleibenden Kacheln — die
        # Reihenfolge über position bleibt stabil, eine Lücke stört dabei
        # nicht (list_dashboard_pins() sortiert nur nach dem Wert, nicht nach
        # Lückenlosigkeit).
        with self._lock, self._conn:
            self._conn.execute(
                "DELETE FROM dashboard_pins WHERE item_type = ? AND item_id = ?", (item_type, item_id)
            )

    def reorder_dashboard_pins(self, pins: list[tuple[str, int]]) -> None:
        """Setzt position neu, komplett durchnummeriert nach der übergebenen
        Reihenfolge (Drag&Drop auf der Übersichtsseite, funktioniert über
        Charts UND Tabellen hinweg gemischt). pins ist eine Liste aus
        (item_type, item_id) — der UPDATE trifft nur tatsächlich vorhandene
        Pins, ein veralteter/manipulierter Eintrag fügt nie einen neuen
        hinzu (das bleibt pin_item_to_dashboard() vorbehalten)."""
        with self._lock, self._conn:
            self._conn.executemany(
                "UPDATE dashboard_pins SET position = ? WHERE item_type = ? AND item_id = ?",
                [(position, item_type, item_id) for position, (item_type, item_id) in enumerate(pins, start=1)],
            )

    # -- Vergleichstabellen (Konzept "Offene Punkte") --------------------------

    def _write_table_columns(self, table_id: int, columns: list[dict]) -> None:
        # Kein eigenes with self._lock hier — wird ausschließlich aus
        # create_saved_table()/update_saved_table() heraus aufgerufen, die
        # den Lock bereits halten (threading.Lock ist nicht reentrant, ein
        # zweites with self._lock hier würde deadlocken).
        self._conn.executemany(
            "INSERT INTO table_columns (table_id, position, label, range_key, offset, year_over_year) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [
                (table_id, i, c["label"], c["range_key"], c["offset"], int(c["year_over_year"]))
                for i, c in enumerate(columns)
            ],
        )

    def _write_table_rows(self, table_id: int, rows: list[dict]) -> None:
        self._conn.executemany(
            "INSERT INTO table_rows (table_id, position, label, row_type, entity_ids, formula, formula_unit, bold) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    table_id, i, r["label"], r["row_type"], json.dumps(r["entity_ids"]),
                    r["formula"], r.get("formula_unit", ""), int(r["bold"]),
                )
                for i, r in enumerate(rows)
            ],
        )

    def create_saved_table(self, name: str, columns: list[dict], rows: list[dict], style: dict | None = None) -> int:
        now = time.time()
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT INTO saved_tables (name, style_json, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (name, json.dumps(style or {}), now, now),
            )
            table_id = cur.lastrowid
            self._write_table_columns(table_id, columns)
            self._write_table_rows(table_id, rows)
            return table_id

    def update_saved_table(
        self, table_id: int, name: str, columns: list[dict], rows: list[dict], style: dict | None = None
    ) -> None:
        # Spalten/Zeilen komplett ersetzen statt einzeln zu diffen — dieselbe
        # Konvention wie update_saved_chart() (eine gespeicherte Tabelle ist
        # eine Abfrage-Definition, jedes Speichern schreibt den kompletten,
        # aktuellen Bearbeitungsstand fest).
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE saved_tables SET name = ?, style_json = ?, updated_at = ? WHERE id = ?",
                (name, json.dumps(style or {}), time.time(), table_id),
            )
            self._conn.execute("DELETE FROM table_columns WHERE table_id = ?", (table_id,))
            self._conn.execute("DELETE FROM table_rows WHERE table_id = ?", (table_id,))
            self._write_table_columns(table_id, columns)
            self._write_table_rows(table_id, rows)

    def list_saved_tables(self) -> list[dict]:
        with self._lock, self._conn:
            rows = self._conn.execute(
                """SELECT t.*,
                          (SELECT COUNT(*) FROM table_columns c WHERE c.table_id = t.id) AS column_count,
                          (SELECT COUNT(*) FROM table_rows r WHERE r.table_id = t.id) AS row_count
                   FROM saved_tables t ORDER BY t.is_favorite DESC, t.created_at DESC"""
            ).fetchall()
            return [dict(row) for row in rows]

    def count_saved_tables(self) -> int:
        with self._lock, self._conn:
            return self._conn.execute("SELECT COUNT(*) FROM saved_tables").fetchone()[0]

    def set_table_favorite(self, table_id: int, favorite: bool) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE saved_tables SET is_favorite = ?, updated_at = ? WHERE id = ?",
                (int(favorite), time.time(), table_id),
            )

    def get_saved_table(self, table_id: int) -> dict | None:
        with self._lock, self._conn:
            t = self._conn.execute("SELECT * FROM saved_tables WHERE id = ?", (table_id,)).fetchone()
            if t is None:
                return None
            column_rows = self._conn.execute(
                "SELECT * FROM table_columns WHERE table_id = ? ORDER BY position ASC", (table_id,)
            ).fetchall()
            row_rows = self._conn.execute(
                "SELECT * FROM table_rows WHERE table_id = ? ORDER BY position ASC", (table_id,)
            ).fetchall()
            result = dict(t)
            result["is_favorite"] = bool(result["is_favorite"])
            result["style"] = json.loads(result["style_json"]) if result.get("style_json") else {}
            result["columns"] = [
                {
                    "label": c["label"], "range_key": c["range_key"], "offset": c["offset"],
                    "year_over_year": bool(c["year_over_year"]),
                }
                for c in column_rows
            ]
            result["rows"] = [
                {
                    "label": r["label"], "row_type": r["row_type"],
                    "entity_ids": json.loads(r["entity_ids"]), "formula": r["formula"],
                    "formula_unit": r["formula_unit"], "bold": bool(r["bold"]),
                }
                for r in row_rows
            ]
            return result

    def delete_saved_table(self, table_id: int) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM table_columns WHERE table_id = ?", (table_id,))
            self._conn.execute("DELETE FROM table_rows WHERE table_id = ?", (table_id,))
            self._conn.execute("DELETE FROM saved_tables WHERE id = ?", (table_id,))
            self._conn.execute("DELETE FROM dashboard_pins WHERE item_type = 'table' AND item_id = ?", (table_id,))

    def get_overview(self) -> dict:
        with self._lock, self._conn:
            row = self._conn.execute(
                """
                SELECT
                    COUNT(*) AS entity_count,
                    COALESCE(SUM(MAX(row_count - (SELECT COUNT(*) FROM deleted_points d WHERE d.entity_id = entities.entity_id), 0)), 0) AS total_rows,
                    COALESCE(SUM(size_bytes), 0) AS total_size_bytes
                FROM entities
                """
            ).fetchone()
            return dict(row)

    def get_last_write_ts(self) -> float | None:
        """Zeitpunkt des zuletzt AKZEPTIERTEN Werts über alle Entitäten hinweg
        (MAX(last_ts), von record_write() gepflegt) — für die Einstellungen,
        Bereich "Verbindung": lässt erkennen, ob überhaupt noch aktuell Daten
        ankommen, unabhängig davon, welche einzelne Entität zuletzt gesendet hat."""
        with self._lock, self._conn:
            row = self._conn.execute("SELECT MAX(last_ts) AS last_ts FROM entities").fetchone()
            return row["last_ts"] if row and row["last_ts"] is not None else None

    def record_stats_snapshot_if_stale(self, min_interval_seconds: float = 3600) -> bool:
        """Schreibt einen neuen Übersichts-Schnappschuss, aber nur wenn der
        letzte mindestens min_interval_seconds zurückliegt (Konzept Abschnitt
        03). Gibt zurück, ob tatsächlich ein Punkt geschrieben wurde. Fragt die
        Summen direkt ab statt über get_overview() zu gehen, weil self._lock
        nicht reentrant ist."""
        now = time.time()
        with self._lock, self._conn:
            latest_row = self._conn.execute("SELECT MAX(ts) AS latest FROM stats_snapshots").fetchone()
            latest = latest_row["latest"] if latest_row else None
            if latest is not None and now - latest < min_interval_seconds:
                return False
            overview = self._conn.execute(
                """
                SELECT
                    COUNT(*) AS entity_count,
                    COALESCE(SUM(MAX(row_count - (SELECT COUNT(*) FROM deleted_points d WHERE d.entity_id = entities.entity_id), 0)), 0) AS total_rows,
                    COALESCE(SUM(size_bytes), 0) AS total_size_bytes
                FROM entities
                """
            ).fetchone()
            self._conn.execute(
                "INSERT INTO stats_snapshots (ts, entity_count, total_rows, total_size_bytes) VALUES (?, ?, ?, ?)",
                (now, overview["entity_count"], overview["total_rows"], overview["total_size_bytes"]),
            )
            return True

    def get_stats_snapshots(self, since_ts: float) -> list[dict]:
        with self._lock, self._conn:
            rows = self._conn.execute(
                "SELECT ts, entity_count, total_rows, total_size_bytes FROM stats_snapshots "
                "WHERE ts >= ? ORDER BY ts",
                (since_ts,),
            ).fetchall()
            return [dict(row) for row in rows]

    def get_stats_by_type(self) -> list[dict]:
        with self._lock, self._conn:
            rows = self._conn.execute(
                """
                SELECT aggregation_type, COUNT(*) AS entity_count,
                       COALESCE(SUM(MAX(row_count - (SELECT COUNT(*) FROM deleted_points d WHERE d.entity_id = entities.entity_id), 0)), 0) AS total_rows,
                       COALESCE(SUM(size_bytes), 0) AS total_size_bytes
                FROM entities GROUP BY aggregation_type ORDER BY total_size_bytes DESC
                """
            ).fetchall()
            return [dict(row) for row in rows]

    def get_stats_by_resolution(self) -> list[dict]:
        with self._lock, self._conn:
            rows = self._conn.execute(
                """
                SELECT resolution, COUNT(*) AS entity_count,
                       COALESCE(SUM(MAX(row_count - (SELECT COUNT(*) FROM deleted_points d WHERE d.entity_id = entities.entity_id), 0)), 0) AS total_rows,
                       COALESCE(SUM(size_bytes), 0) AS total_size_bytes
                FROM entities GROUP BY resolution ORDER BY total_size_bytes DESC
                """
            ).fetchall()
            return [dict(row) for row in rows]

    def get_stats_by_retention(self) -> list[dict]:
        with self._lock, self._conn:
            rows = self._conn.execute(
                """
                SELECT retention, COUNT(*) AS entity_count,
                       COALESCE(SUM(MAX(row_count - (SELECT COUNT(*) FROM deleted_points d WHERE d.entity_id = entities.entity_id), 0)), 0) AS total_rows,
                       COALESCE(SUM(size_bytes), 0) AS total_size_bytes
                FROM entities GROUP BY retention ORDER BY total_size_bytes DESC
                """
            ).fetchall()
            return [dict(row) for row in rows]

    def get_entity(self, entity_id: str) -> sqlite3.Row | None:
        with self._lock, self._conn:
            return self._conn.execute(
                "SELECT entities.*, "
                "(SELECT COUNT(*) FROM deleted_points d WHERE d.entity_id = entities.entity_id) AS deleted_count "
                "FROM entities WHERE entity_id = ?",
                (entity_id,),
            ).fetchone()

    def clear_entity_data(self, entity_id: str) -> None:
        """Setzt eine Entität auf leer zurück, behält aber ihre Konfiguration."""
        validate_entity_id(entity_id)
        with self._lock, self._conn:
            self._conn.execute(
                "DELETE FROM deleted_points WHERE entity_id = ?", (entity_id,)
            )
            self._conn.execute(
                "DELETE FROM ingested_events WHERE entity_id = ?", (entity_id,)
            )
            self._conn.execute(
                """UPDATE entities
                   SET first_ts = NULL, last_ts = NULL, last_value = NULL, row_count = 0,
                       size_bytes = 0, updated_at = ?
                   WHERE entity_id = ?""",
                (time.time(), entity_id),
            )

    def delete_entity(self, entity_id: str) -> None:
        """Entfernt die Entität und alle direkt zugehörigen Indexdaten."""
        validate_entity_id(entity_id)
        with self._lock, self._conn:
            self._conn.execute(
                "DELETE FROM deleted_points WHERE entity_id = ?", (entity_id,)
            )
            self._conn.execute(
                "DELETE FROM ingested_events WHERE entity_id = ?", (entity_id,)
            )
            self._conn.execute(
                "DELETE FROM entities WHERE entity_id = ?", (entity_id,)
            )

    def mark_deleted(
        self, entity_id: str, timestamps: list[float], *, deleted_at: float | None = None
    ) -> None:
        """Markiert jedes Vorkommen in timestamps einzeln als gelöscht — kommt ein
        Zeitstempel darin mehrfach vor (z. B. weil zwei Duplikat-Zeilen mit
        demselben Zeitstempel einzeln ausgewählt wurden), wird entsprechend
        mehrfach vermerkt. get_deleted_counts() liest das als Anzahl zurück, statt
        pauschal "dieser Zeitstempel ist komplett gelöscht" zu markieren."""
        now = time.time() if deleted_at is None else deleted_at
        with self._lock, self._conn:
            self._conn.executemany(
                "INSERT INTO deleted_points (entity_id, ts, deleted_at) VALUES (?, ?, ?)",
                [(entity_id, ts, now) for ts in timestamps],
            )

    def undo_last_deleted_batch(self, entity_id: str) -> int:
        """Macht die zuletzt gelöschte Charge (gleicher deleted_at-Zeitstempel) rückgängig."""
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT MAX(deleted_at) AS latest FROM deleted_points WHERE entity_id = ?",
                (entity_id,),
            ).fetchone()
            latest = row["latest"] if row else None
            if latest is None:
                return 0
            cursor = self._conn.execute(
                "DELETE FROM deleted_points WHERE entity_id = ? AND deleted_at = ?",
                (entity_id, latest),
            )
            return cursor.rowcount

    def get_last_deleted_batch(self, entity_id: str) -> list[float]:
        """Zeitstempel der zuletzt gelöschten Charge (gleicher deleted_at-Wert),
        OHNE etwas zu ändern — für die "Rückgängig"-Vorschau (zeigt, was der
        Undo-Button wiederherstellen würde) und um den Button nur zu aktivieren,
        wenn es überhaupt etwas rückgängig zu machen gibt."""
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT MAX(deleted_at) AS latest FROM deleted_points WHERE entity_id = ?",
                (entity_id,),
            ).fetchone()
            latest = row["latest"] if row else None
            if latest is None:
                return []
            rows = self._conn.execute(
                "SELECT ts FROM deleted_points WHERE entity_id = ? AND deleted_at = ?",
                (entity_id, latest),
            ).fetchall()
            return [r["ts"] for r in rows]

    def get_deleted_counts(self, entity_id: str, start_ts: float, end_ts: float) -> dict[float, int]:
        """Wie viele Vorkommen je Zeitstempel als gelöscht markiert sind — bei
        einem normalen (nicht doppelten) Zeitstempel ist das 0 oder 1, bei einem
        Duplikat kann es 1 sein (nur eines der beiden Vorkommen gelöscht) oder 2
        (beide). filter_deleted_occurrences() nutzt das, um gezielt nur so viele
        Zeilen wie markiert auszufiltern, nicht automatisch alle."""
        with self._lock, self._conn:
            rows = self._conn.execute(
                "SELECT ts, COUNT(*) AS n FROM deleted_points WHERE entity_id = ? AND ts >= ? AND ts < ? GROUP BY ts",
                (entity_id, start_ts, end_ts),
            ).fetchall()
            return {row["ts"]: row["n"] for row in rows}

    def get_deleted_points_count(self) -> int:
        """Gesamtzahl weich gelöschter Vorkommen über alle Entitäten — für die
        Speicherplatz-Einstellung (Konzept, "Offene Punkte": kein Purge-Job)."""
        with self._lock, self._conn:
            row = self._conn.execute("SELECT COUNT(*) AS n FROM deleted_points").fetchone()
            return row["n"]

    def get_deleted_points_by_entity(self) -> list[dict]:
        """Aufschlüsselung der zur Löschung markierten Vorkommen je Entität
        (nur Entitäten mit mindestens einem markierten Vorkommen) — für die
        Statistik-Übersicht, damit sichtbar wird WELCHE Entitäten betroffen
        sind, nicht nur die archiv-weite Summe (siehe get_deleted_points_count)."""
        with self._lock, self._conn:
            rows = self._conn.execute(
                """
                SELECT d.entity_id AS entity_id, e.friendly_name AS friendly_name, COUNT(*) AS n
                FROM deleted_points d
                LEFT JOIN entities e ON e.entity_id = d.entity_id
                GROUP BY d.entity_id
                ORDER BY n DESC, d.entity_id ASC
                """
            ).fetchall()
            return [dict(row) for row in rows]

    def list_deleted_points(
        self, *, search: str = "", page: int = 1, page_size: int = 50
    ) -> dict:
        """Listet einzelne Soft-Delete-Markierungen serverseitig paginiert.

        Die UI lädt diese Detailansicht bewusst erst auf Anforderung. Dadurch
        bleibt die Einstellungsseite auch bei sehr vielen Markierungen klein
        und es werden nie sämtliche Zeilen in den Arbeitsspeicher geladen.
        """
        search = search.strip().lower()
        pattern = f"%{search}%"
        where = """
            WHERE (? = '' OR lower(d.entity_id) LIKE ?
                   OR lower(COALESCE(e.friendly_name, '')) LIKE ?)
        """
        page_size = max(10, min(int(page_size), 200))
        with self._lock, self._conn:
            total = self._conn.execute(
                f"""
                SELECT COUNT(*) AS n
                FROM deleted_points d
                LEFT JOIN entities e ON e.entity_id = d.entity_id
                {where}
                """,
                (search, pattern, pattern),
            ).fetchone()["n"]
            total_pages = max(1, -(-total // page_size))
            page = max(1, min(int(page), total_pages))
            offset = (page - 1) * page_size
            rows = self._conn.execute(
                f"""
                SELECT d.id, d.entity_id, d.ts, d.deleted_at,
                       e.friendly_name, e.unit
                FROM deleted_points d
                LEFT JOIN entities e ON e.entity_id = d.entity_id
                {where}
                ORDER BY d.deleted_at DESC, d.id DESC
                LIMIT ? OFFSET ?
                """,
                (search, pattern, pattern, page_size, offset),
            ).fetchall()
        return {
            "rows": [dict(row) for row in rows],
            "pagination": {
                "page": page,
                "page_size": page_size,
                "total": total,
                "total_pages": total_pages,
                "start": offset + 1 if total else 0,
                "end": min(offset + page_size, total),
            },
        }

    def get_deleted_counts_for_entity(self, entity_id: str) -> dict[float, int]:
        """Wie get_deleted_counts(), aber ohne Zeitfenster — für den Purge, der
        alle gelöschten Vorkommen einer Entität sehen muss, nicht nur die in
        einem bestimmten Anzeige-Zeitraum."""
        with self._lock, self._conn:
            rows = self._conn.execute(
                "SELECT ts, COUNT(*) AS n FROM deleted_points WHERE entity_id = ? GROUP BY ts",
                (entity_id,),
            ).fetchall()
            return {row["ts"]: row["n"] for row in rows}

    def remove_deleted_points(self, entity_id: str, timestamps: list[float]) -> None:
        """Entfernt je einen deleted_points-Eintrag pro Zeitstempel in timestamps
        (mehrfaches Vorkommen in der Liste entfernt entsprechend mehrere Einträge)
        — aufgerufen NACHDEM diese Vorkommen tatsächlich physisch aus dem Hot
        Buffer entfernt wurden (purge_hot_buffer() in cleanup.py), sie brauchen
        dann keine Soft-Delete-Filterung mehr."""
        with self._lock, self._conn:
            for ts in timestamps:
                row = self._conn.execute(
                    "SELECT id FROM deleted_points WHERE entity_id = ? AND ts = ? LIMIT 1",
                    (entity_id, ts),
                ).fetchone()
                if row:
                    self._conn.execute("DELETE FROM deleted_points WHERE id = ?", (row["id"],))

    def close(self) -> None:
        with self._lock:
            self._conn.close()
