"""Tests für die URL-Spiegelung von Zeitraum/Offset im Energiedashboard
(Nutzerwunsch: "zurück zum Energiedashboard" soll den vorher gewählten
Zeitraum/Datum wiederherstellen, so als hätte man den Browser-Zurück-Button
geklickt). Bisher hielt energieFlow() range/offset NUR als clientseitigen
Alpine-Zustand — ein Link zurück zur Dashboard-URL landete deshalb immer auf
der Standardansicht ('Tag', heute). Jetzt spiegelt syncUrlWithPeriod() beide
Werte per history.replaceState() in die URL, und readInitialRangeOffset()
liest sie beim Laden wieder ein."""

from __future__ import annotations

from pathlib import Path

JS = (Path(__file__).resolve().parents[1] / "app/static/js/energiedashboard.js").read_text(encoding="utf-8")


def test_initial_range_and_offset_are_read_from_the_url() -> None:
    assert "function readInitialRangeOffset()" in JS
    assert "new URLSearchParams(window.location.search)" in JS
    assert "range: initialPeriod.range," in JS
    assert "offset: initialPeriod.offset," in JS


def test_invalid_or_missing_range_falls_back_to_day() -> None:
    assert "RANGE_KEYS.includes(range) ? range : 'day'" in JS


def test_non_integer_offset_falls_back_to_zero() -> None:
    assert "Number.isInteger(offset) ? offset : 0" in JS


def test_url_sync_uses_replace_state_not_push_state() -> None:
    """pushState würde bei jedem Perioden-Klick einen eigenen Verlaufs-Schritt
    anlegen — der Browser-Zurück-Button müsste sich dann durch jeden
    einzelnen Zeitraum-Wechsel klicken, statt die Dashboard-Seite direkt zu
    verlassen."""
    assert "function syncUrlWithPeriod(range, offset) {" in JS
    assert "history.replaceState(null, '', url);" in JS
    assert "history.pushState" not in JS


def test_set_range_go_back_and_go_forward_all_sync_the_url() -> None:
    assert "this.offset = 0;\n        syncUrlWithPeriod(this.range, this.offset);\n        this.load();" in JS
    assert "goBack() { this.offset -= 1; syncUrlWithPeriod(this.range, this.offset); this.load(); }" in JS
    go_forward = JS.split("goForward() {", 1)[1][:200]
    assert "syncUrlWithPeriod(this.range, this.offset);" in go_forward


ROOT = Path(__file__).resolve().parents[1]
REPORT_TEMPLATE = (ROOT / "app/templates/_energiedashboard_report.html").read_text(encoding="utf-8")
ROUTES_SOURCE = (ROOT / "app/energiedashboard_routes.py").read_text(encoding="utf-8")


def test_report_back_link_carries_its_own_range_and_offset() -> None:
    """Nutzerwunsch: 'zurück zum Energiedashboard' aus dem Bericht heraus
    soll denselben Zeitraum zeigen, aus dem der Bericht geöffnet wurde —
    nicht die Standardansicht."""
    assert '<a href="{{ base }}/energiedashboard?range={{ range }}&offset={{ offset }}">' in REPORT_TEMPLATE


def test_report_route_passes_offset_into_the_template_context() -> None:
    # offset wurde vorher NICHT in den Kontext aufgenommen (nur range) —
    # ohne das hätte der Link oben immer "offset=" (leer) gerendert.
    assert '"range": range,\n                    "offset": offset,' in ROUTES_SOURCE
