"""„Historisch“: Entitäten, die bewusst keine neuen Werte mehr bekommen (Issue 22).

Eine so markierte Entität zählt nirgends mehr als inaktiv — weder in der
Housekeeping-Liste noch in der Status-Kachel oder der Meldung. Ihre Daten bleiben.
"""

import time
from zoneinfo import ZoneInfo

from app import cleanup_tile
from app import notices as notices_mod
from app.storage.index import Index

TZ = ZoneInfo("Europe/Berlin")


def _old_entity(index: Index, entity_id: str, days: float = 40) -> None:
    index.get_or_create_entity(entity_id, "sensor", "total", "kWh")
    index._conn.execute("UPDATE entities SET last_ts = ? WHERE entity_id = ?", (time.time() - days * 86400, entity_id))
    index._conn.commit()


def test_historic_entities_are_not_counted_as_inactive(tmp_path) -> None:
    index = Index(tmp_path / "index.sqlite")
    _old_entity(index, "sensor.archiv")
    _old_entity(index, "sensor.tot")
    assert cleanup_tile.inactive_entities(index) == 2
    assert sum(notices_mod._bucket_inactive_entities(index, TZ).values()) == 2

    index.set_entity_historic("sensor.archiv", True)
    assert index.get_entity("sensor.archiv")["historic"] == 1
    assert cleanup_tile.inactive_entities(index) == 1
    assert sum(notices_mod._bucket_inactive_entities(index, TZ).values()) == 1

    index.set_entity_historic("sensor.archiv", False)
    assert cleanup_tile.inactive_entities(index) == 2
    index.close()


def test_the_housekeeping_list_moves_the_entity_to_the_historic_section(client) -> None:
    from app.main import index

    _old_entity(index, "sensor.hist_liste")
    assert "sensor.hist_liste" in client.get("/housekeeping/stale-entities").text

    antwort = client.post("/housekeeping/entities/sensor.hist_liste/historic?value=1&days=3").text
    stale_teil, historic_teil = antwort.split('<details class="hk-historic"')
    assert "sensor.hist_liste" not in stale_teil
    assert "sensor.hist_liste" in historic_teil
    assert "Wieder aufnehmen" in historic_teil

    antwort = client.post("/housekeeping/entities/sensor.hist_liste/historic?value=0&days=3&hist_open=1").text
    assert "sensor.hist_liste" in antwort.split('<details class="hk-historic"')[0]


def test_historic_ignores_the_day_threshold_and_unknown_entities_404(client) -> None:
    from app.main import index

    index.get_or_create_entity("sensor.hist_frisch", "sensor", "total", "kWh")
    index._conn.execute("UPDATE entities SET last_ts = ? WHERE entity_id = 'sensor.hist_frisch'", (time.time(),))
    index._conn.commit()
    antwort = client.post("/housekeeping/entities/sensor.hist_frisch/historic?value=1&days=30").text
    assert "sensor.hist_frisch" in antwort  # steht im Abschnitt, auch ohne je inaktiv gewesen zu sein
    assert client.post("/housekeeping/entities/sensor.gibt_es_nicht/historic?value=1").status_code == 404


def test_the_entity_page_offers_the_mark_and_shows_it(client) -> None:
    from app.main import index

    index.get_or_create_entity("sensor.hist_seite", "sensor", "total", "kWh")
    assert "Als historisch markieren" in client.get("/entities/sensor.hist_seite").text
    client.post("/housekeeping/entities/sensor.hist_seite/historic?value=1")
    html = client.get("/entities/sensor.hist_seite").text
    assert "Historisch aufheben" in html
    assert 'class="chip" title="Keine neuen Werte mehr erwartet: wird nicht als inaktiv gemeldet">Historisch' in html
