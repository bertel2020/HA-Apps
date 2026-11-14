"""„Duplikate automatisch entfernen“ auf Routenebene: der Abschnitt in Housekeeping, die Aktionen und das Feld in
der Entitäts-Konfiguration. Die Logik des Laufs deckt test_duplicates_auto.py ab."""

from __future__ import annotations

import pytest

from app import duplicates_auto


@pytest.fixture()
def aus(client):
    """Schalter und Stand vor und nach jedem Test auf „aus“ (der Index der App ist geteilt)."""
    from app.main import index

    def reset():
        for key in ("duplicates_auto_enabled", "duplicates_auto_mode", "duplicates_auto_since", "duplicates_auto_last_run",
                    "duplicates_auto_seen", "duplicates_auto_backlog"):
            index.set_setting(key, "")
        index.set_setting("duplicates_auto_enabled", "off")

    reset()
    yield index
    reset()


def test_abschnitt_zeigt_schalter_aus_und_spalte_gleicher_wert(client, aus) -> None:
    aus.set_duplicate_snapshot([{"entity_id": "sensor.pytest_dupa", "friendly_name": "A", "count": 5, "identical": 3}])
    aus.get_or_create_entity("sensor.pytest_dupa", "sensor", "measurement", "°C")
    html = client.get("/housekeeping").text
    assert 'id="duplicates-body"' in html
    assert 'role="switch"' in html and "checked" not in html.split('role="switch"')[1].split(">")[0]
    assert "davon gleicher Wert" in html
    assert "Automatik</th>" not in html


def test_einschalten_ueber_vorschau_dann_enable(client, aus) -> None:
    aus.get_or_create_entity("sensor.pytest_dupb", "sensor", "measurement", "°C")
    aus.set_duplicate_snapshot([{"entity_id": "sensor.pytest_dupb", "friendly_name": "B", "count": 5, "identical": 3}])
    vorschau = client.post("/housekeeping/duplicates/auto/preview", data={"mode": "identical"})
    assert vorschau.status_code == 200
    assert "Duplikate automatisch entfernen?" in vorschau.text
    assert "würden 3 Duplikate bei 1 Entität entfernt" in vorschau.text
    assert duplicates_auto.settings(aus)["enabled"] is False  # die Vorschau schreibt nichts

    an = client.post("/housekeeping/duplicates/auto/enable", data={"mode": "all"})
    assert an.status_code == 200 and "Automatik eingeschaltet" in an.text
    state = duplicates_auto.settings(aus)
    assert (state["enabled"], state["mode"], state["backlog"]) == (True, "all", True)
    assert "Automatik</th>" in an.text


def test_ausschalten_und_modus_aendern(client, aus) -> None:
    client.post("/housekeeping/duplicates/auto/enable", data={"mode": "identical"})
    modus = client.post("/housekeeping/duplicates/auto/mode", data={"mode": "all"})
    assert modus.status_code == 200 and duplicates_auto.settings(aus)["mode"] == "all"
    aus_antwort = client.post("/housekeeping/duplicates/auto/disable")
    assert "Automatik ausgeschaltet" in aus_antwort.text
    assert duplicates_auto.settings(aus)["enabled"] is False


def test_unbekannter_modus_ist_400(client, aus) -> None:
    assert client.post("/housekeeping/duplicates/auto/enable", data={"mode": "kaputt"}).status_code == 400
    assert client.post("/housekeeping/duplicates/auto/preview", data={"mode": ""}).status_code == 400
    assert duplicates_auto.settings(aus)["enabled"] is False


def test_ausnahme_je_entitaet_aus_housekeeping_und_konfiguration(client, aus) -> None:
    aus.get_or_create_entity("sensor.pytest_dupc", "sensor", "measurement", "°C")
    aus.set_duplicate_snapshot([{"entity_id": "sensor.pytest_dupc", "friendly_name": "C", "count": 2, "identical": 2}])
    client.post("/housekeeping/duplicates/auto/enable", data={"mode": "identical"})
    antwort = client.post("/entities/sensor.pytest_dupc/duplicates-auto", data={"mode": "off", "from": "housekeeping"})
    assert antwort.status_code == 200 and "Ausnahme" in antwort.text and "Wieder einbeziehen" in antwort.text
    assert aus.get_entity("sensor.pytest_dupc")["duplicates_auto"] == "off"
    feld = client.post("/entities/sensor.pytest_dupc/duplicates-auto", data={"mode": "inherit"})
    assert 'id="duplicates-auto-field"' in feld.text and "Ausnehmen" in feld.text
    assert aus.get_entity("sensor.pytest_dupc")["duplicates_auto"] == "inherit"
    assert client.post("/entities/sensor.pytest_dupc/duplicates-auto", data={"mode": "x"}).status_code == 400
    assert client.post("/entities/sensor.pytest_gibts_nicht/duplicates-auto", data={"mode": "off"}).status_code == 404


def test_konfiguration_zeigt_das_feld(client, aus) -> None:
    aus.get_or_create_entity("sensor.pytest_dupd", "sensor", "measurement", "°C")
    assert 'id="duplicates-auto-field"' in client.get("/entities/sensor.pytest_dupd/config").text


def test_rueckgaengig_mit_unbekanntem_lauf_ist_harmlos_und_ungueltiger_wert_400(client, aus) -> None:
    ok = client.post("/housekeeping/duplicates/auto/undo", data={"run_at": "123.0"})
    assert ok.status_code == 200 and "0 Werte wiederhergestellt" in ok.text
    assert client.post("/housekeeping/duplicates/auto/undo", data={"run_at": "kaputt"}).status_code == 400


def test_seitenbesuch_markiert_die_meldung_als_gesehen(client, aus) -> None:
    duplicates_auto.enable(aus, "identical", 1.0)
    aus.set_setting("duplicates_auto_seen", "1.0")
    client.get("/housekeeping")
    assert duplicates_auto.settings(aus)["seen"] > 1.0
