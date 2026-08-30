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
_DISPLAY_NAME_EXPR = "COALESCE(entities.friendly_name, entities.entity_id) COLLATE NOCASE"
SORTABLE_COLUMNS = {
    "entity_id": _DISPLAY_NAME_EXPR,
    "friendly_name": _DISPLAY_NAME_EXPR,
    "type": "aggregation_type",
    "resolution": "resolution",
    "retention": "retention",
    "unit": "unit",
    "rows": "MAX(row_count - COALESCE(dc.deleted_count, 0), 0)",
    "first_ts": "first_ts",
    "last_ts": "last_ts",
    "size": "size_bytes",
}

# Anzahl gelöschter Vorkommen je Entität, einmalig aggregiert statt als
# korrelierte Subquery pro Zeile (ZP-003 in PERFORMANCE.md): eine korrelierte
# Subquery wertet SQLite pro äußerer Zeile separat aus (O(Entities × Deletes));
# dieser LEFT JOIN aggregiert deleted_points genau einmal.
_DELETED_COUNT_JOIN = (
    "LEFT JOIN ("
    "SELECT entity_id, COUNT(*) AS deleted_count FROM deleted_points GROUP BY entity_id"
    ") dc ON dc.entity_id = entities.entity_id"
)

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
    chart_options TEXT NOT NULL DEFAULT '{}',
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

-- RAM-Verbrauch dieses Addon-Containers, stündlich über die Supervisor-API
-- abgefragt (Konzept "Über Zeitarchiv": RAM-Anzeige). Eigene Tabelle statt
-- Erweiterung von stats_snapshots, weil die Quelle eine andere ist (externer
-- Supervisor-Aufruf statt eigener Index-Aggregation) und optional bleibt —
-- ohne Supervisor (z. B. lokale Entwicklung) bleibt sie einfach leer, ohne
-- stats_snapshots zu beeinträchtigen.
CREATE TABLE IF NOT EXISTS memory_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    memory_usage_bytes INTEGER NOT NULL
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
    legend_metrics TEXT NOT NULL DEFAULT '["sum"]',
    legend_style TEXT NOT NULL DEFAULT 'chips',
    chart_type TEXT NOT NULL DEFAULT 'auto',
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
CREATE TABLE IF NOT EXISTS dashboards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    position INTEGER NOT NULL,
    is_default INTEGER NOT NULL DEFAULT 0,
    locked INTEGER NOT NULL DEFAULT 0,
    is_favorite INTEGER NOT NULL DEFAULT 0,
    precise_mode INTEGER NOT NULL DEFAULT 0,
    fill_gaps INTEGER NOT NULL DEFAULT 0
);

-- dashboard_id verweist auf dashboards.id — mehrere, unabhängige Dashboards
-- (Konzept "Dashboards"-Menüpunkt neben der festen Übersichtsseite), jedes
-- mit eigener Kachel-Reihenfolge/-Größe. UNIQUE erlaubt dasselbe Chart/dieselbe
-- Tabelle bewusst auf mehreren Dashboards gleichzeitig angeheftet.
CREATE TABLE IF NOT EXISTS dashboard_pins (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    dashboard_id INTEGER NOT NULL DEFAULT 1,
    item_type TEXT NOT NULL,
    item_id INTEGER NOT NULL,
    -- Nur bei item_type='entity' befüllt (Werte-Kacheln, direkt angeheftete
    -- Entität ohne zugrundeliegendes Chart/Tabelle) — Entitäten haben eine
    -- entity_id (TEXT), keine Integer-ID wie saved_charts/saved_tables,
    -- item_id bleibt für diese Zeilen ungenutzt (0).
    item_entity_id TEXT,
    position INTEGER NOT NULL,
    grid_cols INTEGER NOT NULL DEFAULT 1,
    grid_rows INTEGER NOT NULL DEFAULT 1,
    show_legend INTEGER NOT NULL DEFAULT 0,
    -- Nur bei Werte-Kacheln (item_type='entity') nutzbar — kleiner
    -- Roh-Verlauf statt/neben dem reinen aktuellen Wert.
    show_sparkline INTEGER NOT NULL DEFAULT 1,
    -- Visuelle Verdichtung der 24-h-Sparkline. "raw" zeigt jeden im
    -- Zeitarchiv gespeicherten Punkt, alternativ ein Punkt je Zeit-Bucket.
    sparkline_resolution TEXT NOT NULL DEFAULT 'raw',
    -- Nur bei Werte-Kacheln: Nachkommastellen-Override für die Anzeige
    -- ("auto" = das entity-eigene Feld verwenden, sonst 0-3 wie dort).
    decimals TEXT NOT NULL DEFAULT 'auto',
    -- Nur bei Werte-Kacheln: eigener Kachel-Titel statt des entity-eigenen
    -- friendly_name — NULL/leer bedeutet "übernehmen".
    title TEXT,
    -- Nur bei Werte-Kacheln: "vor X"-Alter neben dem Wert ein-/ausblendbar —
    -- Standard an, da das bisherige (einzige) Verhalten.
    show_age INTEGER NOT NULL DEFAULT 1,
    UNIQUE(dashboard_id, item_type, item_id, item_entity_id)
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
    year_over_year INTEGER NOT NULL DEFAULT 0,
    decimals TEXT NOT NULL DEFAULT 'auto'
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
    bold INTEGER NOT NULL DEFAULT 0,
    aggregation TEXT NOT NULL DEFAULT 'auto'
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
        if "chart_options" not in columns:
            # Individuelle Übersteuerung der Chart-Optionen (Optionen-Menü auf
            # der Entität-eigenen Chart-Seite, entity_detail.html) — ein
            # JSON-Objekt mit dem VOLLSTÄNDIGEN Options-Stand dieser Entität.
            # Leer ('{}'), solange niemand etwas geändert hat: dann gelten die
            # globalen Standardwerte (Setting "entity_chart_defaults", siehe
            # main.py). Sobald die Entität zum ersten Mal eine Option ändert,
            # wird der gesamte aktuelle Stand hier gespeichert ("forkt" von
            # den Defaults ab) — spätere Änderungen an den globalen Defaults
            # wirken sich auf bereits individualisierte Entitäten dann nicht
            # mehr aus, bis sie über "Auf Standard zurücksetzen" wieder auf
            # '{}' zurückgesetzt werden.
            self._conn.execute("ALTER TABLE entities ADD COLUMN chart_options TEXT NOT NULL DEFAULT '{}'")

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
        if "legend_metrics" not in sc_columns:
            # Welche Kennzahlen die Legenden-Chips zeigen (Min/Max/Ø/Summe,
            # siehe chart-legend-item im Template) — jetzt pro Chart
            # konfigurierbar, standardmäßig nur Summe aktiv.
            self._conn.execute(
                "ALTER TABLE saved_charts ADD COLUMN legend_metrics TEXT NOT NULL DEFAULT "
                "'[\"sum\"]'"
            )
        if "legend_style" not in sc_columns:
            # Chips oder Tabelle (Optionen-Menü, "Legenden-Stil") — siehe
            # legend_metrics oben, dieselbe Konfigurierbarkeit pro Chart.
            self._conn.execute(
                "ALTER TABLE saved_charts ADD COLUMN legend_style TEXT NOT NULL DEFAULT 'chips'"
            )
        if "chart_type" not in sc_columns:
            # "auto" (Linie/Balken je Serie automatisch, siehe query.py) oder
            # "timeline" (AN-Intervalle, nur wählbar wenn alle Serien Schalter
            # sind) — dieselbe Konvention wie entities.chart_options auf der
            # Entität-eigenen Chart-Seite (dort zusätzlich "line"/"bar" als
            # explizite Wahl, hier nicht nötig, da Linie/Balken pro Serie
            # ohnehin automatisch feststehen).
            self._conn.execute(
                "ALTER TABLE saved_charts ADD COLUMN chart_type TEXT NOT NULL DEFAULT 'auto'"
            )
        if "show_values" not in sc_columns:
            # "Werte anzeigen" (Optionen-Menü, "Darstellung") — bislang nur ein
            # Laufzeit-Alpine-Feld ohne Persistenz (chart_editor.html setzte es
            # bei jedem Laden stumm auf false zurück); jetzt wie chart_stats/
            # dynamic_y_axis ein gespeichertes Chart-Feld, damit auch die
            # Dashboard-Kachel-Vorschau (main.py _dashboard_tiles_context())
            # dieselbe Einstellung übernehmen kann.
            self._conn.execute(
                "ALTER TABLE saved_charts ADD COLUMN show_values INTEGER NOT NULL DEFAULT 0"
            )
        if "decimals" not in sc_columns:
            # Nachkommastellen-Übersteuerung (Optionen-Menü, "Darstellung") —
            # "auto" übernimmt weiterhin je Serie deren eigene entities.decimals-
            # Einstellung, sonst gilt dieser Wert für ALLE Serien des Charts
            # einheitlich (main.py chart_editor.html render()). Dieselbe
            # Konvention wie entities.decimals/dashboard_pins.decimals.
            self._conn.execute(
                "ALTER TABLE saved_charts ADD COLUMN decimals TEXT NOT NULL DEFAULT 'auto'"
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
        if "aggregation" not in table_row_columns:
            # Aggregation je Entität/Gruppen-Zeile (Ø/Min/Max/Summe) — "auto"
            # ist das bisherige, implizite Verhalten (Zähler/Schalter -> Summe,
            # sonst Durchschnitt), siehe TableCompute.computeValues().
            self._conn.execute("ALTER TABLE table_rows ADD COLUMN aggregation TEXT NOT NULL DEFAULT 'auto'")

        table_column_columns = {row["name"] for row in self._conn.execute("PRAGMA table_info(table_columns)")}
        if "decimals" not in table_column_columns:
            # Dieselbe Konvention wie das entity-eigene "Nachkommastellen"-Feld
            # (formatting.DECIMALS_LABELS) — rein für die Anzeige dieser Spalte.
            self._conn.execute("ALTER TABLE table_columns ADD COLUMN decimals TEXT NOT NULL DEFAULT 'auto'")

        dashboard_columns = {row["name"] for row in self._conn.execute("PRAGMA table_info(dashboard_pins)")}
        if "grid_cols" not in dashboard_columns:
            self._conn.execute("ALTER TABLE dashboard_pins ADD COLUMN grid_cols INTEGER NOT NULL DEFAULT 1")
        if "grid_rows" not in dashboard_columns:
            self._conn.execute("ALTER TABLE dashboard_pins ADD COLUMN grid_rows INTEGER NOT NULL DEFAULT 1")
        if "show_legend" not in dashboard_columns:
            # Legende unter dem Chart einer Dashboard-Kachel (Kachelmenü) — nur
            # ab 2×2 sinnvoll darstellbar, siehe Template/dashboard-tiles.js.
            self._conn.execute("ALTER TABLE dashboard_pins ADD COLUMN show_legend INTEGER NOT NULL DEFAULT 0")
        if "dashboard_id" not in dashboard_columns:
            # UNIQUE(item_type, item_id) muss zu UNIQUE(dashboard_id, item_type,
            # item_id) werden (Konzept "Dashboards": dasselbe Chart darf auf
            # mehreren Dashboards angeheftet sein) — SQLite kann eine
            # UNIQUE-Beschränkung nicht per ALTER TABLE ändern, deshalb Tabelle
            # neu aufbauen (wie schon bei deleted_points oben). Alle
            # bestehenden Pins wandern dabei ins migrierte Default-Dashboard.
            self._conn.execute("ALTER TABLE dashboard_pins RENAME TO dashboard_pins_old")
            self._conn.execute(
                """CREATE TABLE dashboard_pins (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    dashboard_id INTEGER NOT NULL DEFAULT 1,
                    item_type TEXT NOT NULL,
                    item_id INTEGER NOT NULL,
                    position INTEGER NOT NULL,
                    grid_cols INTEGER NOT NULL DEFAULT 1,
                    grid_rows INTEGER NOT NULL DEFAULT 1,
                    show_legend INTEGER NOT NULL DEFAULT 0,
                    UNIQUE(dashboard_id, item_type, item_id)
                )"""
            )
            self._conn.execute(
                "INSERT INTO dashboard_pins "
                "(dashboard_id, item_type, item_id, position, grid_cols, grid_rows, show_legend) "
                "SELECT 1, item_type, item_id, position, grid_cols, grid_rows, show_legend "
                "FROM dashboard_pins_old"
            )
            self._conn.execute("DROP TABLE dashboard_pins_old")

        dashboard_columns = {row["name"] for row in self._conn.execute("PRAGMA table_info(dashboard_pins)")}
        if "item_entity_id" not in dashboard_columns:
            # Werte-Kacheln: eine Entität direkt anheften, ohne zuerst ein
            # Chart/eine Tabelle anzulegen. Entitäten haben aber keine
            # Integer-ID wie saved_charts/saved_tables — zusätzliche Spalte,
            # bei Chart-/Tabellen-Pins ungenutzt (NULL). Die UNIQUE-Beschränkung
            # muss sie mit einschließen (sonst dürfte pro Dashboard nur eine
            # einzige Werte-Kachel existieren, weil item_id für alle den
            # Platzhalter 0 trägt) — SQLite kann eine UNIQUE-Beschränkung nicht
            # per ALTER TABLE ändern, deshalb wieder Tabelle neu aufbauen (wie
            # beim dashboard_id-Umbau oben).
            self._conn.execute("ALTER TABLE dashboard_pins RENAME TO dashboard_pins_old")
            self._conn.execute(
                """CREATE TABLE dashboard_pins (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    dashboard_id INTEGER NOT NULL DEFAULT 1,
                    item_type TEXT NOT NULL,
                    item_id INTEGER NOT NULL,
                    item_entity_id TEXT,
                    position INTEGER NOT NULL,
                    grid_cols INTEGER NOT NULL DEFAULT 1,
                    grid_rows INTEGER NOT NULL DEFAULT 1,
                    show_legend INTEGER NOT NULL DEFAULT 0,
                    show_sparkline INTEGER NOT NULL DEFAULT 1,
                    UNIQUE(dashboard_id, item_type, item_id, item_entity_id)
                )"""
            )
            self._conn.execute(
                "INSERT INTO dashboard_pins (dashboard_id, item_type, item_id, position, grid_cols, grid_rows, show_legend) "
                "SELECT dashboard_id, item_type, item_id, position, grid_cols, grid_rows, show_legend FROM dashboard_pins_old"
            )
            self._conn.execute("DROP TABLE dashboard_pins_old")

        dashboard_columns = {row["name"] for row in self._conn.execute("PRAGMA table_info(dashboard_pins)")}
        if "decimals" not in dashboard_columns:
            # Nachkommastellen-Override für Werte-Kacheln — reine ALTER TABLE-
            # Ergänzung, nicht Teil der UNIQUE-Beschränkung, deshalb ohne den
            # Tabellen-Neuaufbau wie oben.
            self._conn.execute("ALTER TABLE dashboard_pins ADD COLUMN decimals TEXT NOT NULL DEFAULT 'auto'")
        if "title" not in dashboard_columns:
            # Eigener Kachel-Titel für Werte-Kacheln statt des entity-eigenen
            # friendly_name — NULL (Standard) bedeutet "übernehmen".
            self._conn.execute("ALTER TABLE dashboard_pins ADD COLUMN title TEXT")
        if "show_age" not in dashboard_columns:
            # "vor X"-Alter neben dem Wert ein-/ausblendbar — Standard an
            # (bisheriges, einziges Verhalten).
            self._conn.execute("ALTER TABLE dashboard_pins ADD COLUMN show_age INTEGER NOT NULL DEFAULT 1")
        if "sparkline_resolution" not in dashboard_columns:
            self._conn.execute(
                "ALTER TABLE dashboard_pins ADD COLUMN sparkline_resolution TEXT NOT NULL DEFAULT 'raw'"
            )

        dashboards_columns = {row["name"] for row in self._conn.execute("PRAGMA table_info(dashboards)")}
        if "locked" not in dashboards_columns:
            # Fixieren (Konzept "Dashboard sperren"): verhindert versehentliche
            # Layout-Änderungen (Kachelgröße, Entfernen, Umsortieren) beim
            # normalen Ansehen — Umbenennen/Löschen bleiben im Dashboard-Editor
            # unabhängig vom Sperrstatus möglich, betrifft also nur die
            # Kachel-Aktionen auf der Dashboard-Ansichtsseite selbst.
            self._conn.execute("ALTER TABLE dashboards ADD COLUMN locked INTEGER NOT NULL DEFAULT 0")
        if "is_favorite" not in dashboards_columns:
            # Favorit (dieselbe Konvention wie saved_charts/saved_tables) —
            # bestimmt sowohl die Sortierung auf /dashboards als auch im
            # Topnav-Dropdown, da beide dieselbe list_dashboards() nutzen.
            self._conn.execute("ALTER TABLE dashboards ADD COLUMN is_favorite INTEGER NOT NULL DEFAULT 0")
        if "precise_mode" not in dashboards_columns:
            # Präziser Modus: Gitter/Zeilenhöhe halbiert sich (3->6 Spalten,
            # siehe .dashboard-grid.is-precise in dashboard_detail.html/
            # entities.html) — set_dashboard_precise_mode() passt beim
            # Umschalten die gespeicherten Kachelgrößen entsprechend an.
            self._conn.execute("ALTER TABLE dashboards ADD COLUMN precise_mode INTEGER NOT NULL DEFAULT 0")
        if "fill_gaps" not in dashboards_columns:
            # "Lücken auffüllen" (grid-auto-flow: dense, siehe .dashboard-grid.
            # is-dense) — Standard aus, damit bestehende Dashboards ihre
            # heutige strikte Reihenfolge-Anordnung nicht ungefragt ändern.
            self._conn.execute("ALTER TABLE dashboards ADD COLUMN fill_gaps INTEGER NOT NULL DEFAULT 0")

        # Einmalige Anlage des migrierten Default-Dashboards ("Übersicht", fest
        # verankert an id=1, siehe dashboard_pins-Migration oben) — nur beim
        # allerersten Start nach diesem Feature nötig, danach ist dashboards
        # nie mehr leer. is_default markiert es als nicht löschbar, umbenennbar
        # bleibt es trotzdem.
        if self._conn.execute("SELECT COUNT(*) FROM dashboards").fetchone()[0] == 0:
            self._conn.execute(
                "INSERT INTO dashboards (id, name, position, is_default) VALUES (1, 'Übersicht', 0, 1)"
            )

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

    @staticmethod
    def _entity_filter_conditions(
        search: str | None,
        type_filter: str | list[str] | None,
        unit_filter: str | None,
        favorites_only: bool,
    ) -> tuple[list[str], list[str]]:
        if isinstance(type_filter, str):
            type_filter = [type_filter]
        types = [t for t in (type_filter or []) if t and t != "all"]

        conditions: list[str] = []
        params: list[str] = []
        if search:
            conditions.append("(entities.entity_id LIKE ? OR entities.friendly_name LIKE ?)")
            like = f"%{search}%"
            params += [like, like]
        if types:
            placeholders = ", ".join("?" for _ in types)
            conditions.append(f"entities.aggregation_type IN ({placeholders})")
            params += types
        if unit_filter and unit_filter != "all":
            if unit_filter == "__none__":
                conditions.append("entities.unit IS NULL")
            else:
                conditions.append("entities.unit = ?")
                params.append(unit_filter)
        if favorites_only:
            conditions.append("entities.is_favorite = 1")
        return conditions, params

    def list_entities(
        self,
        search: str | None = None,
        type_filter: str | list[str] | None = None,
        unit_filter: str | None = None,
        sort: str = "entity_id",
        direction: str = "asc",
        favorites_only: bool = False,
        limit: int | None = None,
        offset: int = 0,
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
        stattdessen den Rest ganz aus (eigene Ansicht statt Umsortierung).

        limit/offset (ZP-004 in PERFORMANCE.md) grenzen das Ergebnis bereits in
        SQL ein, statt die komplette gefilterte Menge zu laden und erst in Python
        zu paginieren — limit=None (Standard) liefert weiterhin alle Treffer, für
        Aufrufer, die die volle Liste brauchen (Wartungsjobs, Exporte, interne
        Iterationen)."""
        column = SORTABLE_COLUMNS.get(sort, "entity_id")
        direction_sql = "DESC" if direction == "desc" else "ASC"

        conditions, params = self._entity_filter_conditions(
            search, type_filter, unit_filter, favorites_only
        )

        query = (
            "SELECT entities.*, COALESCE(dc.deleted_count, 0) AS deleted_count "
            "FROM entities " + _DELETED_COUNT_JOIN
        )
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += f" ORDER BY {column} {direction_sql}, entities.entity_id ASC"
        if limit is not None:
            query += " LIMIT ? OFFSET ?"
            params = [*params, limit, offset]

        with self._lock, self._conn:
            return self._conn.execute(query, params).fetchall()

    def count_entities(
        self,
        search: str | None = None,
        type_filter: str | list[str] | None = None,
        unit_filter: str | None = None,
        favorites_only: bool = False,
    ) -> int:
        """Gesamtzahl der Treffer für dieselben Filter wie list_entities() —
        Grundlage für die Seiteninfo bei SQL-seitiger Pagination (ZP-004)."""
        conditions, params = self._entity_filter_conditions(
            search, type_filter, unit_filter, favorites_only
        )
        query = "SELECT COUNT(*) AS total FROM entities"
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        with self._lock, self._conn:
            row = self._conn.execute(query, params).fetchone()
            return int(row["total"])

    def set_entity_favorite(self, entity_id: str, favorite: bool) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE entities SET is_favorite = ?, updated_at = ? WHERE entity_id = ?",
                (int(favorite), time.time(), entity_id),
            )

    def set_entity_chart_options(self, entity_id: str, options: dict) -> None:
        """Speichert den vollständigen Chart-Optionen-Stand einer Entität
        (Optionen-Menü, entity_detail.html) — {} setzt sie wieder auf die
        globalen Standardwerte zurück ("Auf Standard zurücksetzen"), siehe
        Kommentar bei der Spalten-Migration oben."""
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE entities SET chart_options = ?, updated_at = ? WHERE entity_id = ?",
                (json.dumps(options), time.time(), entity_id),
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
        legend_metrics: list[str] | None = None,
        legend_style: str = "chips",
        chart_type: str = "auto",
        decimals: str = "auto",
        show_values: bool = False,
    ) -> int:
        now = time.time()
        with self._lock, self._conn:
            cur = self._conn.execute(
                "INSERT INTO saved_charts "
                "(name, entity_ids, range_key, continuous, entity_names, resolution_preset, "
                "dynamic_y_axis, dashboard_animation, chart_stats, legend_metrics, legend_style, "
                "chart_type, decimals, show_values, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    name, json.dumps(entity_ids), range_key, int(continuous),
                    json.dumps(entity_names or {}), resolution_preset,
                    int(dynamic_y_axis), int(dashboard_animation), int(chart_stats),
                    json.dumps(legend_metrics if legend_metrics is not None else ["sum"]),
                    legend_style, chart_type, decimals, int(show_values), now, now,
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
        legend_metrics: list[str] | None = None,
        legend_style: str = "chips",
        chart_type: str = "auto",
        decimals: str = "auto",
        show_values: bool = False,
    ) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE saved_charts SET name = ?, entity_ids = ?, range_key = ?, continuous = ?, "
                "entity_names = ?, resolution_preset = ?, dynamic_y_axis = ?, dashboard_animation = ?, "
                "chart_stats = ?, legend_metrics = ?, legend_style = ?, chart_type = ?, decimals = ?, "
                "show_values = ?, updated_at = ? WHERE id = ?",
                (
                    name, json.dumps(entity_ids), range_key, int(continuous),
                    json.dumps(entity_names or {}), resolution_preset,
                    int(dynamic_y_axis), int(dashboard_animation), int(chart_stats),
                    json.dumps(legend_metrics if legend_metrics is not None else ["sum"]),
                    legend_style, chart_type, decimals, int(show_values), time.time(), chart_id,
                ),
            )

    def _row_to_saved_chart(self, row: sqlite3.Row) -> dict:
        d = dict(row)
        d["entity_ids"] = json.loads(d["entity_ids"])
        d["continuous"] = bool(d["continuous"])
        d["dynamic_y_axis"] = bool(d.get("dynamic_y_axis", 1))
        d["dashboard_animation"] = bool(d.get("dashboard_animation", 1))
        d["chart_stats"] = bool(d.get("chart_stats", 1))
        d["legend_metrics"] = json.loads(d["legend_metrics"]) if d.get("legend_metrics") else ["sum"]
        d["legend_style"] = d.get("legend_style") or "chips"
        d["chart_type"] = d.get("chart_type") or "auto"
        d["decimals"] = d.get("decimals") or "auto"
        d["show_values"] = bool(d.get("show_values", 0))
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

    # -- Dashboards (Konzept "Dashboards"-Menüpunkt: mehrere, unabhängige
    # Dashboards zusätzlich zur festen Übersichtsseite, id=1 ist das beim
    # Feature-Rollout migrierte Default-Dashboard "Übersicht", is_default
    # macht es umbenennbar, aber nicht löschbar). ------------------------------

    def list_dashboards(self) -> list[dict]:
        # Das Standard-Dashboard bleibt immer an erster Stelle, unabhängig von
        # Favoriten — danach Favoriten, sonst die bisherige manuelle Reihenfolge
        # (dieselbe Konvention wie list_saved_charts()/list_saved_tables()).
        # Wirkt sowohl auf /dashboards als auch auf das Topnav-Dropdown, da
        # beide dieselbe Methode nutzen (main.py _template_globals()).
        with self._lock, self._conn:
            rows = self._conn.execute(
                "SELECT * FROM dashboards ORDER BY is_default DESC, is_favorite DESC, position ASC, id ASC"
            ).fetchall()
            return [dict(row) for row in rows]

    def get_default_dashboard_id(self) -> int:
        """Liefert die id des aktuellen Standard-Dashboards — Fallback 1, falls
        (sollte praktisch nie vorkommen) keine Zeile is_default gesetzt hat."""
        with self._lock, self._conn:
            row = self._conn.execute("SELECT id FROM dashboards WHERE is_default = 1 LIMIT 1").fetchone()
            return row["id"] if row else 1

    def set_default_dashboard(self, dashboard_id: int) -> bool:
        """Verschiebt is_default auf ein anderes Dashboard — genau eine Zeile
        trägt es je zu jeder Zeit, deshalb erst das alte Standard-Dashboard
        zurücksetzen, dann das neue setzen, beides in derselben Transaktion."""
        with self._lock, self._conn:
            exists = self._conn.execute(
                "SELECT 1 FROM dashboards WHERE id = ?", (dashboard_id,)
            ).fetchone()
            if exists is None:
                return False
            self._conn.execute("UPDATE dashboards SET is_default = 0 WHERE is_default = 1")
            self._conn.execute("UPDATE dashboards SET is_default = 1 WHERE id = ?", (dashboard_id,))
            return True

    def get_dashboard(self, dashboard_id: int) -> dict | None:
        with self._lock, self._conn:
            row = self._conn.execute("SELECT * FROM dashboards WHERE id = ?", (dashboard_id,)).fetchone()
            return dict(row) if row else None

    def create_dashboard(self, name: str) -> int:
        with self._lock, self._conn:
            max_pos = self._conn.execute("SELECT MAX(position) FROM dashboards").fetchone()[0]
            cursor = self._conn.execute(
                "INSERT INTO dashboards (name, position) VALUES (?, ?)", (name, (max_pos or 0) + 1)
            )
            return cursor.lastrowid

    def rename_dashboard(self, dashboard_id: int, name: str) -> bool:
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "UPDATE dashboards SET name = ? WHERE id = ?", (name, dashboard_id)
            )
            return cursor.rowcount > 0

    def set_dashboard_locked(self, dashboard_id: int, locked: bool) -> bool:
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "UPDATE dashboards SET locked = ? WHERE id = ?", (1 if locked else 0, dashboard_id)
            )
            return cursor.rowcount > 0

    def set_dashboard_favorite(self, dashboard_id: int, favorite: bool) -> bool:
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "UPDATE dashboards SET is_favorite = ? WHERE id = ?", (1 if favorite else 0, dashboard_id)
            )
            return cursor.rowcount > 0

    def set_dashboard_precise_mode(self, dashboard_id: int, precise: bool) -> bool:
        """Verdoppelt beim Einschalten die gespeicherte Größe jeder
        angehefteten Kachel (Gitter/Zeilenhöhe halbieren sich gleichzeitig,
        siehe .dashboard-grid.is-precise) — ohne das würden alle Kacheln beim
        Umschalten plötzlich nur noch halb so groß wirken. Beim Ausschalten
        umgekehrt auf die alte Obergrenze (3) gekappt statt rechnerisch
        halbiert — eine im Präzisen Modus z. B. auf 5 gesetzte Kachel hat
        kein eindeutiges "halbes" Äquivalent im gröberen Gitter."""
        with self._lock, self._conn:
            exists = self._conn.execute(
                "SELECT 1 FROM dashboards WHERE id = ?", (dashboard_id,)
            ).fetchone()
            if exists is None:
                return False
            self._conn.execute(
                "UPDATE dashboards SET precise_mode = ? WHERE id = ?", (1 if precise else 0, dashboard_id)
            )
            if precise:
                self._conn.execute(
                    "UPDATE dashboard_pins SET grid_cols = grid_cols * 2, grid_rows = grid_rows * 2 "
                    "WHERE dashboard_id = ?", (dashboard_id,),
                )
            else:
                self._conn.execute(
                    "UPDATE dashboard_pins SET grid_cols = MIN(grid_cols, 3), grid_rows = MIN(grid_rows, 3) "
                    "WHERE dashboard_id = ?", (dashboard_id,),
                )
            return True

    def set_dashboard_fill_gaps(self, dashboard_id: int, fill_gaps: bool) -> bool:
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "UPDATE dashboards SET fill_gaps = ? WHERE id = ?", (1 if fill_gaps else 0, dashboard_id)
            )
            return cursor.rowcount > 0

    def duplicate_dashboard(self, dashboard_id: int) -> int | None:
        """Kopiert Name UND angeheftete Kacheln (dashboard_pins) auf ein neues
        Dashboard. Weder is_default noch locked noch is_favorite werden
        übernommen — die Kopie ist ein ganz normales, neues Dashboard, das
        genauso wenig automatisch gesperrt oder favorisiert startet wie ein
        frisch angelegtes."""
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT name FROM dashboards WHERE id = ?", (dashboard_id,)
            ).fetchone()
            if row is None:
                return None
            max_pos = self._conn.execute("SELECT MAX(position) FROM dashboards").fetchone()[0]
            cursor = self._conn.execute(
                "INSERT INTO dashboards (name, position) VALUES (?, ?)",
                (f"{row['name']} (Kopie)", (max_pos or 0) + 1),
            )
            new_id = cursor.lastrowid
            pins = self._conn.execute(
                "SELECT item_type, item_id, item_entity_id, position, grid_cols, grid_rows, show_legend, "
                "show_sparkline, sparkline_resolution, decimals, title, show_age "
                "FROM dashboard_pins WHERE dashboard_id = ? ORDER BY position ASC",
                (dashboard_id,),
            ).fetchall()
            self._conn.executemany(
                "INSERT INTO dashboard_pins "
                "(dashboard_id, item_type, item_id, item_entity_id, position, grid_cols, grid_rows, show_legend, "
                "show_sparkline, sparkline_resolution, decimals, title, show_age) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        new_id, p["item_type"], p["item_id"], p["item_entity_id"], p["position"],
                        p["grid_cols"], p["grid_rows"], p["show_legend"], p["show_sparkline"],
                        p["sparkline_resolution"],
                        p["decimals"], p["title"], p["show_age"],
                    )
                    for p in pins
                ],
            )
            return new_id

    def delete_dashboard(self, dashboard_id: int) -> bool:
        """False bei unbekannter id ODER beim Default-Dashboard (is_default) —
        letzteres ist der feste Ankerpunkt für "/" und darf nicht verschwinden."""
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT is_default FROM dashboards WHERE id = ?", (dashboard_id,)
            ).fetchone()
            if row is None or row["is_default"]:
                return False
            self._conn.execute("DELETE FROM dashboards WHERE id = ?", (dashboard_id,))
            self._conn.execute("DELETE FROM dashboard_pins WHERE dashboard_id = ?", (dashboard_id,))
            return True

    # -- Dashboard-Kacheln (Konzept "Offene Punkte", typübergreifend: Charts
    # UND Vergleichstabellen teilen sich dieselbe dashboard_pins-Tabelle und
    # damit denselben Kachel-Grenzwert, siehe DASHBOARD_TILE_LIMIT — jeweils
    # pro Dashboard gezählt). ---------------------------------------------

    DASHBOARD_TILE_LIMIT = 18

    def list_dashboard_pins(self, dashboard_id: int) -> list[dict]:
        """Angeheftete Kacheln eines Dashboards in Reihenfolge — item_type ist
        'chart' oder 'table', der Aufrufer (main.py _dashboard_tiles_context())
        löst jeden Eintrag dann gegen die passende Tabelle auf. Liefert auch
        verwaiste Einträge zurück (sollte durch die Bereinigung in
        delete_saved_chart()/delete_saved_table() praktisch nie vorkommen) —
        das Herausfiltern macht main.py, da nur dort bekannt ist, wie ein
        Chart von einer Tabelle unterschieden und geladen wird."""
        with self._lock, self._conn:
            rows = self._conn.execute(
                "SELECT * FROM dashboard_pins WHERE dashboard_id = ? ORDER BY position ASC", (dashboard_id,)
            ).fetchall()
            return [dict(row) for row in rows]

    def count_dashboard_pins(self, dashboard_id: int | None = None) -> int:
        """Ohne dashboard_id: Gesamtzahl über alle Dashboards (Statistik-Seite).
        Mit dashboard_id: Belegung des Kachel-Limits eines einzelnen Dashboards."""
        with self._lock, self._conn:
            if dashboard_id is None:
                return self._conn.execute("SELECT COUNT(*) FROM dashboard_pins").fetchone()[0]
            return self._conn.execute(
                "SELECT COUNT(*) FROM dashboard_pins WHERE dashboard_id = ?", (dashboard_id,)
            ).fetchone()[0]

    def is_pinned(self, dashboard_id: int, item_type: str, item_id: int) -> bool:
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT 1 FROM dashboard_pins WHERE dashboard_id = ? AND item_type = ? AND item_id = ?",
                (dashboard_id, item_type, item_id),
            ).fetchone()
            return row is not None

    def pin_item_to_dashboard(self, dashboard_id: int, item_type: str, item_id: int) -> bool:
        """Heftet ein Chart oder eine Vergleichstabelle als neue letzte Kachel
        eines Dashboards an — False, wenn das Limit von 18 gleichzeitigen
        Kacheln (Konzept "Offene Punkte": Performance, viele ECharts-Instanzen/
        Tabellen auf einer Seite) für DIESES Dashboard schon erreicht ist, dann
        bleibt alles unverändert. UNIQUE(dashboard_id, item_type, item_id)
        verhindert nebenbei ein doppeltes Anheften auf demselben Dashboard —
        dasselbe Objekt auf einem ANDEREN Dashboard ist dagegen erlaubt."""
        with self._lock, self._conn:
            count = self._conn.execute(
                "SELECT COUNT(*) FROM dashboard_pins WHERE dashboard_id = ?", (dashboard_id,)
            ).fetchone()[0]
            if count >= self.DASHBOARD_TILE_LIMIT:
                return False
            if self._conn.execute(
                "SELECT 1 FROM dashboard_pins WHERE dashboard_id = ? AND item_type = ? AND item_id = ?",
                (dashboard_id, item_type, item_id),
            ).fetchone():
                return True  # schon angeheftet — kein Fehler, einfach nichts weiter tun
            max_pos = self._conn.execute(
                "SELECT MAX(position) FROM dashboard_pins WHERE dashboard_id = ?", (dashboard_id,)
            ).fetchone()[0]
            self._conn.execute(
                "INSERT INTO dashboard_pins (dashboard_id, item_type, item_id, position) VALUES (?, ?, ?, ?)",
                (dashboard_id, item_type, item_id, (max_pos or 0) + 1),
            )
            return True

    def set_dashboard_pin_size(
        self, dashboard_id: int, item_type: str, item_id: int, grid_cols: int, grid_rows: int, max_size: int = 3
    ) -> bool:
        """Speichert die Rastergröße einer angehefteten Dashboard-Kachel.

        Die Validierung lebt zusätzlich zur API auch hier, damit kein anderer
        Aufrufer ungültige CSS-Grid-Spannen persistieren kann. max_size ist 3
        normal, 6 im Präzisen Modus (siehe dashboard_size() in main.py, das
        anhand des Dashboards entscheidet) — der Picker in
        _dashboard_tile_menu.html geht entsprechend weit. False bedeutet: Die
        angegebene Kachel ist nicht (mehr) angeheftet.
        """
        if item_type not in {"chart", "table"}:
            raise ValueError("Ungültiger Dashboard-Kacheltyp")
        if not 1 <= int(grid_cols) <= max_size or not 1 <= int(grid_rows) <= max_size:
            raise ValueError(f"Dashboard-Kachelgröße muss zwischen 1 und {max_size} liegen")
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "UPDATE dashboard_pins SET grid_cols = ?, grid_rows = ? "
                "WHERE dashboard_id = ? AND item_type = ? AND item_id = ?",
                (int(grid_cols), int(grid_rows), dashboard_id, item_type, item_id),
            )
            return cursor.rowcount > 0

    def set_dashboard_pin_legend(
        self, dashboard_id: int, item_type: str, item_id: int, show_legend: bool
    ) -> bool:
        """Speichert, ob eine angeheftete Dashboard-Kachel ihre Chart-Legende
        zeigt (nur bei Charts sinnvoll — Vergleichstabellen haben keine
        Legende, siehe Aufrufer). Nur ab 2×2 Kachelgröße überhaupt sichtbar
        (dashboard-tiles.js prüft das zusätzlich zur Laufzeit anhand der
        aktuellen Größe), der gespeicherte Wert bleibt aber auch beim
        Verkleinern unter 2×2 erhalten, damit er beim erneuten Vergrößern
        nicht verloren geht.
        """
        if item_type != "chart":
            raise ValueError("Legende ist nur für Charts verfügbar")
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "UPDATE dashboard_pins SET show_legend = ? "
                "WHERE dashboard_id = ? AND item_type = ? AND item_id = ?",
                (int(show_legend), dashboard_id, item_type, item_id),
            )
            return cursor.rowcount > 0

    def unpin_item_from_dashboard(self, dashboard_id: int, item_type: str, item_id: int) -> None:
        # Absichtlich keine Neu-Nummerierung der verbleibenden Kacheln — die
        # Reihenfolge über position bleibt stabil, eine Lücke stört dabei
        # nicht (list_dashboard_pins() sortiert nur nach dem Wert, nicht nach
        # Lückenlosigkeit).
        with self._lock, self._conn:
            self._conn.execute(
                "DELETE FROM dashboard_pins WHERE dashboard_id = ? AND item_type = ? AND item_id = ?",
                (dashboard_id, item_type, item_id),
            )

    # -- Werte-Kacheln (item_type='entity') — eine Entität direkt angeheftet,
    # ohne zuerst ein Chart/eine Tabelle anzulegen (Konzept-Erweiterung).
    # Eigene Methoden statt die obigen chart/table-Funktionen um item_entity_id
    # zu erweitern: deren item_id-basierte Signatur bleibt dadurch unverändert
    # für alle bestehenden Aufrufer (charts_pin/tables_pin/dashboard_size/…). --

    def pin_entity_to_dashboard(self, dashboard_id: int, entity_id: str) -> bool:
        """Wie pin_item_to_dashboard(), nur über entity_id statt einer
        Integer-item_id — item_id bleibt für diese Zeilen der Platzhalter 0,
        die eigentliche Identität trägt item_entity_id (siehe UNIQUE-
        Beschränkung der Tabelle)."""
        with self._lock, self._conn:
            count = self._conn.execute(
                "SELECT COUNT(*) FROM dashboard_pins WHERE dashboard_id = ?", (dashboard_id,)
            ).fetchone()[0]
            if count >= self.DASHBOARD_TILE_LIMIT:
                return False
            if self._conn.execute(
                "SELECT 1 FROM dashboard_pins WHERE dashboard_id = ? AND item_type = 'entity' AND item_entity_id = ?",
                (dashboard_id, entity_id),
            ).fetchone():
                return True  # schon angeheftet — kein Fehler, einfach nichts weiter tun
            max_pos = self._conn.execute(
                "SELECT MAX(position) FROM dashboard_pins WHERE dashboard_id = ?", (dashboard_id,)
            ).fetchone()[0]
            self._conn.execute(
                "INSERT INTO dashboard_pins "
                "(dashboard_id, item_type, item_id, item_entity_id, position, show_sparkline) "
                "VALUES (?, 'entity', 0, ?, ?, 1)",
                (dashboard_id, entity_id, (max_pos or 0) + 1),
            )
            return True

    def unpin_entity_from_dashboard(self, dashboard_id: int, entity_id: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "DELETE FROM dashboard_pins WHERE dashboard_id = ? AND item_type = 'entity' AND item_entity_id = ?",
                (dashboard_id, entity_id),
            )

    def set_dashboard_entity_pin_size(
        self, dashboard_id: int, entity_id: str, grid_cols: int, grid_rows: int, max_size: int = 6
    ) -> bool:
        if not 1 <= int(grid_cols) <= max_size or not 1 <= int(grid_rows) <= max_size:
            raise ValueError(f"Dashboard-Kachelgröße muss zwischen 1 und {max_size} liegen")
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "UPDATE dashboard_pins SET grid_cols = ?, grid_rows = ? "
                "WHERE dashboard_id = ? AND item_type = 'entity' AND item_entity_id = ?",
                (int(grid_cols), int(grid_rows), dashboard_id, entity_id),
            )
            return cursor.rowcount > 0

    def set_dashboard_entity_pin_sparkline(
        self, dashboard_id: int, entity_id: str, show_sparkline: bool
    ) -> bool:
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "UPDATE dashboard_pins SET show_sparkline = ? "
                "WHERE dashboard_id = ? AND item_type = 'entity' AND item_entity_id = ?",
                (int(show_sparkline), dashboard_id, entity_id),
            )
            return cursor.rowcount > 0

    def set_dashboard_entity_pin_sparkline_resolution(
        self, dashboard_id: int, entity_id: str, resolution: str
    ) -> bool:
        if resolution not in ("raw", "5min", "30min", "1h"):
            raise ValueError("Ungültige Sparkline-Auflösung")
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "UPDATE dashboard_pins SET sparkline_resolution = ? "
                "WHERE dashboard_id = ? AND item_type = 'entity' AND item_entity_id = ?",
                (resolution, dashboard_id, entity_id),
            )
            return cursor.rowcount > 0

    def set_dashboard_entity_pin_entity(
        self, dashboard_id: int, old_entity_id: str, new_entity_id: str
    ) -> bool:
        """Wechselt die Entität einer Werte-Kachel, ohne Position und
        Darstellungsoptionen der Kachel zu verlieren."""
        with self._lock, self._conn:
            if old_entity_id != new_entity_id and self._conn.execute(
                "SELECT 1 FROM dashboard_pins WHERE dashboard_id = ? "
                "AND item_type = 'entity' AND item_entity_id = ?",
                (dashboard_id, new_entity_id),
            ).fetchone():
                raise ValueError("Diese Entität ist bereits auf dem Dashboard angeheftet")
            cursor = self._conn.execute(
                "UPDATE dashboard_pins SET item_entity_id = ? "
                "WHERE dashboard_id = ? AND item_type = 'entity' AND item_entity_id = ?",
                (new_entity_id, dashboard_id, old_entity_id),
            )
            return cursor.rowcount > 0

    def set_dashboard_entity_pin_show_age(self, dashboard_id: int, entity_id: str, show_age: bool) -> bool:
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "UPDATE dashboard_pins SET show_age = ? "
                "WHERE dashboard_id = ? AND item_type = 'entity' AND item_entity_id = ?",
                (int(show_age), dashboard_id, entity_id),
            )
            return cursor.rowcount > 0

    def set_dashboard_entity_pin_decimals(self, dashboard_id: int, entity_id: str, decimals: str) -> bool:
        if decimals not in ("auto", "0", "1", "2", "3"):
            raise ValueError("Ungültige Nachkommastellen")
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "UPDATE dashboard_pins SET decimals = ? "
                "WHERE dashboard_id = ? AND item_type = 'entity' AND item_entity_id = ?",
                (decimals, dashboard_id, entity_id),
            )
            return cursor.rowcount > 0

    def set_dashboard_entity_pin_title(self, dashboard_id: int, entity_id: str, title: str | None) -> bool:
        """title=None/leer setzt auf "übernehmen" zurück (entity-eigener
        friendly_name statt eines eigenen Kachel-Titels, siehe
        _dashboard_tiles_context() in main.py)."""
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "UPDATE dashboard_pins SET title = ? "
                "WHERE dashboard_id = ? AND item_type = 'entity' AND item_entity_id = ?",
                (title or None, dashboard_id, entity_id),
            )
            return cursor.rowcount > 0

    def reorder_dashboard_pins(self, dashboard_id: int, pins: list[tuple[str, int, str | None]]) -> None:
        """Setzt position neu, komplett durchnummeriert nach der übergebenen
        Reihenfolge (Drag&Drop auf einer Dashboard-Seite, funktioniert über
        Charts, Tabellen UND Werte-Kacheln hinweg gemischt). pins ist eine
        Liste aus (item_type, item_id, item_entity_id) — item_entity_id ist
        bei Werte-Kacheln nötig, da deren item_id für alle den Platzhalter 0
        trägt und sie sonst nicht voneinander unterscheidbar wären ("IS" statt
        "=" für den NULL-sicheren Vergleich bei Chart-/Tabellen-Pins). Der
        UPDATE trifft nur tatsächlich vorhandene Pins DIESES Dashboards, ein
        veralteter/manipulierter Eintrag fügt nie einen neuen hinzu (das
        bleibt pin_item_to_dashboard()/pin_entity_to_dashboard() vorbehalten)."""
        with self._lock, self._conn:
            self._conn.executemany(
                "UPDATE dashboard_pins SET position = ? "
                "WHERE dashboard_id = ? AND item_type = ? AND item_id = ? AND item_entity_id IS ?",
                [
                    (position, dashboard_id, item_type, item_id, item_entity_id)
                    for position, (item_type, item_id, item_entity_id) in enumerate(pins, start=1)
                ],
            )

    # -- Vergleichstabellen (Konzept "Offene Punkte") --------------------------

    def _write_table_columns(self, table_id: int, columns: list[dict]) -> None:
        # Kein eigenes with self._lock hier — wird ausschließlich aus
        # create_saved_table()/update_saved_table() heraus aufgerufen, die
        # den Lock bereits halten (threading.Lock ist nicht reentrant, ein
        # zweites with self._lock hier würde deadlocken).
        self._conn.executemany(
            "INSERT INTO table_columns (table_id, position, label, range_key, offset, year_over_year, decimals) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    table_id, i, c["label"], c["range_key"], c["offset"], int(c["year_over_year"]),
                    c.get("decimals", "auto"),
                )
                for i, c in enumerate(columns)
            ],
        )

    def _write_table_rows(self, table_id: int, rows: list[dict]) -> None:
        self._conn.executemany(
            "INSERT INTO table_rows "
            "(table_id, position, label, row_type, entity_ids, formula, formula_unit, bold, aggregation) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    table_id, i, r["label"], r["row_type"], json.dumps(r["entity_ids"]),
                    r["formula"], r.get("formula_unit", ""), int(r["bold"]), r.get("aggregation", "auto"),
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
                    "year_over_year": bool(c["year_over_year"]), "decimals": c["decimals"],
                }
                for c in column_rows
            ]
            result["rows"] = [
                {
                    "label": r["label"], "row_type": r["row_type"],
                    "entity_ids": json.loads(r["entity_ids"]), "formula": r["formula"],
                    "formula_unit": r["formula_unit"], "bold": bool(r["bold"]),
                    "aggregation": r["aggregation"],
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
                f"""
                SELECT
                    COUNT(*) AS entity_count,
                    COALESCE(SUM(MAX(row_count - COALESCE(dc.deleted_count, 0), 0)), 0) AS total_rows,
                    COALESCE(SUM(size_bytes), 0) AS total_size_bytes
                FROM entities {_DELETED_COUNT_JOIN}
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
                f"""
                SELECT
                    COUNT(*) AS entity_count,
                    COALESCE(SUM(MAX(row_count - COALESCE(dc.deleted_count, 0), 0)), 0) AS total_rows,
                    COALESCE(SUM(size_bytes), 0) AS total_size_bytes
                FROM entities {_DELETED_COUNT_JOIN}
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

    _DUPLICATE_SNAPSHOT_KEY = "duplicate_snapshot_cache"

    def is_duplicate_snapshot_stale(self, min_interval_seconds: float = 3600) -> bool:
        """Ob die im Wartungsplaner zwischengespeicherte Duplikat-Zählung der
        Statistik-Seite (ZP-002 in PERFORMANCE.md) neu berechnet werden sollte.
        Die eigentliche Zählung braucht den Storage-Layer (cleanup.py) und
        bleibt deshalb Sache des Aufrufers — Index kennt nur den Cache-Stand."""
        raw = self.get_setting(self._DUPLICATE_SNAPSHOT_KEY)
        if raw is None:
            return True
        try:
            checked_at = json.loads(raw).get("checked_at")
        except (json.JSONDecodeError, AttributeError):
            return True
        return checked_at is None or time.time() - checked_at >= min_interval_seconds

    def set_duplicate_snapshot(self, rows: list[dict]) -> None:
        payload = json.dumps({"checked_at": time.time(), "rows": rows})
        self.set_setting(self._DUPLICATE_SNAPSHOT_KEY, payload)

    def get_duplicate_snapshot(self) -> dict | None:
        """{"checked_at": ..., "rows": [...]} des letzten Wartungsplaner-Laufs,
        oder None vor dem allerersten Lauf nach einer frischen Installation."""
        raw = self.get_setting(self._DUPLICATE_SNAPSHOT_KEY)
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None

    _CLEANUP_ALLTIME_STATS_PREFIX = "cleanup_alltime_stats:"

    def is_cleanup_alltime_stats_stale(self, entity_id: str, min_interval_seconds: float = 900) -> bool:
        """Ob die je Entität gecachten Ausreißer/Lücken/Duplikate/Wiederholungen-
        Zählungen über die GESAMTE Historie (Bereinigungsseite, "Gesamter
        Zeitraum") neu berechnet werden sollten — ein Vollscan wäre bei
        Entitäten mit Millionen Rohwerten sonst bei jedem Seitenaufruf teuer.
        Anders als der globale Duplikat-Snapshot (ein Wartungsplaner-Lauf für
        alle Entitäten) wird hier bewusst nur je aufgerufener Entität und on
        demand neu gerechnet, statt im Hintergrund für jede Entität im Archiv."""
        raw = self.get_setting(self._CLEANUP_ALLTIME_STATS_PREFIX + entity_id)
        if raw is None:
            return True
        try:
            computed_at = json.loads(raw).get("computed_at")
        except (json.JSONDecodeError, AttributeError):
            return True
        return computed_at is None or time.time() - computed_at >= min_interval_seconds

    def set_cleanup_alltime_stats(self, entity_id: str, counts: dict) -> None:
        payload = json.dumps({"computed_at": time.time(), "counts": counts})
        self.set_setting(self._CLEANUP_ALLTIME_STATS_PREFIX + entity_id, payload)

    def get_cleanup_alltime_stats(self, entity_id: str) -> dict | None:
        """{"computed_at": ..., "counts": {...}} oder None vor der ersten
        Berechnung für diese Entität."""
        raw = self.get_setting(self._CLEANUP_ALLTIME_STATS_PREFIX + entity_id)
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return None

    def is_memory_snapshot_due(self, min_interval_seconds: float = 3600) -> bool:
        """Ob der nächste stündliche RAM-Schnappschuss fällig ist — getrennt
        vom eigentlichen Schreiben (record_memory_snapshot), weil das
        Auslesen des Werts selbst ein externer Netzwerkaufruf an den
        Supervisor ist (main._maintenance_scheduler_loop), der nicht bei
        jedem 30s-Planer-Tick unnötig wiederholt werden soll."""
        with self._lock, self._conn:
            latest_row = self._conn.execute("SELECT MAX(ts) AS latest FROM memory_snapshots").fetchone()
            latest = latest_row["latest"] if latest_row else None
            return latest is None or time.time() - latest >= min_interval_seconds

    def record_memory_snapshot(self, memory_usage_bytes: int) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO memory_snapshots (ts, memory_usage_bytes) VALUES (?, ?)",
                (time.time(), memory_usage_bytes),
            )

    def get_memory_snapshots(self, since_ts: float) -> list[dict]:
        """Für eine künftige RAM-Verlaufsanzeige (noch ungenutzt) — Gegenstück
        zu get_stats_snapshots()."""
        with self._lock, self._conn:
            rows = self._conn.execute(
                "SELECT ts, memory_usage_bytes FROM memory_snapshots WHERE ts >= ? ORDER BY ts",
                (since_ts,),
            ).fetchall()
            return [dict(row) for row in rows]

    def get_stats_by_type(self) -> list[dict]:
        with self._lock, self._conn:
            rows = self._conn.execute(
                f"""
                SELECT aggregation_type, COUNT(*) AS entity_count,
                       COALESCE(SUM(MAX(row_count - COALESCE(dc.deleted_count, 0), 0)), 0) AS total_rows,
                       COALESCE(SUM(size_bytes), 0) AS total_size_bytes
                FROM entities {_DELETED_COUNT_JOIN}
                GROUP BY aggregation_type ORDER BY total_size_bytes DESC
                """
            ).fetchall()
            return [dict(row) for row in rows]

    def get_stats_by_resolution(self) -> list[dict]:
        with self._lock, self._conn:
            rows = self._conn.execute(
                f"""
                SELECT resolution, COUNT(*) AS entity_count,
                       COALESCE(SUM(MAX(row_count - COALESCE(dc.deleted_count, 0), 0)), 0) AS total_rows,
                       COALESCE(SUM(size_bytes), 0) AS total_size_bytes
                FROM entities {_DELETED_COUNT_JOIN}
                GROUP BY resolution ORDER BY total_size_bytes DESC
                """
            ).fetchall()
            return [dict(row) for row in rows]

    def get_stats_by_retention(self) -> list[dict]:
        with self._lock, self._conn:
            rows = self._conn.execute(
                f"""
                SELECT retention, COUNT(*) AS entity_count,
                       COALESCE(SUM(MAX(row_count - COALESCE(dc.deleted_count, 0), 0)), 0) AS total_rows,
                       COALESCE(SUM(size_bytes), 0) AS total_size_bytes
                FROM entities {_DELETED_COUNT_JOIN}
                GROUP BY retention ORDER BY total_size_bytes DESC
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
            # Werte-Kacheln referenzieren die Entität direkt (kein Chart/keine
            # Tabelle dazwischen) — ohne diese Bereinigung bliebe ein
            # verwaister Pin zurück, siehe dieselbe Aufräumlogik in
            # delete_saved_chart()/delete_saved_table().
            self._conn.execute(
                "DELETE FROM dashboard_pins WHERE item_type = 'entity' AND item_entity_id = ?", (entity_id,)
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
