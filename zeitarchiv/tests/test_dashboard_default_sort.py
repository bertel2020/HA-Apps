"""Das Standard-Dashboard bleibt bei jeder Kachelsortierung ganz vorn."""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT ))

from app.storage.index import Index


TEMPLATE = (ROOT / "app/templates/dashboards.html").read_text(encoding="utf-8")
CARD_BROWSER = (ROOT / "app/static/js/card-browser.js").read_text(encoding="utf-8")


def test_default_dashboard_is_marked_as_fixed_first_for_browser_sorting() -> None:
    assert 'data-sort-first="{{ 1 if d.is_default else 0 }}"' in TEMPLATE
    default_criterion = "b.immerErste - a.immerErste"
    favorite_criterion = "b.favorit - a.favorit"
    assert default_criterion in CARD_BROWSER
    assert CARD_BROWSER.index(default_criterion) < CARD_BROWSER.index(favorite_criterion)


def test_index_keeps_new_default_first_even_if_another_dashboard_is_favorite(tmp_path: Path) -> None:
    index = Index(tmp_path / "index.sqlite")
    favorite_id = index.create_dashboard("Favorit")
    default_id = index.create_dashboard("Standard")
    index.set_dashboard_favorite(favorite_id, True)
    assert index.set_default_dashboard(default_id)

    dashboards = index.list_dashboards()
    assert dashboards[0]["id"] == default_id
    assert dashboards[0]["is_default"] == 1
    index.close()
