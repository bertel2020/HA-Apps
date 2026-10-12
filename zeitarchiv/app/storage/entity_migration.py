"""Migration: verschiebt oder kopiert den archivierten Verlauf einer Entität
vollständig in eine andere (typischer Auslöser: Home Assistant hat eine
Entität ersetzt oder umbenannt, der bisherige Verlauf soll unter der neuen
entity_id weiterlaufen).

Baut bewusst auf bereits bestehenden, produktiv genutzten Bausteinen auf statt
eigener Merge-/Dedup-Logik: cleanup.iter_raw_rows() liest den vollständigen
Rohwert-Verlauf der Quelle (Hot Buffer + Archiv, Soft-Deletes bereits
herausgefiltert — auch vom CSV-Export genutzt), symcon_import.plan_import_rows()
/import_rows() übernehmen Monats-Klassifizierung und Zeitstempel-Deduplizierung,
dieselben wie beim CSV-/Symcon-Import — beide bleiben dabei bewusst rein additiv
(include_existing_months=True ergänzt Lücken, überschreibt aber nie einen
bestehenden Wert), weil sie auch vom CSV-/Symcon-Import genutzt werden und dort
ein Überschreiben bestehender Werte ein ungewolltes, riskantes Verhalten wäre.

overlap_resolution="source" ("Quellwerte übernehmen") baut deshalb NICHT auf
diesem gemeinsamen Import-Kern auf, sondern setzt nach dem additiven Schritt
oben separat an: _overwrite_existing_values() ermittelt die Quellzeilen, deren
Zeitstempel im Ziel schon einen Wert hatten (dieselbe Menge, die
plan_import_rows()/import_rows() als "duplicate" zählen), und schreibt genau
diese Zeitstempel im Ziel-Archiv bzw. Hot Buffer direkt neu — ein bewusst auf
die Migration begrenzter Sonderpfad, statt das gemeinsame, auch vom CSV-/
Symcon-Import genutzte Import-Modul um eine Overwrite-Option zu erweitern.
"""

from __future__ import annotations

from ..i18n import tr

import math
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pyarrow as pa
import pyarrow.parquet as pq

from . import entity_removal, hotbuffer, rollup
from .cleanup import iter_raw_rows
from .index import Index
from .paths import entity_dir
from .symcon_import import import_rows, plan_import_rows
from ..limits import MAX_IMPORT_ROWS_PER_ENTITY

# "delete": Verschieben, Quelle inkl. Konfiguration entfernen (empfohlen).
# "clear": Verschieben, nur Datenpunkte der Quelle leeren, Konfiguration bleibt.
# "keep": Kopieren, Quelle bleibt vollständig unverändert.
POST_ACTIONS = ("delete", "clear", "keep")

# "target": bei einer Zeitstempel-Überschneidung gewinnt der bereits
# vorhandene Wert der Zielentität (Standard, rein additiv über den
# gemeinsamen Import-Kern — siehe Moduldocstring).
# "source": die Quelle überschreibt den bestehenden Zielwert an
# überschneidenden Zeitstempeln (siehe _overwrite_existing_values()).
OVERLAP_RESOLUTIONS = ("target", "source")


class IncompatibleTypesError(ValueError):
    """Quelle und Ziel haben unterschiedliche aggregation_type. Rollup-
    Bucketgrößen sind typabhängig (siehe docs/data-model.md, Abschnitt
    "Rollups") — eine Migration zwischen unterschiedlichen Typen ist deshalb
    nicht unterstützt, anders als ein bloßer Einheiten-Unterschied."""


def _validate_factor(factor: float) -> None:
    """Dieselbe Grenze wie symcon_import._scaled_raw_rows() — dort privat und
    an SymconVariable gebunden, hier für beliebige (ts, value)-Zeilen."""
    if not math.isfinite(factor) or factor == 0 or abs(factor) > 1_000_000_000_000:
        raise ValueError(tr("Ungültiger Umrechnungsfaktor"))


def _require_entities(index: Index, source_entity_id: str, target_entity_id: str) -> tuple:
    if source_entity_id == target_entity_id:
        raise ValueError(tr("Quelle und Ziel müssen unterschiedliche Entitäten sein"))
    source = index.get_entity(source_entity_id)
    target = index.get_entity(target_entity_id)
    if source is None:
        raise ValueError(tr("Unbekannte Quell-Entität: {source_entity_id}", source_entity_id=source_entity_id))
    if target is None:
        raise ValueError(tr("Unbekannte Ziel-Entität: {target_entity_id}", target_entity_id=target_entity_id))
    if source["aggregation_type"] != target["aggregation_type"]:
        raise IncompatibleTypesError(
            tr("Quelle ({aggregation_type}) und Ziel ({aggregation_type2}) sind unterschiedliche Zähltypen und können nicht zusammengeführt werden.", aggregation_type=source['aggregation_type'], aggregation_type2=target['aggregation_type'])
        )
    return source, target


def _read_source_rows(
    data_dir: Path, index: Index, source_entity_id: str, tz: ZoneInfo
) -> list[tuple[float, float]]:
    """Liest den gesamten Rohwert-Verlauf der Quelle ein (Hot Buffer + Archiv,
    Soft-Deletes bereits herausgefiltert). Leer, wenn die Quelle noch nie
    einen Wert hatte."""
    entity = index.get_entity(source_entity_id)
    if entity is None or entity["first_ts"] is None or entity["last_ts"] is None:
        return []
    return list(
        iter_raw_rows(
            data_dir,
            index,
            source_entity_id,
            entity["first_ts"],
            entity["last_ts"] + 1,
            tz,
            max_rows=MAX_IMPORT_ROWS_PER_ENTITY,
        )
    )


def _scaled(rows: list[tuple[float, float]], factor: float) -> list[tuple[float, float]]:
    if factor == 1.0:
        return rows
    return [(ts, value * factor) for ts, value in rows]


# Bekannte SI-Vorsatz-Stufen für die beiden physikalischen Größen, bei denen
# ein Einheiten-Wechsel zwischen Quelle und Ziel in der Praxis vorkommt (ein
# Gerät wird ersetzt und meldet fortan in Wh statt kWh, oder W statt kW) —
# bewusst NICHT generisch für jede Einheit: "m" wäre sonst z. B. sowohl
# Milli-Präfix als auch die Basiseinheit Meter, "kWh"/"kW" unterscheiden sich
# nur im letzten Buchstaben und dürfen nie gegeneinander vorgeschlagen werden
# (Energie ist keine Leistung) — zwei getrennte, explizite Tabellen statt
# eines Präfix-Parsers vermeiden beide Fallen von vornherein.
_ENERGY_UNIT_SCALE: dict[str, float] = {"Wh": 1.0, "kWh": 1_000.0, "MWh": 1_000_000.0, "GWh": 1_000_000_000.0}
_POWER_UNIT_SCALE: dict[str, float] = {"W": 1.0, "kW": 1_000.0, "MW": 1_000_000.0, "GW": 1_000_000_000.0}


def suggest_factor(source_unit: str, target_unit: str) -> float | None:
    """Schlägt einen Umrechnungsfaktor vor, wenn Quelle und Ziel dieselbe
    physikalische Größe nur in unterschiedlichem SI-Präfix messen (z. B. kWh
    vs. Wh → 1000.0) — None, wenn beide Einheiten gleich sind (kein Faktor
    nötig) oder keine bekannte, rein multiplikative Umrechnung existiert
    (z. B. °C vs. °F, das bräuchte zusätzlich eine additive Verschiebung, die
    das Migrations-Feature nicht unterstützt)."""
    if not source_unit or not target_unit or source_unit == target_unit:
        return None
    for scale in (_ENERGY_UNIT_SCALE, _POWER_UNIT_SCALE):
        if source_unit in scale and target_unit in scale:
            return scale[source_unit] / scale[target_unit]
    return None


@dataclass
class MigrationPlan:
    """Ergebnis eines Dry Runs — reine Vorschau, kein Schreibvorgang."""

    source_entity_id: str
    target_entity_id: str
    source_unit: str
    target_unit: str
    unit_mismatch: bool
    source_row_count: int
    rows_to_transfer: int
    duplicate_rows: int
    factor: float
    suggested_factor: float | None = None


def plan_migration(
    data_dir: Path,
    index: Index,
    source_entity_id: str,
    target_entity_id: str,
    tz: ZoneInfo,
    factor: float = 1.0,
) -> MigrationPlan:
    """Dry Run: berechnet, was execute_migration() tun würde, ohne etwas zu
    schreiben. Wirft IncompatibleTypesError bei unterschiedlichem
    aggregation_type; ein Einheiten-Unterschied wird nur gemeldet
    (unit_mismatch), nicht blockiert — wie beim bestehenden CSV-/Symcon-Import."""
    _validate_factor(factor)
    source, target = _require_entities(index, source_entity_id, target_entity_id)
    rows = _scaled(_read_source_rows(data_dir, index, source_entity_id, tz), factor)
    import_plan = plan_import_rows(
        data_dir, index, rows, target_entity_id, tz,
        source_label=source_entity_id, factor=factor, include_existing_months=True,
    )
    rows_to_transfer = (
        import_plan.rows_to_import + import_plan.rows_to_merge + import_plan.rows_to_update
    )
    source_unit = source["unit"] or ""
    target_unit = target["unit"] or ""
    return MigrationPlan(
        source_entity_id=source_entity_id,
        target_entity_id=target_entity_id,
        source_unit=source_unit,
        target_unit=target_unit,
        unit_mismatch=source_unit != target_unit,
        source_row_count=len(rows),
        rows_to_transfer=rows_to_transfer,
        duplicate_rows=len(rows) - rows_to_transfer,
        factor=factor,
        suggested_factor=suggest_factor(source_unit, target_unit),
    )


@dataclass
class MigrationResult:
    source_entity_id: str
    target_entity_id: str
    post_action: str
    overlap_resolution: str
    rows_transferred: int
    duplicate_rows: int
    overwritten_rows: int = 0
    repointed_dashboards: list[str] = field(default_factory=list)


def _group_by_month(
    rows: list[tuple[float, float]], tz: ZoneInfo
) -> dict[tuple[int, int], list[tuple[float, float]]]:
    """Eigene, kleine Kopie von symcon_import._group_by_month() statt eines
    Imports über Modulgrenzen einer privaten Funktion — hier werden nur die
    (typischerweise wenigen) überschneidenden Zeilen gruppiert, der
    Speicher-Aufwand, den das Original vermeidet, spielt hier keine Rolle."""
    by_month: dict[tuple[int, int], list[tuple[float, float]]] = {}
    for ts, value in rows:
        local = datetime.fromtimestamp(ts, tz)
        by_month.setdefault((local.year, local.month), []).append((ts, value))
    return by_month


def _existing_target_timestamps(
    data_dir: Path, index: Index, target_entity_id: str, tz: ZoneInfo
) -> set[float]:
    """Zeitstempel, die die Zielentität VOR dieser Migration schon hatte —
    genau die Menge, die plan_import_rows()/import_rows() als "duplicate"
    zählen (siehe Moduldocstring)."""
    entity = index.get_entity(target_entity_id)
    if entity is None or entity["first_ts"] is None or entity["last_ts"] is None:
        return set()
    return {
        ts
        for ts, _value in iter_raw_rows(
            data_dir, index, target_entity_id, entity["first_ts"], entity["last_ts"] + 1, tz,
            max_rows=MAX_IMPORT_ROWS_PER_ENTITY,
        )
    }


def _overwrite_existing_values(
    data_dir: Path, index: Index, target: object, overlap_rows: list[tuple[float, float]], tz: ZoneInfo,
) -> int:
    """"Quellwerte übernehmen": schreibt an jedem überschneidenden Zeitstempel
    den Quellwert in die Zielentität, statt ihn (wie der additive Normalpfad)
    zu verwerfen. Prüft für jeden betroffenen Kalendermonat sowohl eine
    Archivdatei als auch eine Hot-Buffer-Datei — nicht nur "ist das der
    laufende Monat", denn eine ältere, noch nicht rotierte Hot-Datei kann
    genauso existieren (siehe hotbuffer.find_stale_hot_files()). Baut bewusst
    NICHT auf _new_rows_for_archive()/_new_rows_for_merge() aus
    symcon_import.py auf — die filtern überschneidende Zeitstempel gerade
    heraus, das Gegenteil von dem, was hier passieren soll."""
    target_entity_id = target["entity_id"]
    aggregation_type = target["aggregation_type"]
    hourly_rollup = bool(target["hourly_rollup"])
    archive_dir = entity_dir(data_dir, "archive", target_entity_id)
    touched_archive = False
    overwritten = 0
    for (year, month), month_rows in _group_by_month(overlap_rows, tz).items():
        label = f"{year:04d}-{month:02d}"
        value_by_ts = {ts: value for ts, value in month_rows}

        archive_path = archive_dir / f"{label}.parquet"
        if archive_path.exists():
            old_size = archive_path.stat().st_size
            table = pq.read_table(archive_path)
            ts_list = table.column("ts").to_pylist()
            value_list = table.column("value").to_pylist()
            hits = [i for i, ts in enumerate(ts_list) if ts in value_by_ts]
            if hits:
                for i in hits:
                    value_list[i] = value_by_ts[ts_list[i]]
                new_columns = {
                    field.name: (
                        pa.array(value_list, type=field.type)
                        if field.name == "value" else table.column(field.name)
                    )
                    for field in table.schema
                }
                new_table = pa.table(new_columns, schema=table.schema)
                temporary = archive_path.with_name(f".{archive_path.name}.migrating")
                try:
                    pq.write_table(new_table, temporary, compression="zstd")
                    temporary.replace(archive_path)
                finally:
                    temporary.unlink(missing_ok=True)
                index.add_size_bytes(target_entity_id, archive_path.stat().st_size - old_size)
                touched_archive = True
                overwritten += len(hits)

        hot_path = hotbuffer.hot_path(data_dir, target_entity_id, month_rows[0][0], tz)
        if hot_path.exists():
            records = hotbuffer.read_full_rows(hot_path)
            hit_count = sum(1 for ts, *_ in records if ts in value_by_ts)
            if hit_count:
                new_records = [
                    (ts, value_by_ts[ts], None, None, None) if ts in value_by_ts
                    else (ts, value, event_id, min_value, max_value)
                    for ts, value, event_id, min_value, max_value in records
                ]
                hotbuffer.write_records(hot_path, new_records)
                overwritten += hit_count

    if touched_archive:
        # Ein überschriebener Wert in einem abgeschlossenen Monat verändert
        # dessen Rollups (und ggf. den Referenzwert des Folgemonats bei einem
        # Zähler) — dieselbe vollständige Neuaggregation wie bei
        # import_rows()' archived_month_updated-Fall. Der laufende Monat
        # (Hot Buffer) braucht das nicht: dessen Rollups werden bei jeder
        # Abfrage ohnehin live neu berechnet.
        rollup.rebuild_entity_rollups(data_dir, target_entity_id, aggregation_type, tz, hourly_rollup=hourly_rollup)
    return overwritten


def _repoint_dashboard_pins(index: Index, source_entity_id: str, target_entity_id: str) -> list[str]:
    """Hängt jede Werte-Kachel, die noch auf die Quelle zeigt, auf das Ziel um
    — Kachel-Einstellungen (Titel, Rundung, Sparkline, Größe) bleiben dabei
    erhalten (Index.set_dashboard_entity_pin_entity()). Existiert auf
    demselben Dashboard schon eine Kachel für das Ziel, entsteht seit dem
    Mehrfach-Anheften-Feature einfach eine zweite, unabhängig konfigurierte
    Kachel der Zielentität — kein Sonderfall mehr wie früher, als das Umhängen
    dafür die Quell-Kachel ersatzlos entfernt hätte."""
    repointed: list[str] = []
    for dashboard in index.list_entity_pin_dashboards(source_entity_id):
        index.set_dashboard_entity_pin_entity(dashboard["id"], dashboard["pin_id"], target_entity_id)
        repointed.append(dashboard["name"])
    return repointed


def execute_migration(
    data_dir: Path,
    index: Index,
    source_entity_id: str,
    target_entity_id: str,
    tz: ZoneInfo,
    *,
    factor: float = 1.0,
    post_action: str = "delete",
    overlap_resolution: str = "target",
) -> MigrationResult:
    """Führt die Migration tatsächlich aus. post_action bestimmt, was mit der
    Quelle nach der Übertragung passiert (siehe POST_ACTIONS), overlap_resolution,
    wer bei einer Zeitstempel-Überschneidung gewinnt (siehe OVERLAP_RESOLUTIONS).
    Aufrufer (die Route in main.py) muss vorher storage_coordinator.entities(
    [source, target]) halten — genau wie beim CSV-/Symcon-Import ist hier kein
    eigenes Locking eingebaut."""
    if post_action not in POST_ACTIONS:
        raise ValueError(tr("Ungültige post_action: {post_action!r}", post_action=post_action))
    if overlap_resolution not in OVERLAP_RESOLUTIONS:
        raise ValueError(tr("Ungültige overlap_resolution: {overlap_resolution!r}", overlap_resolution=overlap_resolution))
    _validate_factor(factor)
    _, target = _require_entities(index, source_entity_id, target_entity_id)
    rows = _scaled(_read_source_rows(data_dir, index, source_entity_id, tz), factor)
    # Vor dem eigentlichen Import ermitteln — import_rows() unten schreibt
    # bereits neue Zeitstempel ins Ziel, der Stand DAVOR ist aber genau das,
    # was "überschneidend" bedeutet (siehe _existing_target_timestamps()).
    existing_target_ts = (
        _existing_target_timestamps(data_dir, index, target_entity_id, tz)
        if overlap_resolution == "source" else set()
    )

    import_result = import_rows(
        data_dir, index, rows, target_entity_id, tz,
        source_label=source_entity_id, factor=factor, include_existing_months=True,
    )
    rows_transferred = (
        import_result.rows_imported + import_result.rows_merged + import_result.rows_updated
    )

    overwritten_rows = 0
    if overlap_resolution == "source" and existing_target_ts:
        overlap_rows = [(ts, value) for ts, value in rows if ts in existing_target_ts]
        if overlap_rows:
            overwritten_rows = _overwrite_existing_values(data_dir, index, target, overlap_rows, tz)

    repointed: list[str] = []
    if post_action != "keep":
        repointed = _repoint_dashboard_pins(index, source_entity_id, target_entity_id)
        if post_action == "delete":
            entity_removal.delete_entity(data_dir, index, source_entity_id)
        elif post_action == "clear":
            entity_removal.delete_all_values(data_dir, index, source_entity_id)

    return MigrationResult(
        source_entity_id=source_entity_id,
        target_entity_id=target_entity_id,
        post_action=post_action,
        overlap_resolution=overlap_resolution,
        rows_transferred=rows_transferred,
        duplicate_rows=import_result.duplicate_rows,
        overwritten_rows=overwritten_rows,
        repointed_dashboards=repointed,
    )
