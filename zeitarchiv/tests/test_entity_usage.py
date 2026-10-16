"""Verwendungsprüfung vor dem Mehrfach-Löschen (entity_usage.py): findet gespeicherte
Diagramme, Vergleichstabellen, Dashboard-Wert-Kacheln und Energiedashboard-Rollen."""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

from app import entity_usage
from app.storage.index import Index

COLUMNS = [{"label": "Heute", "range_key": "day", "offset": 0, "year_over_year": False}]


def _row(label: str, entity_ids: list[str]) -> dict:
    return {"label": label, "row_type": "entity", "entity_ids": entity_ids, "formula": "", "formula_unit": "", "bold": False}


def _index(tmp: Path) -> Index:
    index = Index(tmp / "index.sqlite")
    for name in ("sensor.chart", "sensor.table", "sensor.tile", "sensor.energy", "sensor.free"):
        index.get_or_create_entity(name, "sensor", "measurement", "kWh")
    return index


def test_finds_every_kind_of_usage_and_leaves_unused_entities_empty() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-usage-test-"))
    try:
        index = _index(tmp)
        index.create_saved_chart("Mein Diagramm", ["sensor.chart", "sensor.table"], "day", False)
        index.create_saved_table("Meine Tabelle", COLUMNS, [_row("Zeile A", ["sensor.table"]), _row("Zeile B", ["sensor.table"])])
        index.pin_entity_to_dashboard(index.get_default_dashboard_id(), "sensor.tile")
        index.set_setting("energiedashboard_config", json.dumps({"netzbezug": "sensor.energy", "schema_version": 1}))

        usages = entity_usage.find_usages(index, ["sensor.chart", "sensor.table", "sensor.tile", "sensor.energy", "sensor.free"])

        assert [u["kind"] for u in usages["sensor.chart"]] == ["chart"]
        assert usages["sensor.chart"][0]["name"] == "Mein Diagramm"
        assert sorted(u["kind"] for u in usages["sensor.table"]) == ["chart", "table"]
        table_use = next(u for u in usages["sensor.table"] if u["kind"] == "table")
        assert table_use["detail"] == "Zeile A, Zeile B"  # pro Tabelle nur einmal gemeldet
        assert [u["kind"] for u in usages["sensor.tile"]] == ["tile"]
        assert usages["sensor.tile"][0]["auto_cleaned"] is True
        assert [u["kind"] for u in usages["sensor.energy"]] == ["energy"]
        assert usages["sensor.free"] == []

        summary = entity_usage.summarize(usages)
        assert summary == {"requested": 5, "in_use": 4, "needs_attention": 3}
        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_empty_request_and_duplicate_ids() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="zeitarchiv-usage-test-"))
    try:
        index = _index(tmp)
        assert entity_usage.find_usages(index, []) == {}
        assert list(entity_usage.find_usages(index, ["sensor.free", "sensor.free"])) == ["sensor.free"]
        index.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
