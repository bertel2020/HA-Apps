"""Regressionstests für geglättete Linien ohne künstliche Randlücken."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENTITY = (ROOT / "app/templates/entity_detail.html").read_text(encoding="utf-8")
EDITOR = (ROOT / "app/templates/chart_editor.html").read_text(encoding="utf-8")
DASHBOARD = (ROOT / "app/static/js/dashboard-tiles.js").read_text(encoding="utf-8")


def test_all_line_chart_renderers_use_smooth_lines() -> None:
    assert "smooth: this.chartType === 'line'" in ENTITY
    assert "main.smooth = true" in EDITOR
    assert "cmp.smooth = true" in EDITOR
    assert "cfg.smooth = true" in DASHBOARD


def test_all_line_chart_renderers_extend_last_value_to_window_end() -> None:
    assert "mainData.push([this.windowEnd * 1000" in ENTITY
    assert "compareData.push([this.windowEnd * 1000" in ENTITY
    assert "mainData.push([this.windowEnd * 1000" in EDITOR
    assert "compareData.push([this.windowEnd * 1000" in EDITOR
    assert "lineData.push([data.window_end * 1000" in DASHBOARD


def test_line_charts_do_not_mix_smoothing_with_step_mode() -> None:
    assert ".step = 'end'" not in ENTITY
    assert ".step = 'end'" not in EDITOR
    assert ".step = 'end'" not in DASHBOARD
