"""Tabellenkachel: die Beschriftungsspalte darf nicht mitten im Wort brechen.

Wertezellen sind nowrap, die Beschriftung hatte `word-break: break-word` und damit
eine Mindestbreite von einem Zeichen. Bei zu schmaler Kachel (oder breiten Werten)
verteilte das Auto-Tabellenlayout die Breite so, dass „Spülmaschine" als Streifen
mit ein bis zwei Buchstaben je Zeile erschien. Die Kachel scrollt waagerecht.
"""
import re

from _paths import STATIC

SEITEN_CSS = ["entities.css", "dashboard_detail.css"]


def test_first_column_of_tile_tables_keeps_words_together():
    for name in SEITEN_CSS:
        css = (STATIC / "css" / "pages" / name).read_text(encoding="utf-8")
        regel = re.search(
            r"\.dtile-mini-table th:first-child,\.dtile-mini-table td:first-child\{([^}]*)\}", css
        )
        assert regel, name
        assert "word-break:normal" in regel.group(1) and "overflow-wrap:normal" in regel.group(1), name
        # Die Ausnahme muss NACH der allgemeinen break-word-Regel stehen, sonst gewinnt diese.
        assert css.index("td:first-child{word-break:normal") > css.index("word-break:break-word"), name
