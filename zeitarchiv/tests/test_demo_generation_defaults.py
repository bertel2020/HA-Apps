"""Zeitraum der Demo-Daten-Generierung: 3 Jahre statt vormals 6 Monate
(Nutzerwunsch). Betrifft ausschließlich den In-App-Demo-Modus
(build_demo_worker() in app/demo_mode.py ruft run_generation() ohne eigenes
months=, übernimmt also diesen Default) — das eigenständige CLI-Skript
scripts/generate_demo_data.py hat seinen eigenen --months-Default (6) und
bleibt bewusst unverändert.
"""

import inspect

from app.demo_generation import run_generation


def test_the_app_generates_three_years_of_history_by_default() -> None:
    months_default = inspect.signature(run_generation).parameters["months"].default
    assert months_default == 36
