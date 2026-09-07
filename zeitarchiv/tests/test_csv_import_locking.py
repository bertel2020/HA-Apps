"""Der CSV-Import darf nicht die ganze App anhalten (ZG-25).

Zwei Dinge liefen hier schief, und nur eines davon war eine Zahl.

Das Sichtbare: `execute_csv_import()` legte das Lesen der Datei, das Sortieren
und das Schreiben gemeinsam in ein `coordinator.exclusive()`. Für die gesamte
Dauer eines Imports stand damit jede andere Speicheroperation still — auch die
laufende Aufnahme aus Home Assistant, die mit dieser Datei nichts zu tun hat.
Dabei schreibt der Import genau eine Entität; der Live-Schreibpfad kommt für
dasselbe längst mit deren Entitätssperre aus (ZA-003), und die Vorschau direkt
daneben ebenfalls.

Das Unsichtbare: `_group_by_month()` baute die übergebenen `(ts, value)`-Tupel
neu zusammen, statt sie weiterzureichen. Ein solches Tupel kostet in CPython
104 Byte — bei zehn Millionen Zeilen, dem eigenen Importlimit, also 1,3 GiB
allein dafür, dass dieselben Werte zweimal im Speicher lagen.

Beim Umbau fiel ein dritter Fehler auf, der nichts mit Speicher zu tun hat und
den `written_rows` verdeckt hatte — siehe
`test_first_ts_comes_from_the_oldest_written_month`.
"""

from __future__ import annotations

import ast
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pyarrow as pa
import pyarrow.parquet as pq

from _paths import APP
from app.storage import symcon_import
from app.storage.index import Index

TZ = ZoneInfo("Europe/Berlin")


# --------------------------------------------------------------------------
# Sperrumfang der Route
# --------------------------------------------------------------------------

def _function(tree: ast.AST, name: str) -> ast.FunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name}() nicht gefunden — Test zeigt ins Leere")


def _coordinator_with_blocks(node: ast.AST) -> list[ast.With]:
    """Alle `with self.deps.coordinator.…()`-Blöcke unterhalb von node."""
    blocks = []
    for child in ast.walk(node):
        if not isinstance(child, ast.With):
            continue
        for item in child.items:
            if "coordinator" in ast.unparse(item.context_expr):
                blocks.append(child)
    return blocks


def _calls(node: ast.AST) -> set[str]:
    return {
        ast.unparse(child.func)
        for child in ast.walk(node)
        if isinstance(child, ast.Call)
    }


def _tree() -> ast.AST:
    return ast.parse((APP / "import_routes.py").read_text(encoding="utf-8"))


def test_the_csv_import_locks_only_its_own_entity() -> None:
    """`exclusive()` hielte für die gesamte Dauer auch jede fremde Entität an."""
    for name in ("execute_csv_import", "plan_csv_locked"):
        blocks = _coordinator_with_blocks(_function(_tree(), name))
        assert blocks, f"{name}() sperrt gar nicht mehr — das wäre zu wenig"
        for block in blocks:
            used = " ".join(ast.unparse(item.context_expr) for item in block.items)
            assert "coordinator.entity(" in used, f"{name}(): {used}"
            assert "exclusive" not in used, (
                f"{name}() nimmt wieder die globale Sperre: {used}"
            )


def test_reading_the_file_happens_before_any_lock_is_taken() -> None:
    """Datei lesen und sortieren berührt keinen Speicherbestand.

    Es ist aber der Teil, der bei großen Dateien den Arbeitsspeicher füllt —
    unter der Sperre wäre er die halbe Wartezeit für alle anderen.
    """
    for name in ("execute_csv_import", "plan_csv_locked"):
        function = _function(_tree(), name)
        assert "csv_import.parse_rows" in _calls(function), f"{name}() parst nicht mehr"
        for block in _coordinator_with_blocks(function):
            assert "csv_import.parse_rows" not in _calls(block), (
                f"{name}() liest die Datei wieder unter der Sperre"
            )


# --------------------------------------------------------------------------
# Speicher
# --------------------------------------------------------------------------

def test_grouping_by_month_passes_the_given_tuples_through() -> None:
    """Identität statt Speichermessung: exakt und ohne Wackelkontakt.

    Ein neu gebautes Tupel ist vom übergebenen nicht zu unterscheiden — außer
    daran, dass es ein anderes Objekt ist. Genau das kostet 104 Byte je Zeile.
    """
    rows = [(1_600_000_000.0 + i * 86_400, float(i)) for i in range(40)]
    by_month = symcon_import._group_by_month(rows, TZ)

    grouped = [row for month in sorted(by_month) for row in by_month[month]]
    assert sorted(grouped) == sorted(rows), "Inhalt verändert"
    assert len(by_month) > 1, "alles in einem Monat — der Test prüfte dann zu wenig"

    originals = {id(row) for row in rows}
    fremd = [row for row in grouped if id(row) not in originals]
    assert not fremd, (
        f"{len(fremd)} von {len(rows)} Tupeln wurden neu gebaut statt weitergereicht — "
        "das ist eine zweite vollständige Kopie des Datensatzes"
    )


# --------------------------------------------------------------------------
# Der Fehler, den written_rows verdeckt hatte
# --------------------------------------------------------------------------

def test_first_ts_comes_from_the_oldest_written_month() -> None:
    """`first_ts` muss das Minimum über ALLE geschriebenen Monate sein.

    Vorher stand dort das erste Element einer mitgeführten Liste. Die wurde in
    der Reihenfolge to_import → to_merge → to_update gefüllt — ein per
    `include_existing_months` ergänzter Monat kann aber älter sein als jeder
    neu angelegte. Ein Import, der einen bestehenden Archivmonat um frühere
    Zeitstempel ergänzt, ließ `first_ts` deshalb zu spät stehen.
    """
    tmp = Path(tempfile.mkdtemp(prefix="zg25-first-ts-"))
    index = Index(tmp / "index.sqlite")
    try:
        entity_id = "sensor.zg25"
        index.get_or_create_entity(entity_id, "sensor", "measurement", "kWh", "ZG-25")

        # Bestehendes Archiv 2020-01, beginnend am 20. Januar.
        spaet = datetime(2020, 1, 20, 12, 0, tzinfo=TZ).timestamp()
        archive = tmp / "archive" / entity_id
        archive.mkdir(parents=True)
        pq.write_table(
            pa.table({"ts": [spaet], "value": [1.0]}), archive / "2020-01.parquet"
        )
        index.set_first_ts_and_add_rows(entity_id, spaet, 1)

        # Der Import bringt einen NEUEN Monat (2021-05, landet in to_import)
        # und ergänzt den BESTEHENDEN um frühere Werte (to_update).
        frueh = datetime(2020, 1, 5, 8, 0, tzinfo=TZ).timestamp()
        neu = datetime(2021, 5, 10, 8, 0, tzinfo=TZ).timestamp()
        result = symcon_import.import_rows(
            tmp, index, [(neu, 5.0), (frueh, 2.0)], entity_id, TZ,
            source_label="probe", include_existing_months=True,
        )
        assert result.rows_imported == 1 and result.rows_updated == 1, result

        assert index.get_entity(entity_id)["first_ts"] == frueh, (
            "first_ts zeigt nicht auf den ältesten geschriebenen Wert"
        )
    finally:
        index.close()
        shutil.rmtree(tmp, ignore_errors=True)
