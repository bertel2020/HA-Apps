"""Verwendungsanzeige in geöffneten Chart- und Tabellenansichten."""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.storage.index import Index


PARTIAL = (ROOT / "app/templates/_dashboard_usage.html").read_text(encoding="utf-8")
CHART_EDITOR = (ROOT / "app/templates/chart_editor.html").read_text(encoding="utf-8")
TABLE_EDITOR = (ROOT / "app/templates/table_editor.html").read_text(encoding="utf-8")


def test_usage_partial_follows_app_chip_and_popover_patterns() -> None:
    assert 'class="chip dashboard-usage-link"' in PARTIAL
    assert 'class="chip menu-btn"' in PARTIAL
    assert 'class="menu-popover menu-popover-narrow dashboard-usage-popover"' in PARTIAL
    assert 'class="menu-row dashboard-usage-row"' in PARTIAL
    assert "keinem Dashboard" in PARTIAL


def test_saved_chart_and_table_views_include_usage_partial() -> None:
    assert '{% if chart_id %}{% include "_dashboard_usage.html" %}{% endif %}' in CHART_EDITOR
    assert '{% if table_id %}{% include "_dashboard_usage.html" %}{% endif %}' in TABLE_EDITOR


def test_item_dashboard_usage_is_default_first_then_alphabetical(tmp_path: Path) -> None:
    index = Index(tmp_path / "index.sqlite")
    chart_id = index.create_saved_chart("Verbrauch", ["sensor.test"], "day", False)
    alpha_id = index.create_dashboard("Alpha")
    default_id = index.create_dashboard("Zulu")
    index.pin_item_to_dashboard(alpha_id, "chart", chart_id)
    index.pin_item_to_dashboard(default_id, "chart", chart_id)
    assert index.set_default_dashboard(default_id)

    usage = index.list_item_dashboards("chart", chart_id)
    assert [row["id"] for row in usage] == [default_id, alpha_id]
    index.close()
