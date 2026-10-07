"""Englischer Modus der Demo-Daten: nur die Anzeigenamen wechseln, die entity_ids bleiben."""

import random
import re
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.demo_generation import DEMO_ENTITIES, localized_id, run_generation
from app.i18n import load_catalog
from app.storage.index import Index

GERMAN_WORDS = re.compile(r"Wohnzimmer|Leistung|Energie|Spül|Heimspeicher|Balkonkraftwerk|Zähler|zähler|Kühl|Stromverbrauch|Einspeisung|Bezug|Heute|Gesamt")


def test_every_demo_name_has_an_english_translation() -> None:
    catalog = load_catalog("en")["app"]
    for entity in DEMO_ENTITIES:
        english = catalog.get(entity.friendly_name)
        assert english, entity.friendly_name
        assert english.startswith("Demo ")
        assert not GERMAN_WORDS.search(english), english
    assert len({catalog[e.friendly_name] for e in DEMO_ENTITIES}) == len(DEMO_ENTITIES)


def test_english_ids_are_unique_slugs_of_the_english_names() -> None:
    ids = [localized_id(e.entity_id, "en") for e in DEMO_ENTITIES]
    assert len(set(ids)) == len(ids)
    assert all(re.fullmatch(r"[a-z_]+\.demo_[a-z0-9_]+", i) for i in ids), ids
    assert localized_id("sensor.demo_wohnzimmer_temperatur", "en") == "sensor.demo_living_room_temperature"
    assert localized_id("binary_sensor.demo_regensensor", "en") == "binary_sensor.demo_rain_sensor"
    assert localized_id("sensor.demo_wind", "de") == "sensor.demo_wind"


@pytest.mark.parametrize("language", ["de", "en"])
def test_generation_writes_names_and_ids_in_the_requested_language(tmp_path: Path, language: str) -> None:
    index = Index(tmp_path / "index.sqlite")
    run_generation(tmp_path, index, ZoneInfo("Europe/Berlin"), random.Random(1), months=1, language=language)
    names = {e["entity_id"]: e["friendly_name"] for e in index.list_entities()}
    catalog = load_catalog("en")["app"]
    translate = (lambda n: n) if language == "de" else (lambda n: catalog[n])
    assert names == {localized_id(e.entity_id, language): translate(e.friendly_name) for e in DEMO_ENTITIES}


def test_append_continues_the_english_dataset(tmp_path: Path) -> None:
    index = Index(tmp_path / "index.sqlite")
    tz = ZoneInfo("Europe/Berlin")
    run_generation(tmp_path, index, tz, random.Random(1), months=1, language="en")
    result = run_generation(tmp_path, index, tz, random.Random(2), append=True, language="en")
    assert not result.fell_back_to_full_history


def test_switching_language_replaces_the_dataset(tmp_path: Path) -> None:
    index = Index(tmp_path / "index.sqlite")
    tz = ZoneInfo("Europe/Berlin")
    run_generation(tmp_path, index, tz, random.Random(1), months=1)
    run_generation(tmp_path, index, tz, random.Random(1), months=1, clean=True, language="en")
    ids = {e["entity_id"] for e in index.list_entities()}
    assert "sensor.demo_heizung" not in ids
    assert "sensor.demo_heating" in ids
    assert len(ids) == len(DEMO_ENTITIES)
