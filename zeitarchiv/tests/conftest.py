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
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
_TEST_DATA_DIR = Path(tempfile.mkdtemp(prefix="zeitarchiv-pytest-"))
os.environ.setdefault("ZEITARCHIV_DATA_DIR", str(_TEST_DATA_DIR))

import pytest
from starlette.testclient import TestClient


@pytest.fixture(scope="session")
def client():
    from app.main import app

    with TestClient(app) as c:
        yield c
