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

# Erst NACH os.environ.setdefault oben — siehe Modul-Docstring.
import pytest  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402


@pytest.fixture(scope="session")
def client():
    from app.main import app

    with TestClient(app) as c:
        yield c
