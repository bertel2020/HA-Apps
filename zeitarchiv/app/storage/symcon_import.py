"""Symcon-Import: liest den Symcon-db-Ordner direkt (Konzept Abschnitt 03,
"Bestehende Daten aus Symcon importieren") statt über ein Skript, das erst in
Symcon laufen müsste.

WICHTIG — ehrliche Einschränkung: das CSV-Format des db-Ordners ist NICHT
offiziell dokumentiert, nur aus Community-Quellen bekannt (Rohdaten in
Jahres-/Monatsordnern, kommagetrennt, Zeitstempel in UTC, Strings
Base64-kodiert). Dieser Parser ist ein erster, vorsichtiger Entwurf gegen genau
dieses Community-Format — noch nicht gegen echte Symcon-Exportdateien
verifiziert (siehe Konzept, "Offene Punkte"). Deshalb: jede erkannte Datei wird
gegen Plausibilitätsregeln geprüft, unklare Dateien/Zeilen werden übersprungen
statt stillschweigend falsch interpretiert.

Der db-Ordner selbst enthält keine Klarnamen — nur Variablen-IDs (Zahlen).
Namen stehen in Symcons Objektbaum, exportiert als separate settings.json
(Konzept "Offene Punkte") — deren Import ist optional; ohne sie bleibt es bei
der Werte-Vorschau (Min/Max) als einzige Wiedererkennungshilfe.
"""

from __future__ import annotations

import json

import csv
import math
import re
import shutil
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pyarrow as pa
import pyarrow.parquet as pq

from . import hotbuffer, rollup
from ..limits import (
    MAX_IMPORT_ROWS_PER_ENTITY,
    MAX_ZIP_COMPRESSION_RATIO,
    MAX_ZIP_MEMBERS,
    MAX_ZIP_UNCOMPRESSED_BYTES,
)
from .index import Index
from .paths import entity_dir


def extract_zip(
    zip_path: Path,
    dest_dir: Path,
    on_progress: Callable[[int, int], None] | None = None,
    *,
    max_members: int = MAX_ZIP_MEMBERS,
    max_uncompressed_bytes: int = MAX_ZIP_UNCOMPRESSED_BYTES,
    max_compression_ratio: int = MAX_ZIP_COMPRESSION_RATIO,
) -> None:
    """Entpackt ein hochgeladenes Symcon-db-ZIP nach dest_dir. Leert dest_dir
    vorher komplett — jeder Upload ersetzt den vorherigen Stand, statt alten und
    neuen Inhalt zu vermischen (Konzept Abschnitt 03: "diese Daten bleiben
    erstmal erhalten", bis sie explizit über den Löschen-Button entfernt werden;
    ein erneuter Upload ist dagegen ein bewusster Ersatz, kein Zusammenführen).
    Entpackt Mitglied für Mitglied statt mit extractall(), damit on_progress(done,
    total) für die Fortschrittsanzeige im Import-Assistenten (Abschnitt 04)
    aufgerufen werden kann — bei ZIPs mit tausenden Dateien sonst ein
    unsichtbarer, mehrere Sekunden langer Stillstand."""
    staging_dir = dest_dir.with_name(dest_dir.name + ".extracting")
    shutil.rmtree(staging_dir, ignore_errors=True)
    try:
        with zipfile.ZipFile(zip_path) as zf:
            members = zf.infolist()
            if len(members) > max_members:
                raise ValueError(f"ZIP enthält zu viele Einträge (maximal {max_members})")
            total_uncompressed = sum(member.file_size for member in members)
            if total_uncompressed > max_uncompressed_bytes:
                raise ValueError(
                    "Entpackte ZIP-Größe überschreitet das erlaubte Limit"
                )
            total_compressed = sum(member.compress_size for member in members)
            if total_uncompressed and (
                total_compressed == 0
                or total_uncompressed / total_compressed > max_compression_ratio
            ):
                raise ValueError("ZIP-Kompressionsverhältnis ist nicht zulässig")
            if any(member.flag_bits & 0x1 for member in members):
                raise ValueError("Verschlüsselte ZIP-Dateien werden nicht unterstützt")
            staging_dir.mkdir(parents=True)
            staging_resolved = staging_dir.resolve()
            for member in members:
                # Zip-Slip-Schutz: kein Eintrag darf über einen "../"-Pfad außerhalb
                # des temporären Zielordners landen.
                target = (staging_dir / member.filename).resolve()
                if target != staging_resolved and staging_resolved not in target.parents:
                    raise ValueError(f"Unsicherer Pfad im ZIP: {member.filename}")
            total = len(members)
            for i, member in enumerate(members, start=1):
                zf.extract(member, staging_dir)
                if on_progress is not None:
                    on_progress(i, total)
        if dest_dir.exists():
            shutil.rmtree(dest_dir)
        staging_dir.replace(dest_dir)
    finally:
        shutil.rmtree(staging_dir, ignore_errors=True)


def delete_source(source_dir: Path) -> None:
    shutil.rmtree(source_dir, ignore_errors=True)

# Symcon-Variablen-IDs sind reine Zahlen — Dateien/Ordner ohne diese Form
# gehören nicht zum Rohdaten-Archiv einer Variable (z. B. andere Dateitypen).
_VARIABLE_ID_RE = re.compile(r"^\d+$")

# Objekt-Schlüssel in settings.json, z. B. "ID56436" — dieselbe Zahl wie der
# CSV-Dateiname im db-Ordner (_VARIABLE_ID_RE), nur mit "ID"-Präfix.
_SETTINGS_ID_RE = re.compile(r"^ID(\d+)$")

# Symcon exportiert selbst angelegte Profile vollständig unter ``profiles``.
# Die eingebauten ``~``-Profile stehen dagegen nur mit ihrem Namen an der
# Variable. Für deren gebräuchliche Messprofile ergänzen wir die bekannte
# Einheit als Fallback.
_BUILTIN_PROFILE_UNITS = (
    ("~Temperature", "°C"),
    ("~Watt", "W"),
    ("~Volt", "V"),
    ("~Ampere", "A"),
    ("~Electricity", "kWh"),
    ("~WindSpeed.kmh", "km/h"),
    ("~Humidity", "%"),
    ("~Illumination", "lx"),
    ("~Pressure", "mbar"),
    ("~Hertz", "Hz"),
    ("~CO2", "ppm"),
    ("~SunAltitude", "°"),
    ("~SunAzimuth", "°"),
    ("~EEP_A50801_ILL", "lx"),
    ("~EEP_A50801_SVC", "V"),
    ("~EEP_A50801_TMP", "°C"),
    ("~EltakoFAFT60_SVC", "V"),
)


def _profile_unit(entry: dict, profiles: dict) -> str | None:
    data = entry.get("data")
    if not isinstance(data, dict):
        return None
    custom_profile = data.get("customProfile")
    standard_profile = data.get("profile")
    profile_name = (
        custom_profile.strip()
        if isinstance(custom_profile, str) and custom_profile.strip()
        else standard_profile.strip()
        if isinstance(standard_profile, str) and standard_profile.strip()
        else ""
    )
    if not profile_name:
        return None

    profile = profiles.get(profile_name)
    if isinstance(profile, dict):
        # Einheiten stehen normalerweise im Suffix. Prefix deckt Profile ab,
        # die etwa ein Währungszeichen vor dem Zahlenwert darstellen.
        for field in ("suffix", "prefix"):
            unit = profile.get(field)
            if isinstance(unit, str) and unit.strip():
                return unit.strip()

    for prefix, unit in _BUILTIN_PROFILE_UNITS:
        if profile_name.startswith(prefix):
            return unit
    return None


def extract_variable_names(raw: bytes | str) -> dict[str, dict[str, str | None]]:
    """Baut aus einer hochgeladenen settings.json (Symcons Objektbaum-Export,
    Konzept "Offene Punkte") eine Variablen-ID → {name, parent, unit}-Zuordnung
    — rein informativ für den Import-Assistenten, beeinflusst Zuordnung/Import
    selbst nicht. Der db-Ordner allein hat keine Metadaten (siehe
    Moduldocstring); dieser Zusatz-Upload ist deshalb komplett optional.

    "parent" ist der Klarname des direkten übergeordneten Objekts (aus dessen
    "parentID", z. B. der Raum/die Kategorie, in der die Variable in Symcon
    einsortiert ist) — hilft beim Wiedererkennen, welche physische Variable
    gemeint ist, wenn der Variablenname allein mehrdeutig ist (z. B. mehrere
    Variablen namens "Temperatur IST" in unterschiedlichen Räumen). Nur EINE
    Ebene wird aufgelöst, kein vollständiger Pfad bis zur Wurzel — deckt den
    üblichen Fall (Variable direkt unter einem Raum/einer Kategorie) ab, ohne
    unnötig tief zu verschachteln. None, falls parentID fehlt, 0 (Wurzel) ist
    oder das Elternobjekt selbst keinen Namen hat.

    Ein echter Symcon-Export verschachtelt den Objektbaum unter einem
    Top-Level-Schlüssel "objects" (neben "compatibility"/"options"/"profiles")
    — die "ID<Zahl>"-Einträge stehen dort, nicht auf oberster Ebene. Fällt auf
    die oberste Ebene zurück, falls kein "objects"-Schlüssel existiert (z. B.
    ein bereits auf den Objektbaum zugeschnittener Auszug).

    Nur Einträge der Form "ID<Zahl>" mit einem nicht-leeren "name"-Feld
    zählen; alles andere im Objektbaum (Kategorien, Instanzen, Skripte,
    Module, …) wird stillschweigend ignoriert statt zu stören — Kategorien
    werden aber weiterhin als mögliche PARENTS berücksichtigt, auch wenn sie
    selbst nicht ins Ergebnis aufgenommen werden (sie haben ja keine eigenen
    Rohdaten im db-Ordner, tauchen also nie als Variablen-ID auf)."""
    data = json.loads(raw)
    if not isinstance(data, dict):
        return {}
    objects = data.get("objects", data)
    if not isinstance(objects, dict):
        return {}
    profiles = data.get("profiles", {})
    if not isinstance(profiles, dict):
        profiles = {}

    def _parent_name(entry: dict) -> str | None:
        parent_id = entry.get("parentID")
        if not parent_id:
            return None
        parent = objects.get(f"ID{parent_id}")
        if not isinstance(parent, dict):
            return None
        parent_name = parent.get("name")
        return parent_name.strip() if isinstance(parent_name, str) and parent_name.strip() else None

    result: dict[str, dict[str, str | None]] = {}
    for key, entry in objects.items():
        match = _SETTINGS_ID_RE.match(key)
        if not match or not isinstance(entry, dict):
            continue
        name = entry.get("name")
        if isinstance(name, str) and name.strip():
            result[match.group(1)] = {
                "name": name.strip(),
                "parent": _parent_name(entry),
                "unit": _profile_unit(entry, profiles),
            }
    return result

# Plausibilitätsgrenzen für Zeitstempel — alles außerhalb ist mit hoher
# Wahrscheinlichkeit ein Parse-Fehler (falsche Spalte, falsches Format) statt
# ein echter Wert, und wird als einzelne Zeile übersprungen statt die ganze
# Datei zu verwerfen.
_MIN_PLAUSIBLE_TS = datetime(2000, 1, 1, tzinfo=timezone.utc).timestamp()
_MAX_PLAUSIBLE_TS = datetime(2100, 1, 1, tzinfo=timezone.utc).timestamp()

# Bool-Variablen aus Symcon: dieselbe 1/0-Normalisierung, die die HA-Integration
# für binary_sensor/switch/input_boolean beim Live-Schreiben bereits anwendet
# (custom_components/zeitarchiv/events.py) — ein importierter Bool-Verlauf muss
# sich in Zeitarchiv genauso verhalten wie ein live archivierter. Die Wertspalte
# ist bei Symcon nicht offiziell dokumentiert (siehe Moduldocstring); "true"/
# "false" ist die naheliegendste Textform, falls der reine Float-Parse fehlschlägt.
_BOOL_VALUES = {"true": 1.0, "false": 0.0, "wahr": 1.0, "falsch": 0.0}


def _parse_value(raw: str) -> float | None:
    try:
        return float(raw)
    except ValueError:
        pass
    return _BOOL_VALUES.get(raw.strip().lower())


@dataclass
class SymconVariable:
    """Eine per Scan gefundene Symcon-Variable — noch ohne Klarname (siehe
    Modul-Docstring), deshalb Werte-Vorschau statt Namensvorschlag."""

    variable_id: str
    files: list[Path] = field(default_factory=list)
    row_count: int = 0
    skipped_rows: int = 0
    first_ts: float | None = None
    last_ts: float | None = None
    min_value: float | None = None
    max_value: float | None = None
    readable: bool = True
    error: str | None = None


def scan_source(
    source_dir: Path, on_progress: Callable[[int, int], None] | None = None
) -> list[SymconVariable]:
    """Durchsucht source_dir rekursiv nach Rohdaten-Dateien einer Symcon-Variable
    (Dateiname = Variablen-ID + .csv) und gruppiert sie pro Variable — eine
    Variable kann über mehrere Jahres-/Monatsordner verteilt sein. on_progress
    (done, total) wird nach jeder analysierten Variable aufgerufen — das Parsen
    aller Rohdaten für die Vorschau (Abschnitt 04) kann bei hunderten Variablen
    mehrere Sekunden dauern, ohne Rückmeldung wirkt das sonst wie ein Hänger."""
    if not source_dir.exists():
        return []
    by_id: dict[str, list[Path]] = {}
    for csv_path in sorted(source_dir.rglob("*.csv")):
        variable_id = csv_path.stem
        if not _VARIABLE_ID_RE.match(variable_id):
            continue
        by_id.setdefault(variable_id, []).append(csv_path)
    items = sorted(by_id.items(), key=lambda kv: int(kv[0]))
    total = len(items)
    result = []
    for i, (vid, paths) in enumerate(items, start=1):
        result.append(_analyze_variable(vid, paths))
        if on_progress is not None:
            on_progress(i, total)
    return result


def _analyze_variable(variable_id: str, files: list[Path]) -> SymconVariable:
    var = SymconVariable(variable_id=variable_id, files=files)
    all_rows: list[tuple[float, float]] = []
    for path in files:
        try:
            skipped = [0]
            all_rows.extend(
                _parse_csv_file(
                    path,
                    max_rows=MAX_IMPORT_ROWS_PER_ENTITY - len(all_rows),
                    skipped_counter=skipped,
                )
            )
            var.skipped_rows += skipped[0]
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            var.readable = False
            var.error = f"{path.name}: {exc}"
            return var
    if not all_rows:
        var.readable = False
        var.error = "keine lesbaren Zeilen gefunden"
        return var
    all_rows.sort()
    var.row_count = len(all_rows)
    var.first_ts = all_rows[0][0]
    var.last_ts = all_rows[-1][0]
    values = [v for _, v in all_rows]
    var.min_value = min(values)
    var.max_value = max(values)
    return var


def _parse_csv_file(
    path: Path, *, max_rows: int | None = None, skipped_counter: list[int] | None = None
) -> list[tuple[float, float]]:
    """Erwartetes Format (Community-Wissen, nicht offiziell dokumentiert):
    kommagetrennt, erste Spalte Unix-Zeitstempel (UTC), zweite Spalte Zahlwert.
    Einzelne unlesbare/unplausible Zeilen werden übersprungen statt die ganze
    Datei zu verwerfen — eine kaputte Zeile soll nicht die komplette Variable
    unnötig als "nicht lesbar" markieren."""
    rows: list[tuple[float, float]] = []
    with path.open("r", encoding="utf-8") as f:
        for line in csv.reader(f):
            if len(line) < 2:
                if skipped_counter is not None:
                    skipped_counter[0] += 1
                continue
            try:
                ts = float(line[0])
            except ValueError:
                if skipped_counter is not None:
                    skipped_counter[0] += 1
                continue
            value = _parse_value(line[1])
            if value is None:
                if skipped_counter is not None:
                    skipped_counter[0] += 1
                continue
            if not (_MIN_PLAUSIBLE_TS <= ts <= _MAX_PLAUSIBLE_TS):
                if skipped_counter is not None:
                    skipped_counter[0] += 1
                continue
            rows.append((ts, value))
            if max_rows is not None and len(rows) > max_rows:
                raise ValueError(
                    (
                        f"mehr als {MAX_IMPORT_ROWS_PER_ENTITY:,} Datenzeilen; "
                        "Importlimit überschritten"
                    ).replace(",", ".")
                )
    return rows


def raw_rows(variable: SymconVariable) -> list[tuple[float, float]]:
    """Liest alle Rohzeilen einer bereits gescannten Variable neu ein (der Scan
    selbst hält nur die Statistik, nicht die vollen Daten im Speicher)."""
    rows: list[tuple[float, float]] = []
    for path in variable.files:
        rows.extend(
            _parse_csv_file(path, max_rows=MAX_IMPORT_ROWS_PER_ENTITY - len(rows))
        )
    rows.sort()
    return rows


@dataclass
class ImportPlan:
    """Ergebnis eines Dry Runs — reine Vorschau, kein Schreibvorgang. execute()
    führt exakt denselben Monats-Klassifizierungscode aus wie plan(), damit
    Vorschau und tatsächliches Ergebnis nie auseinanderlaufen können."""

    entity_id: str
    variable_id: str
    months_to_import: list[str] = field(default_factory=list)
    months_to_merge: list[str] = field(default_factory=list)
    months_to_update: list[str] = field(default_factory=list)
    months_to_skip: list[str] = field(default_factory=list)
    rows_to_import: int = 0
    rows_to_merge: int = 0
    rows_to_update: int = 0
    # Nur vom eigenen CSV-Import (csv_import.py) befüllt — Zeilen, die sich
    # nicht als (Zeitstempel, Wert) lesen ließen und deshalb schon beim
    # Einlesen (nicht erst hier bei der Monats-Klassifizierung) aussortiert
    # wurden. Beim Symcon-Import immer 0 (dessen Parser zählt das nicht mit).
    skipped_rows: int = 0
    factor: float = 1.0


@dataclass
class ImportResult:
    entity_id: str
    variable_id: str
    imported_months: list[str] = field(default_factory=list)
    merged_months: list[str] = field(default_factory=list)
    updated_months: list[str] = field(default_factory=list)
    skipped_months: list[str] = field(default_factory=list)
    rows_imported: int = 0
    rows_merged: int = 0
    rows_updated: int = 0
    skipped_rows: int = 0
    source_rows: int = 0
    duplicate_rows: int = 0
    factor: float = 1.0


def _scaled_raw_rows(variable: SymconVariable, factor: float) -> list[tuple[float, float]]:
    if not math.isfinite(factor) or factor == 0 or abs(factor) > 1_000_000_000_000:
        raise ValueError("Ungültiger Umrechnungsfaktor")
    rows = raw_rows(variable)
    if factor == 1.0:
        return rows
    return [(ts, value * factor) for ts, value in rows]


def _classify_months(
    data_dir: Path,
    entity,
    by_month: dict[tuple[int, int], list[tuple[float, float]]],
    tz: ZoneInfo,
    include_existing_months: bool = False,
) -> tuple[
    list[tuple[int, int, str, list[tuple[float, float]]]],
    list[tuple[int, int, str, list[tuple[float, float]]]],
    list[tuple[int, int, str, list[tuple[float, float]]]],
    list[str],
]:
    """Teilt die Monate einer Variable in vier Gruppen (Konzept Abschnitt 04):

    - to_import: liegt komplett vor dem ersten vorhandenen Zeitarchiv-Wert und
      hat noch keine Archivdatei &mdash; wird als neue Monats-Archivdatei
      geschrieben, wie bisher.
    - to_merge: überlappt den ersten vorhandenen Wert, hat aber noch KEINE
      Archivdatei. Das kann nur der laufende Monat sein &mdash; jeder frühere
      überlappende Monat wäre durch die normale Rotation längst archiviert.
      Wird zeilenweise in den Hot Buffer zusammengeführt (import_variable()),
      Duplikate (exakt gleicher Zeitstempel) werden dabei ausgelassen.
    - to_update: Archivdatei existiert schon und include_existing_months wurde
      explizit aktiviert. Nur noch nicht vorhandene Zeitstempel werden ergänzt.
    - to_skip: Archivdatei existiert schon und die Ergänzungsoption ist aus.
    """
    existing_first_ts = entity["first_ts"]
    entity_id = entity["entity_id"]
    archive_dir = entity_dir(data_dir, "archive", entity_id)
    to_import: list[tuple[int, int, str, list[tuple[float, float]]]] = []
    to_merge: list[tuple[int, int, str, list[tuple[float, float]]]] = []
    to_update: list[tuple[int, int, str, list[tuple[float, float]]]] = []
    to_skip: list[str] = []
    for (year, month), month_rows in sorted(by_month.items()):
        label = f"{year:04d}-{month:02d}"
        archive_path = archive_dir / f"{label}.parquet"
        month_end_ts = _month_end_ts(year, month, tz)
        overlaps_existing = existing_first_ts is not None and month_end_ts > existing_first_ts
        if archive_path.exists():
            if include_existing_months:
                to_update.append((year, month, label, month_rows))
            else:
                to_skip.append(label)
        elif overlaps_existing:
            to_merge.append((year, month, label, month_rows))
        else:
            to_import.append((year, month, label, month_rows))
    return to_import, to_merge, to_update, to_skip


def _new_rows_for_merge(
    data_dir: Path, entity_id: str, month_rows: list[tuple[float, float]], tz: ZoneInfo
) -> list[tuple[float, float]]:
    """Filtert Symcon-Zeilen heraus, deren Zeitstempel exakt mit einer bereits
    im Hot Buffer vorhandenen Zeile übereinstimmt (Duplikat-Prüfung für den
    laufenden Monat, Konzept Abschnitt 04) — der Hot Buffer ist die einzige
    Quelle für den noch offenen Monat, es gibt dafür keine Archivdatei."""
    existing_ts = {
        ts for ts, _ in hotbuffer.read_rows(hotbuffer.hot_path(data_dir, entity_id, month_rows[0][0], tz))
    }
    return sorted(row for row in month_rows if row[0] not in existing_ts)


def _new_rows_for_archive(
    archive_path: Path, month_rows: list[tuple[float, float]]
) -> list[tuple[float, float]]:
    """Liefert nur Quellzeilen mit noch unbekanntem Zeitstempel.

    Vorhandene Archivwerte gewinnen immer; die Opt-in-Funktion ergänzt Lücken
    und ersetzt niemals bereits gespeicherte Messwerte.
    """
    existing_ts = set(pq.read_table(archive_path, columns=["ts"]).column("ts").to_pylist())
    new_by_ts: dict[float, tuple[float, float]] = {}
    for row in month_rows:
        if row[0] not in existing_ts and row[0] not in new_by_ts:
            new_by_ts[row[0]] = row
    return sorted(new_by_ts.values())


def _group_by_month(
    rows: list[tuple[float, float]], tz: ZoneInfo
) -> dict[tuple[int, int], list[tuple[float, float]]]:
    by_month: dict[tuple[int, int], list[tuple[float, float]]] = {}
    for ts, value in rows:
        local = datetime.fromtimestamp(ts, tz)
        by_month.setdefault((local.year, local.month), []).append((ts, value))
    return by_month


def plan_import(
    data_dir: Path,
    index: Index,
    variable: SymconVariable,
    entity_id: str,
    tz: ZoneInfo,
    factor: float = 1.0,
) -> ImportPlan:
    """Dry Run: berechnet, was import_variable() tun würde, ohne etwas zu
    schreiben (Konzept Abschnitt 03) — beliebig oft wiederholbar. Dünner
    Wrapper um plan_import_rows() (siehe dort) — liest nur die Rohdaten aus
    den Symcon-CSV-Dateien der Variable ein."""
    return plan_import_rows(
        data_dir,
        index,
        _scaled_raw_rows(variable, factor),
        entity_id,
        tz,
        source_label=variable.variable_id,
        skipped_rows=variable.skipped_rows,
        factor=factor,
    )


def plan_import_rows(
    data_dir: Path,
    index: Index,
    rows: list[tuple[float, float]],
    entity_id: str,
    tz: ZoneInfo,
    source_label: str = "",
    skipped_rows: int = 0,
    factor: float = 1.0,
    include_existing_months: bool = False,
) -> ImportPlan:
    """Generischer Dry-Run-Kern: berechnet, was import_rows() für bereits
    geparste (ts, value)-Zeilen tun würde, ohne etwas zu schreiben — von
    plan_import() (Symcon-Format) UND vom eigenen CSV-Import (csv_import.py,
    freies Spalten-/Format-Mapping) genutzt, weil die Monats-Klassifizierung
    (Konzept Abschnitt 04) unabhängig vom Herkunftsformat der Rohdaten ist.
    skipped_rows ist rein informativ (siehe ImportPlan) und fließt in keine
    Berechnung hier ein."""
    entity = index.get_entity(entity_id)
    if entity is None:
        raise ValueError(f"Unbekannte Entität: {entity_id}")
    by_month = _group_by_month(rows, tz)
    to_import, to_merge, to_update, to_skip = _classify_months(
        data_dir, entity, by_month, tz, include_existing_months
    )
    rows_to_merge = sum(
        len(_new_rows_for_merge(data_dir, entity_id, month_rows, tz)) for _, _, _, month_rows in to_merge
    )
    archive_dir = entity_dir(data_dir, "archive", entity_id)
    rows_to_update = sum(
        len(_new_rows_for_archive(archive_dir / f"{label}.parquet", month_rows))
        for _, _, label, month_rows in to_update
    )
    return ImportPlan(
        entity_id=entity_id,
        variable_id=source_label,
        months_to_import=[label for _, _, label, _ in to_import],
        months_to_merge=[label for _, _, label, _ in to_merge],
        months_to_update=[label for _, _, label, _ in to_update],
        months_to_skip=to_skip,
        rows_to_import=sum(len(rows) for _, _, _, rows in to_import),
        rows_to_merge=rows_to_merge,
        rows_to_update=rows_to_update,
        skipped_rows=skipped_rows,
        factor=factor,
    )


def import_variable(
    data_dir: Path,
    index: Index,
    variable: SymconVariable,
    entity_id: str,
    tz: ZoneInfo,
    on_month_done: Callable[[str, int], None] | None = None,
    factor: float = 1.0,
) -> ImportResult:
    """Importiert die Rohdaten einer Symcon-Variable in eine bestehende
    Zeitarchiv-Entität — dünner Wrapper um import_rows() (siehe dort), liest
    nur die Rohdaten aus den Symcon-CSV-Dateien der Variable ein."""
    return import_rows(
        data_dir,
        index,
        _scaled_raw_rows(variable, factor),
        entity_id,
        tz,
        source_label=variable.variable_id,
        skipped_rows=variable.skipped_rows,
        on_month_done=on_month_done,
        factor=factor,
    )


def import_rows(
    data_dir: Path,
    index: Index,
    rows: list[tuple[float, float]],
    entity_id: str,
    tz: ZoneInfo,
    source_label: str = "",
    skipped_rows: int = 0,
    on_month_done: Callable[[str, int], None] | None = None,
    factor: float = 1.0,
    include_existing_months: bool = False,
) -> ImportResult:
    """Generischer Import-Kern: schreibt bereits geparste (ts, value)-Zeilen in
    eine bestehende Zeitarchiv-Entität — dieselbe Monats-Klassifizierung wie
    plan_import_rows(), nur dass hier tatsächlich geschrieben wird. Löst danach
    dieselbe Neu-Aggregation aus wie eine Typ-Änderung (Abschnitt 03).
    on_month_done(label, row_count) wird nach jedem geschriebenen Monat
    aufgerufen — für die Fortschrittsanzeige im Import-Assistenten (Abschnitt
    04). Von import_variable() (Symcon-Format) UND vom eigenen CSV-Import
    (csv_import.py) genutzt — siehe plan_import_rows()."""
    entity = index.get_entity(entity_id)
    if entity is None:
        raise ValueError(f"Unbekannte Entität: {entity_id}")
    aggregation_type = entity["aggregation_type"]

    by_month = _group_by_month(rows, tz)
    to_import, to_merge, to_update, to_skip = _classify_months(
        data_dir, entity, by_month, tz, include_existing_months
    )

    result = ImportResult(
        entity_id=entity_id,
        variable_id=source_label,
        skipped_months=to_skip,
        skipped_rows=skipped_rows,
        source_rows=len(rows),
        factor=factor,
    )
    archive_dir = entity_dir(data_dir, "archive", entity_id)
    archive_dir.mkdir(parents=True, exist_ok=True)
    written_rows: list[tuple[float, float]] = []
    archived_month_updated = False

    for year, month, label, month_rows in to_import:
        month_rows.sort()
        table = pa.table(
            {"ts": [r[0] for r in month_rows], "value": [r[1] for r in month_rows]}
        )
        archive_path = archive_dir / f"{label}.parquet"
        pq.write_table(table, archive_path, compression="zstd")
        index.add_size_bytes(entity_id, archive_path.stat().st_size)
        rollup.append_completed_month(data_dir, entity_id, aggregation_type, table, year, month, tz)

        result.imported_months.append(label)
        result.rows_imported += len(month_rows)
        written_rows.extend(month_rows)
        if on_month_done is not None:
            on_month_done(label, len(month_rows))

    for year, month, label, month_rows in to_merge:
        # Der laufende Monat hat keine Archivdatei (Konzept Abschnitt 02) — die
        # Rollups für abgeschlossene Perioden lesen bei der nächsten Abfrage
        # ohnehin live aus dem Hot Buffer neu (Abschnitt 05/06), ein expliziter
        # Rollup-Schreibvorgang ist hier anders als bei to_import nicht nötig.
        new_rows = _new_rows_for_merge(data_dir, entity_id, month_rows, tz)
        result.duplicate_rows += len(month_rows) - len(new_rows)
        if new_rows:
            hotbuffer.append_many(data_dir, entity_id, new_rows, tz)

        result.merged_months.append(label)
        result.rows_merged += len(new_rows)
        written_rows.extend(new_rows)
        if on_month_done is not None:
            on_month_done(label, len(new_rows))

    for year, month, label, month_rows in to_update:
        archive_path = archive_dir / f"{label}.parquet"
        new_rows = _new_rows_for_archive(archive_path, month_rows)
        result.duplicate_rows += len(month_rows) - len(new_rows)
        if new_rows:
            old_size = archive_path.stat().st_size
            old_table = pq.read_table(archive_path, columns=["ts", "value"])
            new_table = pa.table(
                {"ts": [r[0] for r in new_rows], "value": [r[1] for r in new_rows]}
            )
            combined = pa.concat_tables([old_table, new_table]).sort_by("ts")
            temporary = archive_path.with_name(f".{archive_path.name}.importing")
            try:
                pq.write_table(combined, temporary, compression="zstd")
                temporary.replace(archive_path)
            finally:
                temporary.unlink(missing_ok=True)
            index.add_size_bytes(entity_id, archive_path.stat().st_size - old_size)
            archived_month_updated = True

        result.updated_months.append(label)
        result.rows_updated += len(new_rows)
        written_rows.extend(new_rows)
        if on_month_done is not None:
            on_month_done(label, len(new_rows))

    if archived_month_updated:
        # Ein ergänzter Zählerwert kann auch den Referenzwert des Folgemonats
        # verändern. Deshalb alle Rollups der Entität atomar aus den nun
        # vollständigen Roharchiven neu aufbauen, nicht nur den einen Monat.
        rollup.rebuild_entity_rollups(data_dir, entity_id, aggregation_type, tz)

    if written_rows:
        _backfill_index_stats(index, entity_id, written_rows)

    return result


def _month_end_ts(year: int, month: int, tz: ZoneInfo) -> float:
    end_year, end_month = (year + 1, 1) if month == 12 else (year, month + 1)
    return datetime(end_year, end_month, 1, tzinfo=tz).timestamp()


def _backfill_index_stats(index: Index, entity_id: str, rows: list[tuple[float, float]]) -> None:
    """Zieht first_ts/row_count nach, falls der Import ältere Daten als den
    bisherigen ersten Wert eingebracht hat — last_ts bleibt unangetastet
    (kommt aus dem aktuellen Live-Betrieb, nie aus dem Import)."""
    entity = index.get_entity(entity_id)
    if entity is None or not rows:
        return
    new_first = rows[0][0]
    if entity["first_ts"] is None or new_first < entity["first_ts"]:
        index.set_first_ts_and_add_rows(entity_id, new_first, len(rows))
    else:
        index.add_row_count(entity_id, len(rows))
