"""Konfiguration einer Entität in Abschnitten (Zeilen mit Kurzzeile, Abzeichen „angepasst“/„fest“)."""

from __future__ import annotations

import pytest


@pytest.fixture()
def entities(client):
    from app import main

    main.index.get_or_create_entity("sensor.cfg_std", "sensor", "measurement", "W")
    main.index.get_or_create_entity("switch.cfg_sw", "switch", None, None)
    yield "sensor.cfg_std", "switch.cfg_sw"
    for entity_id in ("sensor.cfg_std", "switch.cfg_sw"):
        main.index.delete_entity(entity_id)
    for key in ("default_retention", "default_gap_threshold"):
        with main.index._lock, main.index._conn:
            main.index._conn.execute("DELETE FROM settings WHERE key = ?", (key,))


def _set(entity_id: str, **values: str) -> None:
    from app import main

    with main.index._lock, main.index._conn:
        for column, value in values.items():
            main.index._conn.execute(f"UPDATE entities SET {column} = ? WHERE entity_id = ?", (value, entity_id))  # noqa: S608


def test_the_page_has_the_five_sections_in_order_and_a_jump_line(client, entities) -> None:
    html = client.get(f"/entities/{entities[0]}/config").text
    ids = [html.index(f'id="cfg-{name}"') for name in ("allgemein", "erfassung", "aufbewahrung", "darstellung", "pruefung")]
    assert ids == sorted(ids)
    assert 'class="cfg-jump"' in html and 'href="#cfg-pruefung"' in html
    assert 'data-hints="collapsed"' in html
    assert html.index("Datenverwaltung") > html.index('id="cfg-pruefung"')


def test_every_row_has_a_short_line_next_to_the_label(client, entities) -> None:
    html = client.get(f"/entities/{entities[0]}/config").text
    assert html.count('class="field cfg-row"') >= 11
    assert html.count('class="cfg-short"') >= html.count('class="field cfg-row"') - 1


def test_untouched_entity_shows_no_adjusted_badge(client, entities) -> None:
    html = client.get(f"/entities/{entities[0]}/config").text
    assert 'class="badge adjusted"' not in html


def test_a_deviation_from_the_default_is_marked_and_counted(client, entities) -> None:
    _set(entities[0], retention="365d", gap_threshold="60")
    html = client.get(f"/entities/{entities[0]}/config").text
    assert html.count('class="badge adjusted"') == 2
    assert "Standard: Unbegrenzt" in html  # der Standard steht im Tooltip
    assert '<small class="cfg-sec-adj">· 1 angepasst</small>' in html  # Aufbewahrung und Prüfung je 1
    assert '<sup title="1 angepasst">1</sup>' in html


def test_changing_the_global_default_changes_the_marker(client, entities) -> None:
    from app import main

    _set(entities[0], retention="365d")
    main.index.set_setting("default_retention", "365d")
    assert 'class="badge adjusted"' not in client.get(f"/entities/{entities[0]}/config").text


def test_blocked_fields_show_a_fixed_value_and_never_a_deviation(client, entities) -> None:
    html = client.get(f"/entities/{entities[1]}/config").text
    assert html.count('class="badge fixed"') == 4  # Auflösung, Werte ignorieren, Verdichtung, Ausreißer
    assert "disabled" not in html.split('id="cfg-erfassung"')[1].split('id="cfg-darstellung"')[0]
    assert "Anzeigemodus" in html
    _set(entities[1], resolution="1h")  # gesperrt: kein Abzeichen, auch bei abweichendem Wert
    assert 'class="badge adjusted"' not in client.get(f"/entities/{entities[1]}/config").text


def test_form_fragment_after_saving_keeps_the_structure_and_shows_saved(client, entities) -> None:
    response = client.post(f"/entities/{entities[0]}/config", data={"retention": "365d"})
    assert response.status_code == 200
    assert 'id="cfg-erfassung"' in response.text and "✓ Gespeichert" in response.text
    assert 'class="badge adjusted"' in response.text


def test_the_badge_resets_the_field_to_the_default_with_one_post(client, entities) -> None:
    from app import main

    _set(entities[0], gap_threshold="60", retention="365d")
    html = client.get(f"/entities/{entities[0]}/config").text
    assert """hx-vals='{"gap_threshold": "15"}'""" in html  # Standardwert, nicht die Beschriftung
    assert 'hx-confirm="Aufbewahrung auf den Standard' in html  # nur die Aufbewahrung fragt nach
    assert html.count("hx-confirm=") == 1

    response = client.post(f"/entities/{entities[0]}/config", data={"gap_threshold": "15"})
    assert response.status_code == 200
    assert main.index.get_entity(entities[0])["gap_threshold"] == "15"
    assert response.text.count('class="badge adjusted"') == 1  # die Aufbewahrung bleibt abweichend


def test_states_compare_against_the_enforced_values_not_the_global_ones(client, entities) -> None:
    from app import main

    main.index.get_or_create_entity("sensor.cfg_state", "sensor", None, None, state=True)
    try:
        assert 'class="badge adjusted"' not in client.get("/entities/sensor.cfg_state/config").text
    finally:
        main.index.delete_entity("sensor.cfg_state")
