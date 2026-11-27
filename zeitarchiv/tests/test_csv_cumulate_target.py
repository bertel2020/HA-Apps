"""CSV-Import: "Werte sind Summen je Zeitraum" gilt nur für Zähler.

Die Laufsumme macht aus Periodenwerten laufende Zählerstände. Bei einem Sensor oder Schalter wären die Werte danach aufaddiert
und falsch, bei einer Zustands-Entität bliebe die Option ohne Wirkung. Die Maske sperrt den Haken deshalb, sobald ein gewähltes
Ziel kein Zähler ist, und der Server rechnet auch bei einem anderen Formular nur für Zähler auf."""

from __future__ import annotations

import json
import re

import pytest

from app.main import index

COUNTER = "sensor.csv_cum_counter"
SENSOR = "sensor.csv_cum_sensor"
FORM = {"delimiter": ";", "has_header": "on", "ts_col": "0", "value_col": "1", "ts_format": "de_date", "custom_pattern": ""}
CSV = "Zeitraum;Wert\n15.01.2026;1,0\n15.02.2026;2,0\n"


@pytest.fixture()
def uploaded(client):
    index.get_or_create_entity(COUNTER, "sensor", "total_increasing", "kWh")
    index.get_or_create_entity(SENSOR, "sensor", "measurement", "°C")
    client.post("/import/csv/delete")
    client.post("/import/csv/upload", files={"file": ("werte.csv", CSV.encode("utf-8"), "text/csv")})
    yield
    client.post("/import/csv/delete")
    for entity_id in (COUNTER, SENSOR):
        if index.get_entity(entity_id) is not None:
            index.delete_entity(entity_id)


def _box(html: str) -> str:
    """Das <input> des Hakens "cumulate" samt seinen Attributen."""
    return re.search(r'<input type="checkbox" name="cumulate".*?>', html, re.S).group(0)


def test_the_two_entities_have_the_expected_types(uploaded) -> None:
    assert index.get_entity(COUNTER)["aggregation_type"] == "counter"
    assert index.get_entity(SENSOR)["aggregation_type"] != "counter"


def test_a_counter_target_keeps_the_box_usable_and_set(client, uploaded) -> None:
    html = client.post("/import/csv/preview", data={**FORM, "entity_id": COUNTER, "cumulate": "on"}).text
    box = _box(html)
    assert " checked" in box
    assert " disabled" not in box
    assert "blocked: false" in html  # Alpine blendet den Hinweis damit aus


def test_a_sensor_target_blocks_the_box_and_drops_the_tick(client, uploaded) -> None:
    html = client.post("/import/csv/preview", data={**FORM, "entity_id": SENSOR, "cumulate": "on"}).text
    box = _box(html)
    assert " disabled" in box
    assert " checked" not in box
    assert "Nur für Zähler" in html
    assert "blocked: true" in html  # Alpine zeigt den Hinweis damit an


def test_no_target_yet_leaves_the_box_open(client, uploaded) -> None:
    html = client.post("/import/csv/preview", data={**FORM, "cumulate": "on"}).text
    box = _box(html)
    assert " checked" in box and " disabled" not in box


def test_a_second_target_that_is_no_counter_blocks_the_box_too(client, uploaded) -> None:
    html = client.post(
        "/import/csv/preview",
        data={**FORM, "entity_id": COUNTER, "cumulate": "on", "extra_col": "1", "extra_entity": SENSOR},
    ).text
    box = _box(html)
    assert " disabled" in box and " checked" not in box


def test_the_options_carry_the_aggregation_type_for_the_live_switch(client, uploaded) -> None:
    html = client.post("/import/csv/preview", data={**FORM, "entity_id": COUNTER}).text
    types = re.search(r"types: (\{.*?\}), blocked:", html, re.S)
    assert types, "Typen der Ziele fehlen in der Maske"
    mapping = json.loads(types.group(1).replace("\\u0027", "'"))
    assert mapping[COUNTER] == "counter" and mapping[SENSOR] != "counter"


@pytest.mark.parametrize(
    ("entity_id", "ticked", "expected"),
    [(COUNTER, True, True), (COUNTER, False, False), (SENSOR, True, False), ("sensor.not_archived", True, False), ("", True, False)],
)
def test_the_server_sums_up_only_for_a_counter_even_when_the_form_says_so(uploaded, entity_id: str, ticked: bool, expected: bool) -> None:
    """Auch ein anderes Formular (Haken gesetzt, Ziel ein Sensor) bekommt keine aufaddierten Werte."""
    from types import SimpleNamespace  # noqa: PLC0415

    from app.import_routes import ImportService  # noqa: PLC0415

    service = ImportService.__new__(ImportService)
    service.deps = SimpleNamespace(index=index)
    assert service._csv_cumulate_for(entity_id, ticked) is expected


@pytest.mark.parametrize(
    ("ids", "types", "blocked"),
    [
        ([], {}, False),
        (["", ""], {}, False),
        ([COUNTER], {COUNTER: "counter"}, False),
        ([SENSOR], {SENSOR: "standard"}, True),
        ([COUNTER, SENSOR], {COUNTER: "counter", SENSOR: "standard"}, True),
        (["sensor.state"], {"sensor.state": "state"}, True),
        (["binary_sensor.on_off"], {"binary_sensor.on_off": "switch"}, True),
        (["sensor.unknown"], {}, False),
    ],
)
def test_the_rule_for_a_blocked_box(ids: list[str], types: dict[str, str], blocked: bool) -> None:
    from app.import_routes import ImportService  # noqa: PLC0415

    assert ImportService.csv_cumulate_blocked(ids, types) is blocked
