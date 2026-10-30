"""Adresse der Zeitarchiv-Seite in Home Assistant (für den Link der Karte)."""

from __future__ import annotations

import io
import json
import urllib.error

import pytest

from app import supervisor_stats


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setattr(supervisor_stats, "_panel_link", {"value": None, "failed_at": None})


class _Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _info(slug) -> _Response:
    return _Response(json.dumps({"result": "ok", "data": {"slug": slug}}).encode())


def test_the_link_is_the_panel_path_of_the_add_on(monkeypatch) -> None:
    monkeypatch.setenv("SUPERVISOR_TOKEN", "t")
    calls = []
    monkeypatch.setattr(supervisor_stats.urllib.request, "urlopen", lambda request, timeout: calls.append(request.full_url) or _info("a0d7b954_zeitarchiv"))
    assert supervisor_stats.panel_link() == "/hassio/ingress/a0d7b954_zeitarchiv"
    assert supervisor_stats.panel_link() == "/hassio/ingress/a0d7b954_zeitarchiv"
    assert calls == ["http://supervisor/addons/self/info"]  # ein Erfolg bleibt für die Laufzeit


def test_without_a_supervisor_there_is_no_link(monkeypatch) -> None:
    monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
    assert supervisor_stats.panel_link() is None


@pytest.mark.parametrize("slug", ["", None, 5, "a/b", "../x", "a b", "a?x=1"])
def test_a_strange_name_is_never_turned_into_a_link(monkeypatch, slug) -> None:
    monkeypatch.setenv("SUPERVISOR_TOKEN", "t")
    monkeypatch.setattr(supervisor_stats.urllib.request, "urlopen", lambda request, timeout: _info(slug))
    assert supervisor_stats.panel_link() is None


def test_a_failure_is_retried_only_after_a_pause(monkeypatch) -> None:
    monkeypatch.setenv("SUPERVISOR_TOKEN", "t")
    calls = []

    def fail(request, timeout):
        calls.append(1)
        raise urllib.error.URLError("weg")

    monkeypatch.setattr(supervisor_stats.urllib.request, "urlopen", fail)
    now = [1000.0]
    monkeypatch.setattr(supervisor_stats.time, "monotonic", lambda: now[0])
    assert supervisor_stats.panel_link() is None
    assert supervisor_stats.panel_link() is None
    assert len(calls) == 1
    now[0] += supervisor_stats.PANEL_LINK_RETRY_SECONDS + 1
    monkeypatch.setattr(supervisor_stats.urllib.request, "urlopen", lambda request, timeout: _info("x_zeitarchiv"))
    assert supervisor_stats.panel_link() == "/hassio/ingress/x_zeitarchiv"
