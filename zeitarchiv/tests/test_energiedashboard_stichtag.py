"""Gemeinsamer Stichtag von Energiefluss (Übersicht) und Reiter „Vergleich“.

Der Energiefluss meldet seinen Tag an den Store dashStichtag (announceWindow, bestand schon); umgekehrt folgt er jetzt dem Store
(followStichtag): ein im Vergleich gewählter Tag öffnet den Energiefluss im Zeitraum „Tag“, „Heute“ führt zur laufenden Periode zurück."""

from __future__ import annotations

import re
import shutil
import subprocess

import pytest

from _paths import APP

JS = (APP / "static/js/energiedashboard.js").read_text(encoding="utf-8")


def test_the_flow_follows_the_shared_stichtag() -> None:
    assert "followStichtag() {" in JS
    assert "window.addEventListener('dashboard-stichtag'" in JS
    follow = JS.split("followStichtag() {", 1)[1][:600]
    # Zeigt der Energiefluss den Tag schon, bleibt er in seiner Periode (sonst würde sein eigenes Ende eines Monats ihn auf „Tag“ zwingen).
    assert "store.date === (window.energieWindowAt || null)" in follow
    assert "this.range = 'day';" in follow
    assert "this.offset = dayOffsetFor(store.date);" in follow
    assert "syncUrlWithPeriod(this.range, this.offset);" in follow


def test_a_date_link_without_a_period_opens_that_day() -> None:
    assert "window.DashboardStichtag && !startParams.has('range') && !startParams.has('offset')" in JS


def test_the_flow_still_announces_its_window() -> None:
    assert "window.dispatchEvent(new CustomEvent('energie-window'" in JS


@pytest.mark.skipif(shutil.which("node") is None, reason="node nicht installiert")
@pytest.mark.parametrize(
    ("heute", "datum", "erwartet"),
    [
        ((2026, 11, 8), None, 0),
        ((2026, 11, 8), "2026-11-08", 0),
        ((2026, 11, 8), "2026-11-07", -1),
        ((2026, 11, 8), "2026-10-31", -8),
        ((2026, 3, 29), "2026-03-28", -1),  # Sommerzeitwechsel verschluckt keinen Tag
        ((2026, 1, 2), "2025-12-31", -2),  # Jahreswechsel
        ((2026, 11, 8), "2026-11-20", 0),  # Zukunft gibt es nicht
    ],
)
def test_day_offset_counts_calendar_days_back_from_today(heute, datum, erwartet) -> None:
    function = re.search(r"function dayOffsetFor\(dateStr\) \{.*?\n  \}\n", JS, re.S).group(0)
    y, m, d = heute
    script = (
        f"const window = {{ServerTime: {{parts: () => ({{year: {y}, month: {m}, day: {d}}})}}}};"
        f"{function}\nconsole.log(dayOffsetFor({datum!r}));".replace("None", "null")
    )
    out = subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True, timeout=30).stdout
    assert int(out) == erwartet
