"""Test für den referrer-basierten "zurück"-Link (dynamic-back-link.js).

Nutzerwunsch: ein Klick auf "← zurück zum Energiedashboard" soll dessen
vorher gewählten Zeitraum/Datum wiederherstellen — "so, wie man auch am
Browser zurück klicken würde". Die eigentliche Lösung liegt in
energiedashboard.js (syncUrlWithPeriod(), siehe test_energiedashboard_period_url.py):
das Energiedashboard spiegelt Zeitraum/Offset jetzt selbst in seine URL, das
document.referrer trägt sie dadurch automatisch mit — dynamic-back-link.js
selbst bleibt ein einfacher Link auf genau diese URL."""

from __future__ import annotations

from pathlib import Path

JS = (Path(__file__).resolve().parents[1] / "app/static/js/dynamic-back-link.js").read_text(encoding="utf-8")


def test_link_navigates_to_the_referrer_url_as_is() -> None:
    assert "a.href = document.referrer;" in JS


def test_energiedashboard_route_is_recognized() -> None:
    assert r"/^\/energiedashboard(\/|$)/" in JS
