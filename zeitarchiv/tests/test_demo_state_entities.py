"""Zustands-Entitäten in den Demo-Daten (ROADMAP 1.29): Typ "state", die
Zuordnung in der Reihenfolge von DemoEntity.states, Werte nur gültige
Indizes, Anzeigenamen in der Sprache des Datensatzes."""

import random
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.demo_generation import DEMO_ENTITIES, localized_id, run_generation
from app.i18n import translate
from app.storage import query
from app.storage.index import STATE_UNAVAILABLE_INDEX, Index

TZ = ZoneInfo("Europe/Berlin")
STATE_ENTITIES = [e for e in DEMO_ENTITIES if e.states is not None]


def test_there_are_state_entities() -> None:
    assert {e.entity_id for e in STATE_ENTITIES} == {
        "sensor.demo_saugroboter_status",
        "sensor.demo_waschmaschine_programm",
        "sensor.demo_heizung_betrieb",
    }


@pytest.mark.parametrize("language", ["de", "en"])
def test_generation_writes_state_entities(tmp_path: Path, language: str) -> None:
    index = Index(tmp_path / "index.sqlite")
    try:
        run_generation(tmp_path, index, TZ, random.Random(3), months=2, language=language)
        now = datetime.now(TZ)
        for demo in STATE_ENTITIES:
            entity_id = localized_id(demo.entity_id, language)
            entity = index.get_entity(entity_id)
            assert entity["aggregation_type"] == "state", entity_id
            mapping = index.get_state_map(entity_id)
            assert [row["text"] for row in mapping] == [raw for raw, _label in demo.states]
            assert [row["label"] for row in mapping] == [translate(label, language) for _raw, label in demo.states]
            result = query.query_raw_series(tmp_path, index, entity_id, "month", TZ, now)
            values = {p["value"] for p in result["points"]}
            assert values, entity_id
            allowed = {float(i) for i in range(len(demo.states))} | {float(STATE_UNAVAILABLE_INDEX)}
            assert values <= allowed, (entity_id, values - allowed)
            # Die Anteile kommen für den archivierten Vormonat aus den Rollups.
            shares = query.query_series(tmp_path, index, entity_id, "year", TZ, now)
            assert shares["chart_type"] == "shares"
            assert {idx for p in shares["points"] for idx, _s, _e in p["states"]} <= {int(v) for v in allowed}
            # Die Heizung steht im Sommer durchgehend auf "Aus" — ein
            # zweimonatiger Lauf im August hätte dort nur einen Zustand.
            if demo.entity_id != "sensor.demo_heizung_betrieb":
                assert len(values) > 1, entity_id
    finally:
        index.close()
