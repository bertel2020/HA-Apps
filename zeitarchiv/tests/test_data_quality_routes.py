"""Housekeeping → Datenqualität auf Routenebene: der Abschnitt, die
aufgeklappte Zeile und die Startwerte der Bereinigungsseite aus dem Link.

Die Zusammenführung der Zahlen selbst deckt test_data_quality.py ab; hier geht
es um das, was nur die Seiten beisteuern.
"""

from __future__ import annotations

import re


def _entity(index, entity_id: str) -> None:
    index.get_or_create_entity(entity_id, "sensor", "measurement", "°C")


def test_datenqualitaet_seite_zeigt_die_matrix_mit_link_auf_die_bereinigung(client) -> None:
    from app.main import index

    from app.storage.cleanup import OUTLIER_RULE_VERSION

    _entity(index, "sensor.pytest_dq_matrix")
    index.set_first_ts_and_add_rows("sensor.pytest_dq_matrix", 1_700_000_000.0, 0)
    index.set_cleanup_alltime_stats(
        "sensor.pytest_dq_matrix", {"all": 100, "duplicates": 12, "counter_decreases": 0},
        "off", OUTLIER_RULE_VERSION,
    )
    html = client.get("/housekeeping/datenqualitaet").text
    assert 'href="/entities/sensor.pytest_dq_matrix/cleanup?filter=duplicates&amp;range=all"' in html
    # Die Zeile lädt ihre Einzelbefunde über einen absoluten Pfad: die Seite
    # liegt unter /housekeeping/, ein relativer Link würde dort ins Leere laufen.
    assert 'hx-get="/entities/sensor.pytest_dq_matrix/data-quality"' in html
    # Ohne htmx bliebe die aufgeklappte Zeile bei „Lädt …“ stehen.
    assert "vendor/htmx" in html


def test_housekeeping_verweist_auf_die_datenqualitaet_statt_sie_einzubetten(client) -> None:
    html = client.get("/housekeeping").text
    assert 'id="datenqualitaet"' in html
    assert 'href="/housekeeping/datenqualitaet"' in html
    assert "dq-table" not in html


def test_aufgeklappte_zeile_ohne_rohdaten_ist_leer_aber_erreichbar(client) -> None:
    from app.main import index

    _entity(index, "sensor.pytest_dq_detail")
    antwort = client.get("/entities/sensor.pytest_dq_detail/data-quality")
    assert antwort.status_code == 200
    assert "Keine Einzelbefunde" in antwort.text


def test_aufgeklappte_zeile_unbekannte_entitaet_ist_404(client) -> None:
    assert client.get("/entities/sensor.pytest_dq_gibt_es_nicht/data-quality").status_code == 404


def _start(html: str) -> tuple[str, str]:
    filter_ = re.search(r'id="rows-filter-input" value="([^"]*)"', html).group(1)
    range_ = re.search(r'id="range-field" value="([^"]*)"', html).group(1)
    return filter_, range_


def test_bereinigungsseite_uebernimmt_filter_und_zeitraum_aus_dem_link(client) -> None:
    from app.main import index

    _entity(index, "sensor.pytest_dq_start")
    html = client.get("/entities/sensor.pytest_dq_start/cleanup?filter=duplicates&range=all").text
    assert _start(html) == ("duplicates", "all")
    assert re.search(r'class="active" data-range="all"', html)
    assert not re.search(r'class="active" data-range="day"', html)


def test_bereinigungsseite_ohne_parameter_startet_wie_bisher(client) -> None:
    from app.main import index

    _entity(index, "sensor.pytest_dq_default")
    html = client.get("/entities/sensor.pytest_dq_default/cleanup").text
    assert _start(html) == ("all", "day")
    assert re.search(r'class="active" data-range="day"', html)


def test_bereinigungsseite_verwirft_ungueltige_startwerte(client) -> None:
    from app.main import index

    _entity(index, "sensor.pytest_dq_invalid")
    html = client.get("/entities/sensor.pytest_dq_invalid/cleanup?filter=blah&range=blub").text
    assert _start(html) == ("all", "day")


def test_heatmap_ansicht_und_startzeitraum_der_bereinigung(client) -> None:
    from datetime import datetime, timedelta

    from app.main import TZ, index
    from app.storage.cleanup import OUTLIER_RULE_VERSION

    _entity(index, "sensor.pytest_dq_heat")
    index.set_first_ts_and_add_rows("sensor.pytest_dq_heat", 1_700_000_000.0, 0)
    gestern = (datetime.now(TZ) - timedelta(days=1)).strftime("%Y-%m-%d")
    index.set_cleanup_alltime_stats(
        "sensor.pytest_dq_heat", {"all": 100, "duplicates": 2}, "off", OUTLIER_RULE_VERSION,
        daily={gestern: {"duplicates": 2}}, gap_threshold="15",
    )
    html = client.get("/housekeeping/datenqualitaet?view=heatmap&kind=duplicates").text
    assert f'href="/entities/sensor.pytest_dq_heat/cleanup?filter=duplicates&amp;range=day&amp;date={gestern}"' in html
    assert "dq-heat" in html
    # Der Link öffnet die Bereinigung auf diesem Tag: einen Tag zurück.
    seite = client.get(f"/entities/sensor.pytest_dq_heat/cleanup?filter=duplicates&range=day&date={gestern}").text
    assert re.search(r'id="offset-field" value="-1"', seite)
    assert re.search(r'id="offset-field" value="0"', client.get("/entities/sensor.pytest_dq_heat/cleanup").text)
