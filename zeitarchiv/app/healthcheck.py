"""Docker-HEALTHCHECK für Zeitarchiv — kein eigenständiges Modul der App,
wird nur von Docker als Subprozess aufgerufen (siehe Dockerfile).

Kein curl/wget im schlanken python:3.12-slim-Image, deshalb reines Python
über die Standardbibliothek. Fragt /api/health ohne Token ab — eine 401
("nicht autorisiert") gilt hier als gesund, weil sie beweist, dass der
Prozess antwortet UND das Index-Lock zur Token-Prüfung (check_auth() ->
_current_api_token() -> ensure_api_token(index)) frei war. Nur ein Timeout,
ein Verbindungsfehler oder ein 5xx gelten als ungesund. Ein eigener,
unauthentifizierter Healthcheck-Endpunkt würde dieselbe Aussage treffen,
aber unnötig neue Angriffsfläche schaffen.
"""

from __future__ import annotations

import sys
import urllib.error
import urllib.request

URL = "http://127.0.0.1:8127/api/health"
TIMEOUT_SECONDS = 8


def main() -> int:
    try:
        urllib.request.urlopen(URL, timeout=TIMEOUT_SECONDS)
    except urllib.error.HTTPError as exc:
        return 0 if exc.code == 401 else 1
    except Exception:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
