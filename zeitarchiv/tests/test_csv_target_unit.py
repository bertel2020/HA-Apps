"""CSV-Import: Die Maske zeigt die gespeicherte Einheit der Ziel-Entität (ROADMAP 1.3).

Die Zeile wechselt im Browser mit der Auswahl (Alpine); serverseitig prüfbar ist, dass jede
Option ihre Einheit trägt und die Zeile samt Text in der Maske steht."""

from __future__ import annotations

import json
import re

import pytest

from app.main import index

ENTITY = "sensor.csv_unit_hint"
FORM = {"delimiter": ";", "has_header": "on", "ts_col": "0", "ts_format": "de_date", "custom_pattern": ""}
CSV = "Zeitraum;Wert\n15.01.2026;1,0\n15.02.2026;2,0\n"


@pytest.fixture()
def uploaded(client):
    index.get_or_create_entity(ENTITY, "sensor", "total_increasing", "kWh")
    client.post("/import/csv/delete")
    client.post("/import/csv/upload", files={"file": ("werte.csv", CSV.encode("utf-8"), "text/csv")})
    yield
    client.post("/import/csv/delete")
    if index.get_entity(ENTITY) is not None:
        index.delete_entity(ENTITY)


def test_every_option_carries_its_stored_unit(client, uploaded) -> None:
    html = client.post("/import/csv/preview", data={**FORM, "value_col": "1"}).text
    options = re.search(r"entityPicker\((\[.*?\]), ", html, re.S)
    assert options, "Auswahl der Ziel-Entität fehlt in der Maske"
    entries = json.loads(options.group(1).replace("\\u0027", "'"))
    unit_of = {entry["entity_id"]: entry["unit"] for entry in entries}
    assert unit_of[ENTITY] == "kWh"


def test_the_mask_has_the_target_unit_line(client, uploaded) -> None:
    html = client.post("/import/csv/preview", data={**FORM, "value_col": "1", "entity_id": ENTITY}).text
    assert 'class="csv-target-unit"' in html
    element = html[html.index('class="csv-target-unit"'):]
    element = element[:element.index("</small>")]
    # tojson schreibt Umlaute als \\uXXXX; decodiert steht der Satz mit dem Platzhalter da.
    text = json.loads(re.search(r"x-text='(\".*?\")\.replace", element, re.S).group(1))
    assert text == "Gespeichert in @@UNIT@@. Die Werte der Datei müssen in dieser Einheit stehen."
    assert 'replace("@@UNIT@@"' in element  # Alpine setzt die Einheit der Auswahl ein
