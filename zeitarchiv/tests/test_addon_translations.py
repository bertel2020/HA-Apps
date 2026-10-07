"""Übersetzungen der Add-on-Konfiguration (translations/*.yaml) passen zu config.yaml."""

from __future__ import annotations

import pytest
import yaml

from _paths import ADDON

CONFIG = yaml.safe_load((ADDON / "config.yaml").read_text(encoding="utf-8"))


@pytest.mark.parametrize("language", ["de", "en"])
def test_every_option_and_port_has_a_translation(language: str) -> None:
    translation = yaml.safe_load((ADDON / "translations" / f"{language}.yaml").read_text(encoding="utf-8"))
    assert set(translation["configuration"]) == set(CONFIG["options"])
    for texts in translation["configuration"].values():
        assert texts["name"].strip() and texts["description"].strip()
    assert set(translation["network"]) == set(CONFIG["ports"])
