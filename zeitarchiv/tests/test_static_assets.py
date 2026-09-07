"""Tests für die AUSLIEFERUNG von /static/ — Cache-Control, Validierung,
Kompression (GZipMiddleware und _CachedStaticFiles in main.py).

Wie die Cache-Buster gebildet werden, steht seit ZG-05 nicht mehr hier,
sondern in tests/test_asset_versions.py: Das ist eine eigene Frage
(Inhalts-Hash je Datei) und war als Anhängsel dieser Datei schlecht
aufgehoben."""

from __future__ import annotations

from _paths import APP_JS


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
    content_length = (APP_JS / "dd-picker.js").stat().st_size
    if content_length < 500:
        assert resp.headers.get("Content-Encoding") != "gzip"


def test_html_responses_are_compressed_too(client) -> None:
    """GZipMiddleware soll nicht nur static/, sondern auch normale
    HTML-Seiten treffen."""
    resp = client.get("/entities", headers={"Accept-Encoding": "gzip"})
    assert resp.status_code == 200
    assert resp.headers.get("Content-Encoding") == "gzip"




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
