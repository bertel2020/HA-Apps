"""Tests für app/healthcheck.py — Docker-HEALTHCHECK-Skript."""

from __future__ import annotations

import sys
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import healthcheck


def test_returns_zero_when_health_endpoint_responds_ok(monkeypatch) -> None:
    monkeypatch.setattr(healthcheck.urllib.request, "urlopen", lambda url, timeout: None)
    assert healthcheck.main() == 0


def test_treats_401_as_healthy_since_it_proves_the_process_and_lock_respond(monkeypatch) -> None:
    def fake_urlopen(url, timeout):
        raise urllib.error.HTTPError(url, 401, "Unauthorized", None, None)

    monkeypatch.setattr(healthcheck.urllib.request, "urlopen", fake_urlopen)
    assert healthcheck.main() == 0


def test_treats_other_http_errors_as_unhealthy(monkeypatch) -> None:
    def fake_urlopen(url, timeout):
        raise urllib.error.HTTPError(url, 503, "Service Unavailable", None, None)

    monkeypatch.setattr(healthcheck.urllib.request, "urlopen", fake_urlopen)
    assert healthcheck.main() == 1


def test_treats_timeout_or_connection_error_as_unhealthy(monkeypatch) -> None:
    def fake_urlopen(url, timeout):
        raise TimeoutError("timed out")

    monkeypatch.setattr(healthcheck.urllib.request, "urlopen", fake_urlopen)
    assert healthcheck.main() == 1
