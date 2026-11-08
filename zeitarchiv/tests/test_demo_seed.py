"""Vordefinierte Demo-Oberfläche: Energie- und Wetterdashboard, Dashboards, Charts, Tabellen,
Aufräumen bei „Neu erzeugen“, Sprache der Namen."""

import json
import random
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app import demo_seed, energiedashboard_routes, wetter_routes
from app.demo_generation import localized_id, run_generation
from app.demo_mode import DEMO_HISTORY_MONTHS
from app.storage.index import Index

TZ = ZoneInfo("Europe/Berlin")


@pytest.fixture(scope="module")
def demo_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("demo-seed")
    index = Index(path / "index.sqlite")
    run_generation(path, index, TZ, random.Random(5), months=2, language="de")
    return path


@pytest.fixture
def index(demo_dir: Path) -> Index:
    index = Index(demo_dir / "index.sqlite")
    demo_seed.remove_seed(index)
    return index


def test_demo_history_is_at_least_five_years() -> None:
    assert DEMO_HISTORY_MONTHS >= 60


def test_seed_creates_everything(index: Index) -> None:
    assert not demo_seed.is_seeded(index)
    assert demo_seed.seed(index, "de", reset=False) is True
    assert demo_seed.is_seeded(index)

    assert len(index.list_saved_charts()) == len(demo_seed._CHARTS)
    assert len(index.list_saved_tables()) == len(demo_seed._TABLES)
    assert [d["name"] for d in index.list_dashboards()][1:] == ["Energie", "Wetter"]
    for dashboard in index.list_dashboards():
        assert index.count_dashboard_pins(dashboard["id"]) > 0

    energy = json.loads(index.get_setting(energiedashboard_routes.SETTING_CONFIG))
    assert energy["netzbezug"] == "sensor.demo_stromzaehler_bezug"
    assert index.get_setting(energiedashboard_routes.SETTING_ENABLED) == "1"
    weather = json.loads(index.get_setting(wetter_routes.SETTING_CONFIG))
    assert weather["temperature"] == "sensor.demo_aussentemperatur"
    assert index.get_setting(wetter_routes.SETTING_ENABLED) == "1"
    assert wetter_routes.is_wetter_configured(index)


def test_every_referenced_entity_exists(index: Index) -> None:
    demo_seed.seed(index, "de", reset=False)
    energy = json.loads(index.get_setting(energiedashboard_routes.SETTING_CONFIG))
    ids = [energy["netzbezug"], energy["einspeisung"]]
    ids += [row["entity_id"] for row in energy["erzeuger"] + energy["verbraucher"]]
    for storage in energy["speicher"]:
        ids += [storage[key] for key in ("laden_entity_id", "entladen_entity_id", "soc_entity_id", "capacity_entity_id")]
    ids += [energy["kosten"]["preis_netzbezug"], energy["co2"]["faktor_netzbezug_entity"]]
    weather = json.loads(index.get_setting(wetter_routes.SETTING_CONFIG))
    ids += [value for key, value in weather.items() if key in wetter_routes.ROLE_KEYS and value]
    ids += [row["entity_id"] for row in weather["extra_temperatures"] + weather["heating"]]
    assert [i for i in ids if index.get_entity(i) is None] == []
    for chart in index.list_saved_charts():
        assert all(index.get_entity(i) is not None for i in chart["entity_ids"])


def test_second_run_without_reset_changes_nothing(index: Index) -> None:
    demo_seed.seed(index, "de", reset=False)
    assert demo_seed.seed(index, "de", reset=False) is False
    assert len(index.list_saved_charts()) == len(demo_seed._CHARTS)


def test_reset_recreates_and_keeps_own_objects(index: Index) -> None:
    own = index.create_saved_chart("Mein Chart", ["sensor.demo_aussentemperatur"], "week", False)
    demo_seed.seed(index, "de", reset=False)
    before = {c["id"] for c in index.list_saved_charts()}
    assert demo_seed.seed(index, "de", reset=True) is True
    after = {c["id"] for c in index.list_saved_charts()}
    assert own in after
    assert len(after) == len(demo_seed._CHARTS) + 1
    assert (before - {own}).isdisjoint(after)
    assert len(index.list_saved_tables()) == len(demo_seed._TABLES)
    assert len(index.list_dashboards()) == 3
    assert index.count_dashboard_pins(index.get_default_dashboard_id()) == len(
        [item for item in demo_seed._DASHBOARDS[0][1] if item[0] != "section"]
    )


def test_english_names_and_ids(tmp_path: Path) -> None:
    index = Index(tmp_path / "index.sqlite")
    run_generation(tmp_path, index, TZ, random.Random(6), months=2, language="en")
    demo_seed.seed(index, "en", reset=True)
    names = {c["name"] for c in index.list_saved_charts()}
    assert "Dynamic electricity price" in names
    energy = json.loads(index.get_setting(energiedashboard_routes.SETTING_CONFIG))
    assert energy["netzbezug"] == localized_id("sensor.demo_stromzaehler_bezug", "en")
    assert index.get_entity(energy["netzbezug"]) is not None
    assert "Energy balance" in {t["name"] for t in index.list_saved_tables()}
