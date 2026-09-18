"""Housekeeping → Speicherplatz → Automatische Bereinigung: die Einstellungs-
Route (/settings/purge-auto). Was der Wartungsplaner mit den gespeicherten
Werten tatsächlich macht, deckt test_automatic_purge_scheduler.py ab."""

from __future__ import annotations


def test_saving_persists_both_settings_and_renders_the_new_labels(client) -> None:
    from app.main import index

    response = client.post(
        "/settings/purge-auto",
        data={"purge_auto_enabled": "on", "purge_min_age_days": "7"},
    )
    assert response.status_code == 200
    assert index.get_setting("purge_auto_enabled", "off") == "on"
    assert index.get_setting("purge_min_age_days", "") == "7"
    assert "1 Woche" in response.text
    assert "✓ Gespeichert" in response.text

    # Aufräumen, damit spätere Tests (u. a. der Vollseiten-Aufruf) wieder den
    # Standardzustand (Automatik aus) sehen.
    client.post(
        "/settings/purge-auto",
        data={"purge_auto_enabled": "off", "purge_min_age_days": "30"},
    )


def test_an_unknown_value_is_rejected(client) -> None:
    response = client.post("/settings/purge-auto", data={"purge_auto_enabled": "maybe"})
    assert response.status_code == 400

    response = client.post("/settings/purge-auto", data={"purge_min_age_days": "45"})
    assert response.status_code == 400
