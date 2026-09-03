"""Seiten-Smoke-Tests über die echte FastAPI-App (siehe conftest.py).

Anders als die restliche Suite (die Module/Templates isoliert testet) geht
das hier end-to-end durch Route-Handler, Context-Builder und Template —
die einzige Ebene, die z. B. eine falsche Funktionssignatur in einem
Context-Builder wie collect_notices() beim Aufruf durch einen Route-Handler
zuverlässig fängt.
"""

from __future__ import annotations

import pytest

PAGES = ["/", "/entities", "/statistik", "/housekeeping", "/settings", "/import"]


@pytest.mark.parametrize("path", PAGES)
def test_page_loads(client, path) -> None:
    resp = client.get(path)
    assert resp.status_code == 200
