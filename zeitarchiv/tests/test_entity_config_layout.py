"""Regressionstests für das responsive Layout der Entitätskonfiguration."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "app" / "templates" / "entity_config.html"


def test_entity_configuration_uses_the_full_app_width() -> None:
    source = TEMPLATE.read_text(encoding="utf-8")
    assert ".config-card{max-width:none;margin:20px 0 0;}" in source
    assert "#config-form{" in source
    assert "grid-template-columns:repeat(2,minmax(0,1fr))" in source
    assert "#config-form .field select{width:100%;min-width:0;}" in source


def test_entity_data_actions_are_equal_and_responsive() -> None:
    source = TEMPLATE.read_text(encoding="utf-8")
    assert '<div class="entity-data-actions">' in source
    assert ".entity-data-actions{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px;}" in source
    assert "#config-form,.entity-data-actions{grid-template-columns:1fr;}" in source
    assert 'class="btn btn-danger-outline" id="delete-all-values-btn"' in source
    assert 'class="btn btn-danger" id="delete-entity-btn"' in source
