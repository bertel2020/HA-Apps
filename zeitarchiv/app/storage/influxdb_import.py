"""InfluxDB-Rohdaten importieren (einmaliger Backfill, InfluxDB 1.x): liest die
von der Home-Assistant-Integration ``influxdb`` (``api_version: 1``)
geschriebene Historie über die InfluxQL-HTTP-API (``/query``) ein — Pendant zu
vm_import.py für ein eigenständiges InfluxDB.

Datenlage (gegen eine echte InfluxDB 1.8 geprüft, nicht nur der Doku
entnommen): Die Integration legt je Zustandswechsel einen Punkt an.

- Measurement: ``override_measurement`` → ``measurement_attr`` (Standard: die
  Einheit, z. B. ``W``, ``%``) → ``default_measurement`` → volle Entity-ID. Eine
  Entität kann deshalb in MEHREREN Measurements liegen (Einheitenwechsel), und
  es gibt keine Abfrage "alle Measurements durchsuchen" — der Regex-``FROM /.*/``
  ersetzt sie.
- Tags: ``domain`` und ``entity_id``; ``entity_id`` enthält nur den
  Objektteil OHNE Domain (``sensor.wohnzimmer_temperatur`` →
  ``domain=sensor``, ``entity_id=wohnzimmer_temperatur``). Alle weiteren Tags
  (``friendly_name`` mit ``tags_attributes``, ``source`` …) sind für die
  Zuordnung belanglos: wird eine Entität umbenannt, entsteht eine neue Serie
  mit demselben ``domain``/``entity_id`` — hier wird deshalb nie nach Serie
  getrennt, sondern nach Zeitstempel zu einer Zeitreihe zusammengeführt.
- Felder: Zahl → ``value``; Schalterzustände (on/off, open/closed …) →
  ``value`` 1/0 UND ``state`` (Text); sonstiger Text → nur ``state``.
  ``unknown``/``unavailable`` werden gar nicht erst geschrieben.

Zustands-Entitäten (Typ "state") werden über das Textfeld ``state`` und den
Zustandsindex abgebildet (``ha_import.state_mapper_for()``), alle anderen über
das Zahlenfeld ``value``.

Abruf: ``chunked=true`` liefert die Antwort in Teilen (``chunk_size`` Punkte
je Zeile) und wird zeilenweise gelesen — gemessen 3 Mio Punkte in 1,9 s statt
10,4 s mit ``LIMIT``/``OFFSET``. Zusätzlich wird in Zeitfenstern abgefragt,
damit auch eine mehrjährige Historie nie als eine einzige Abfrage läuft.

Gibt wie vm_import.py bereits geparste (ts, value)-Zeilen zurück, die
unverändert an symcon_import.py::plan_import_rows()/import_rows()
weitergereicht werden; die Ergebnistypen sind die aus ha_import.py.

InfluxDB 2.x ist hier NICHT abgedeckt (Token statt Benutzer/Passwort, Zugriff
über ein DBRP-Mapping) — die Abfrageschicht wäre dieselbe."""

from __future__ import annotations

import base64
import json
import logging
import math
import re
import ssl
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Iterator
from datetime import datetime, timedelta

from ..i18n import tr
from ..limits import MAX_IMPORT_ROWS_PER_ENTITY
from .ha_import import EntityAvailability, HistoryFetchResult

# Wie vm_import.py: nur DEBUG, Fehler meldet die aufrufende Route.
logger = logging.getLogger(__name__)

# Hostname des Community-Add-ons (Slug mit Bindestrich); der Repo-Hash ist
# installationsabhängig und nicht geprüft, deshalb muss die Adresse
# überschreibbar sein.
DEFAULT_BASE_URL = "http://a0d7b954-influxdb:8086"
DEFAULT_DATABASE = "homeassistant"
REQUEST_TIMEOUT = 60
# Ein Jahr je Abfrage: das Fenster ist nicht der Engpass (siehe Docstring), es
# begrenzt nur, wie viel eine einzelne Abfrage durchsuchen muss.
HISTORY_CHUNK = timedelta(days=365)
# Punkte je Antwortzeile bei chunked=true (InfluxDB-Standard ist 10 000).
CHUNK_SIZE = 20_000
# Verfügbarkeit: so viele Entitäten je Abfrage (Regex auf den Tag entity_id).
ENTITY_BATCH_SIZE = 40

VALUE_FIELD = "value"
STATE_FIELD = "state"


class InfluxApiError(RuntimeError):
    """InfluxDB nicht erreichbar oder die Anfrage schlägt fehl."""


def normalize_base_url(base_url: str | None) -> str:
    url = (base_url or "").strip() or DEFAULT_BASE_URL
    if "://" not in url:
        url = "http://" + url
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise InfluxApiError(tr("Ungültige InfluxDB-Adresse"))
    return url.rstrip("/")


def split_entity_id(entity_id: str) -> tuple[str, str]:
    """``sensor.foo`` → (``sensor``, ``foo``): Domain und Objektteil stehen in
    InfluxDB in zwei getrennten Tags."""
    domain, _, object_id = entity_id.partition(".")
    if not domain or not object_id:
        raise ValueError(tr("Ungültige Entitäts-ID: {entity_id}", entity_id=entity_id))
    return domain, object_id


def _quote(value: str) -> str:
    """Zeichenkette als InfluxQL-Literal in einfachen Anführungszeichen."""
    return "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"


def _regex_escape(value: str) -> str:
    # Nur für das /…/-Literal von InfluxQL: Schrägstrich und Regex-Zeichen.
    return re.sub(r"([\\/.^$*+?()\[\]{}|])", r"\\\1", value)


def _request(
    base_url: str,
    database: str,
    query: str,
    auth: tuple[str, str] | None,
    extra: dict[str, str] | None = None,
) -> urllib.request.Request:
    """GET /query — Zugangsdaten nie in der URL, nur als Authorization-Header."""
    params = {"db": database, "q": query, "epoch": "ms"}
    if extra:
        params.update(extra)
    url = normalize_base_url(base_url) + "/query?" + urllib.parse.urlencode(params)
    headers = {"Accept": "application/json", "User-Agent": "Zeitarchiv/InfluxImport"}
    if auth and (auth[0] or auth[1]):
        raw = f"{auth[0]}:{auth[1]}".encode()
        headers["Authorization"] = "Basic " + base64.b64encode(raw).decode()
    return urllib.request.Request(url, headers=headers)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Folgt keiner Weiterleitung: urllib schickt den Authorization-Header sonst
    auch an den neuen Host mit, und die Zugangsdaten gehören nur zur
    eingegebenen Adresse."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


def _opener(verify_tls: bool) -> urllib.request.OpenerDirector:
    if verify_tls:
        return urllib.request.build_opener(_NoRedirect)
    # Selbstsigniertes Zertifikat (typisch bei einer eigenen InfluxDB): nur auf
    # ausdrücklichen Wunsch ungeprüft, siehe Schalter in der Oberfläche.
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return urllib.request.build_opener(_NoRedirect, urllib.request.HTTPSHandler(context=context))


def _error_for(exc: urllib.error.HTTPError) -> InfluxApiError:
    try:
        detail = exc.read().decode("utf-8", errors="replace").strip()[:200]
    except OSError:
        detail = ""
    if exc.code == 401:
        return InfluxApiError(tr("InfluxDB lehnt die Zugangsdaten ab (Benutzer oder Passwort falsch)"))
    return InfluxApiError(
        tr("InfluxDB antwortete mit Fehler {code}", code=exc.code) + (f": {detail}" if detail else "")
    )


def _open_lines(request: urllib.request.Request, verify_tls: bool = True) -> Iterator[bytes]:
    """Antwort zeilenweise: bei chunked=true ist jede Zeile ein eigenes
    JSON-Dokument, sonst steht die ganze Antwort in einer Zeile. Nie den
    Authorization-Header loggen."""
    logger.debug("InfluxDB-Anfrage · %s", request.selector.split("?", 1)[0])
    try:
        with _opener(verify_tls).open(request, timeout=REQUEST_TIMEOUT) as response:
            yield from response
    except urllib.error.HTTPError as exc:
        # Vor URLError prüfen (Unterklasse), sonst ginge der Statuscode verloren.
        raise _error_for(exc) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise InfluxApiError(
            tr("InfluxDB nicht erreichbar: {v}", v=exc.reason if isinstance(exc, urllib.error.URLError) else exc)
        ) from exc


def _results(lines: Iterator[bytes]) -> Iterator[dict]:
    """Die ``results``-Einträge aller Antwortzeilen; Fehler von InfluxDB
    (häufig mit Status 200 ausgeliefert) werden zu InfluxApiError."""
    for line in lines:
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except ValueError as exc:
            raise InfluxApiError(tr("InfluxDB lieferte keine gültige Antwort")) from exc
        if not isinstance(payload, dict):
            raise InfluxApiError(tr("InfluxDB lieferte keine gültige Antwort"))
        if payload.get("error"):
            raise InfluxApiError(_explain(str(payload["error"])))
        for result in payload.get("results") or []:
            if isinstance(result, dict) and result.get("error"):
                raise InfluxApiError(_explain(str(result["error"])))
            yield result


def _explain(message: str) -> str:
    if "database not found" in message.lower():
        return tr("Die Datenbank wurde in InfluxDB nicht gefunden. Bitte den Namen prüfen: {message}", message=message)
    return tr("InfluxDB meldet: {message}", message=message[:200])


def _clean_value(value: object) -> float | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    value = float(value)
    return round(value, 3) if math.isfinite(value) else None


def _entity_filter(entity_id: str) -> str:
    domain, object_id = split_entity_id(entity_id)
    return f'"domain" = {_quote(domain)} AND "entity_id" = {_quote(object_id)}'


def fetch_history_rows(
    entity_id: str,
    start: datetime,
    end: datetime,
    base_url: str | None = None,
    database: str | None = None,
    auth: tuple[str, str] | None = None,
    verify_tls: bool = True,
    state_mapper: Callable[[str, float], float | None] | None = None,
    max_rows: int = MAX_IMPORT_ROWS_PER_ENTITY,
) -> HistoryFetchResult:
    """Liest die Rohpunkte einer Entität zeitfensterweise über GET /query.
    Mehrere Serien und Measurements derselben Entität (Umbenennung,
    Einheitenwechsel, siehe Moduldocstring) werden nach Zeitstempel
    zusammengeführt; gleichzeitige Punkte kollabieren auf den ersten, damit
    der generische Importkern strikt aufsteigende Zeitstempel bekommt.

    Mit ``state_mapper`` (Zustands-Entität) wird das Textfeld ``state``
    gelesen, sonst das Zahlenfeld ``value``."""
    field = STATE_FIELD if state_mapper is not None else VALUE_FIELD
    result = HistoryFetchResult()
    points: dict[float, float] = {}
    where = _entity_filter(entity_id)
    window_start = start
    while window_start < end:
        window_end = min(window_start + HISTORY_CHUNK, end)
        query = (
            f'SELECT "{field}" FROM /.*/ WHERE {where} '
            f"AND time >= {int(window_start.timestamp() * 1000)}ms "
            f"AND time < {int(window_end.timestamp() * 1000)}ms"
        )
        request = _request(
            base_url or DEFAULT_BASE_URL, database or DEFAULT_DATABASE, query, auth,
            {"chunked": "true", "chunk_size": str(CHUNK_SIZE)},
        )
        for res in _results(_open_lines(request, verify_tls)):
            for series in res.get("series") or []:
                for stamp, raw in series.get("values") or []:
                    if state_mapper is not None:
                        ts = stamp / 1000.0
                        value = state_mapper(raw, ts) if isinstance(raw, str) else None
                    else:
                        value = _clean_value(raw)
                    if value is None:
                        result.skipped += 1
                        result.discarded.append({"reason": tr("Wert ist nicht importierbar"), "timestamp_ms": stamp})
                        continue
                    ts = stamp / 1000.0
                    if ts in points:
                        result.discarded.append(
                            {"reason": tr("Zeitstempel ist doppelt oder nicht aufsteigend"), "timestamp_ms": stamp}
                        )
                        continue
                    points[ts] = value
                    if len(points) > max_rows:
                        raise ValueError(
                            tr(
                                "InfluxDB-Historie enthält mehr als {limit} Datenpunkte",
                                limit=f"{max_rows:,}".replace(",", "."),
                            )
                        )
        window_start = window_end
    result.rows = sorted(points.items())
    return result


def fetch_availability(
    entity_ids: list[str],
    start: datetime,
    end: datetime,
    base_url: str | None = None,
    database: str | None = None,
    auth: tuple[str, str] | None = None,
    verify_tls: bool = True,
    state_entity_ids: set[str] | None = None,
) -> dict[str, EntityAvailability]:
    """Verfügbarkeits-Vorschau für die Auswahltabelle: je Batch drei
    Abfragen (Anzahl, erster, letzter Zeitpunkt über das gesamte Fenster)
    statt eines Abrufs je Entität. Anzahl und Zeitpunkte stehen in getrennten
    Abfragen, weil InfluxDB bei count() zusammen mit first()/last() den
    Zeitstempel verwirft (immer 0). Serien und Measurements derselben Entität
    werden zusammengefasst (Summe bzw. frühester/spätester Punkt).
    ``state_entity_ids`` nennt die Zustands-Entitäten, deren Zeitreihe im
    Textfeld ``state`` liegt."""
    state_entity_ids = state_entity_ids or set()
    result = {eid: EntityAvailability(eid) for eid in entity_ids}
    time_clause = (
        f"time >= {int(start.timestamp() * 1000)}ms AND time < {int(end.timestamp() * 1000)}ms"
    )
    for field, ids in (
        (VALUE_FIELD, [e for e in entity_ids if e not in state_entity_ids]),
        (STATE_FIELD, [e for e in entity_ids if e in state_entity_ids]),
    ):
        for batch_start in range(0, len(ids), ENTITY_BATCH_SIZE):
            batch = ids[batch_start : batch_start + ENTITY_BATCH_SIZE]
            wanted: dict[tuple[str, str], str] = {}
            for eid in batch:
                domain, object_id = split_entity_id(eid)
                wanted[(domain, object_id)] = eid
            domains = "|".join(sorted({_regex_escape(d) for d, _ in wanted}))
            objects = "|".join(sorted({_regex_escape(o) for _, o in wanted}))
            where = f'"domain" =~ /^({domains})$/ AND "entity_id" =~ /^({objects})$/ AND {time_clause}'
            queries = {
                "count": f'SELECT count("{field}") FROM /.*/ WHERE {where} GROUP BY "domain", "entity_id"',
                "first": f'SELECT first("{field}") FROM /.*/ WHERE {where} GROUP BY "domain", "entity_id"',
                "last": f'SELECT last("{field}") FROM /.*/ WHERE {where} GROUP BY "domain", "entity_id"',
            }
            found: dict[str, dict[str, float]] = {}
            for key, query in queries.items():
                request = _request(base_url or DEFAULT_BASE_URL, database or DEFAULT_DATABASE, query, auth)
                for res in _results(_open_lines(request, verify_tls)):
                    for series in res.get("series") or []:
                        tags = series.get("tags") or {}
                        eid = wanted.get((tags.get("domain"), tags.get("entity_id")))
                        if eid is None:
                            continue  # Domain und Objektteil passen nur einzeln zur Auswahl
                        for row in series.get("values") or []:
                            try:
                                # count: Spalte 1 ist die Anzahl (Zeit immer 0);
                                # first/last: Spalte 0 ist der Zeitpunkt des
                                # Punkts, Spalte 1 sein Wert (bei state ein Text).
                                stamp = float(row[0])
                                number = float(row[1]) if key == "count" else 0.0
                            except (IndexError, TypeError, ValueError):
                                continue
                            entry = found.setdefault(eid, {})
                            if key == "count":
                                entry["count"] = entry.get("count", 0.0) + number
                            elif key == "first":
                                entry["first"] = min(entry.get("first", math.inf), stamp / 1000.0)
                            else:
                                entry["last"] = max(entry.get("last", -math.inf), stamp / 1000.0)
            for eid in batch:
                data = found.get(eid)
                if not data or not data.get("count"):
                    continue
                avail = result[eid]
                avail.count = int(data["count"])
                avail.first_ts = data.get("first")
                avail.last_ts = data.get("last")
    return result
