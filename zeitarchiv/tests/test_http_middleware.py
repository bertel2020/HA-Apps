"""Charakterisierungstests für die globale HTTP-Middleware in main.py
(Security-Header + Request-Logging/X-Request-ID) — siehe ROADMAP.md, "Neu
seit 0.76.1", Punkt 2. Laufen bewusst über den echten TestClient (conftest.py),
nicht isoliert gegen eine einzelne Funktion: das Verhalten soll unabhängig
davon gleich bleiben, ob dahinter BaseHTTPMiddleware oder eine reine
ASGI-Middleware-Klasse steckt.
"""

from __future__ import annotations

import re

REQUEST_ID_RE = re.compile(r"^[0-9a-f]{12}$")


def test_security_headers_present_on_every_response(client) -> None:
    resp = client.get("/")
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["X-Frame-Options"] == "SAMEORIGIN"
    assert resp.headers["Referrer-Policy"] == "same-origin"
    assert resp.headers["Permissions-Policy"] == "camera=(), microphone=(), geolocation=()"
    assert "Content-Security-Policy" in resp.headers


def test_security_headers_present_even_on_error_responses(client) -> None:
    """Header dürfen nicht an einen Erfolgsfall gekoppelt sein — eine 404
    braucht dieselbe CSP wie eine normale Seite."""
    resp = client.get("/this-path-does-not-exist")
    assert resp.status_code == 404
    assert "Content-Security-Policy" in resp.headers
    assert resp.headers["X-Content-Type-Options"] == "nosniff"


def test_request_id_header_is_fresh_per_request(client) -> None:
    first = client.get("/")
    second = client.get("/")
    assert REQUEST_ID_RE.fullmatch(first.headers["X-Request-ID"])
    assert REQUEST_ID_RE.fullmatch(second.headers["X-Request-ID"])
    assert first.headers["X-Request-ID"] != second.headers["X-Request-ID"]


def test_request_id_header_matches_access_log_line(client, caplog) -> None:
    """request.state.request_id (main.py) und das Access-Log (logging_setup.py)
    müssen denselben Wert tragen — api_routes.py verlässt sich beim
    Korrelieren seiner eigenen Ingest-Logs auf genau dieses Feld. Ein 404
    statt einer normalen Seite, weil Erfolgsfälle im Default-Access-Modus
    ("errors") gar nicht geloggt werden (siehe DEFAULT_ACCESS_LOG_MODE)."""
    with caplog.at_level("WARNING", logger="zeitarchiv.access"):
        resp = client.get("/this-path-does-not-exist")
    assert resp.status_code == 404
    request_id = resp.headers["X-Request-ID"]
    matching = [
        record for record in caplog.records
        if "event=http_request" in record.getMessage() and f"request_id={request_id}" in record.getMessage()
    ]
    assert matching, f"Kein Access-Log-Eintrag mit request_id={request_id} gefunden"
