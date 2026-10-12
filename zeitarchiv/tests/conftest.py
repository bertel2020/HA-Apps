"""Nur für Tests, die die FastAPI-App tatsächlich starten (test_routes.py) —
der Rest der Suite testet einzelne Module direkt und braucht das nicht.

app/main.py liest ZEITARCHIV_DATA_DIR beim Modul-Import einmalig in die
globale DATA_DIR-Konstante ein — muss deshalb VOR dem ersten
"from app.main import app" gesetzt sein. conftest.py wird von pytest
garantiert vor allen Testmodulen geladen. Eigenes, frisches Verzeichnis pro
Testlauf — nie /data oder demo-data, damit Tests nichts Reales berühren.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

# Der Import allein genügt: _paths hängt das Verzeichnis, das `app/` enthält,
# an sys.path — in beiden Baum-Layouts. conftest wird von pytest vor allen
# Testmodulen geladen, damit gilt das für die ganze Suite.
import _paths  # noqa: F401

_TEST_DATA_DIR = Path(tempfile.mkdtemp(prefix="zeitarchiv-pytest-"))
os.environ.setdefault("ZEITARCHIV_DATA_DIR", str(_TEST_DATA_DIR))

# Mehrere Tests rendern Templates mit einer eigenen, nackten jinja2.Environment
# (ohne die App). Seit die Templates `_("…")` für die Übersetzung nutzen, brauchen
# auch sie diese Funktion — hier einmal für alle, statt in jedem Test.
import jinja2  # noqa: E402

from app import i18n  # noqa: E402

_environment_init = jinja2.Environment.__init__


def _environment_with_gettext(self, *args, **kwargs):
    _environment_init(self, *args, **kwargs)
    self.globals.setdefault("_", i18n._gettext)


jinja2.Environment.__init__ = _environment_with_gettext

# Erst NACH os.environ.setdefault oben — siehe Modul-Docstring.
import pytest  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402


@pytest.fixture(scope="session")
def client():
    from app.main import app

    with TestClient(app) as c:
        yield c


@pytest.fixture(autouse=True)
def _no_background_language(monkeypatch):
    """Templates, die ein Test ohne Anfrage rendert, sind deutsch. Die Hintergrundsprache der App
    (zuletzt gesehene Browsersprache aus der Datenbank) darf aus einem früheren Test nicht hineinwirken;
    Tests dazu setzen sie selbst."""
    monkeypatch.setattr(i18n, "_background_provider", None)
    monkeypatch.setattr(i18n, "_last_seen", None)
