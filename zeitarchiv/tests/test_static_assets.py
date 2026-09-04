"""Tests für die Auslieferung von /static/ (Cache-Control, Kompression,
Cache-Busting) — siehe die "GUI-Performance"-Vorschläge (GZipMiddleware,
_CachedStaticFiles, vendor_v in main.py)."""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

TEMPLATES_DIR = Path(__file__).resolve().parents[1] / "app" / "templates"
VENDOR_SCRIPT_RE = re.compile(r'src="[^"]*vendor/(?:htmx|echarts|alpine)\.min\.js([^"]*)"')


def test_static_assets_get_long_immutable_cache_control(client) -> None:
    resp = client.get("static/css/app.css")
    assert resp.status_code == 200
    assert resp.headers["Cache-Control"] == "public, max-age=31536000, immutable"


def test_static_assets_still_send_last_modified_and_etag(client) -> None:
    """Cache-Control ergänzt, ersetzt aber nicht die bestehende
    Last-Modified/ETag-Validierung (relevant, falls ein Client den langen
    Cache ignoriert oder erzwungen neu lädt)."""
    resp = client.get("static/css/app.css")
    assert "Last-Modified" in resp.headers
    assert "ETag" in resp.headers


def test_gzip_compresses_large_static_assets(client) -> None:
    resp = client.get("static/vendor/echarts.min.js", headers={"Accept-Encoding": "gzip"})
    assert resp.status_code == 200
    assert resp.headers.get("Content-Encoding") == "gzip"


def test_gzip_skips_small_responses(client) -> None:
    """minimum_size=500 — eine sehr kleine Antwort soll nicht komprimiert
    werden (Kompressions-Overhead lohnt sich dort nicht)."""
    resp = client.get("static/js/dd-picker.js", headers={"Accept-Encoding": "gzip"})
    assert resp.status_code == 200
    content_length = (Path(__file__).resolve().parents[1] / "app" / "static" / "js" / "dd-picker.js").stat().st_size
    if content_length < 500:
        assert resp.headers.get("Content-Encoding") != "gzip"


def test_html_responses_are_compressed_too(client) -> None:
    """GZipMiddleware soll nicht nur static/, sondern auch normale
    HTML-Seiten treffen."""
    resp = client.get("/entities", headers={"Accept-Encoding": "gzip"})
    assert resp.status_code == 200
    assert resp.headers.get("Content-Encoding") == "gzip"


def test_all_vendor_script_references_carry_a_cache_busting_version() -> None:
    """htmx/echarts/alpine trugen bisher keinen ?v=-Parameter — einzige
    Lücke, die den langen Cache-Control von _CachedStaticFiles unsicher
    gemacht hätte (siehe main.py, vendor_v)."""
    checked = 0
    for path in TEMPLATES_DIR.glob("*.html"):
        text = path.read_text(encoding="utf-8")
        for match in VENDOR_SCRIPT_RE.finditer(text):
            checked += 1
            assert "?v={{ vendor_v }}" in match.group(0), f"{path.name}: {match.group(0)!r} ohne vendor_v"
    assert checked >= 18, f"Erwartete mindestens 18 Vendor-Script-Referenzen, gefunden: {checked}"


def _run_all() -> None:
    tests = [obj for name, obj in globals().items() if name.startswith("test_")]
    for test in tests:
        if "client" in test.__code__.co_varnames[: test.__code__.co_argcount]:
            continue  # braucht die pytest-Fixture, nicht direkt ausführbar
        test()
        print(f"ok  {test.__name__}")
    print(f"\n{len(tests)} Tests geprüft.")


if __name__ == "__main__":
    _run_all()
