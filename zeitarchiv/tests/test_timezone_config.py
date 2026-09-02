"""Tests für die startfeste Auflösung der Add-on-Zeitzone."""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT ))

from app.timezone_config import DEFAULT_TIMEZONE_NAME, load_timezone  # noqa: E402


def test_valid_option_is_used() -> None:
    assert str(load_timezone({"timezone": "UTC"}, environment={})) == "UTC"


def test_environment_override_is_used_without_option() -> None:
    assert str(
        load_timezone({}, environment={"ZEITARCHIV_TIMEZONE": "Europe/London"})
    ) == "Europe/London"


def test_unknown_timezone_falls_back_and_reports_error() -> None:
    messages: list[str] = []
    timezone = load_timezone(
        {"timezone": "Europe/Definitely-Not-A-Zone"},
        environment={},
        on_invalid=messages.append,
    )

    assert str(timezone) == DEFAULT_TIMEZONE_NAME
    assert len(messages) == 1
    assert "Definitely-Not-A-Zone" in messages[0]
    assert "Fallback" in messages[0]


def test_non_string_manual_value_cannot_abort_startup() -> None:
    messages: list[str] = []
    timezone = load_timezone(
        {"timezone": 123}, environment={}, on_invalid=messages.append
    )

    assert str(timezone) == DEFAULT_TIMEZONE_NAME
    assert messages


def test_supervisor_schema_rejects_arbitrary_timezone_strings() -> None:
    config = (ROOT  / "config.yaml").read_text(encoding="utf-8")
    assert 'timezone: "match(' in config
    assert 'timezone: "str"' not in config
