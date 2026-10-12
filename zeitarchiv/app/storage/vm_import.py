"""VictoriaMetrics-Rohdaten importieren (einmaliger Backfill): liest die von
der Home-Assistant-Integration ``influxdb`` in VictoriaMetrics geschriebene
Historie über deren HTTP-API ein — Pendant zu ha_import.py, aber ohne
Supervisor-Token, weil VictoriaMetrics eine eigene, direkt erreichbare API hat.

Datenlage (an der echten Instanz geprüft, nicht angenommen): Die
influxdb-Integration schreibt je Zustandswechsel einen Punkt. Mit
``measurement_attr: entity_id`` heißt die Messreihe ``<entity_id>_value``
(z. B. ``sensor.wohnzimmer_temperatur_value``) — also die VOLLE Entity-ID
inklusive Domain. Der Tag ``entity_id`` enthält dagegen nur den Objektteil
ohne Domain; deshalb matcht dieses Modul über den Metriknamen, nicht über den
Tag. ``_value`` ist der Zustand als Zahl (``on`` → 1, ``off`` → 0), identisch
zur Normalisierung des Live-Pfads. ``_state`` ist unbrauchbar: VictoriaMetrics
macht aus dem Zustandsstring eine 0 und verwirft ``unknown``/``unavailable``
nicht von allein — ``_value`` enthält solche Zustände dagegen gar nicht erst.

Wichtig für den Abruf: ``/api/v1/export`` liefert die gespeicherten Rohpunkte,
``query_range`` würde auf ein Raster interpolieren. Ändert sich ein Tag
(z. B. ``friendly_name`` nach einer Umbenennung), legt VictoriaMetrics eine
zweite Serie mit demselben Metriknamen an — der Export liefert dann mehrere
Zeilen für eine Entität, die hier zu einer Zeitreihe zusammengeführt werden.

Gibt wie ha_import.py/csv_import.py bereits geparste (ts, value)-Zeilen
zurück, die unverändert an symcon_import.py::plan_import_rows()/import_rows()
weitergereicht werden. Die Ergebnistypen (HistoryFetchResult,
EntityAvailability) sind bewusst die aus ha_import.py, damit Routen und
Importbericht beide Quellen gleich behandeln können."""

from __future__ import annotations

from ..i18n import tr

import base64
import json
import logging
import math
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta

from ..limits import MAX_IMPORT_ROWS_PER_ENTITY
from .ha_import import EntityAvailability, HistoryFetchResult

# Wie ha_import.py: nur DEBUG, Fehler meldet die aufrufende Route.
logger = logging.getLogger(__name__)

# Hostname = Add-on-Slug mit Bindestrich statt Unterstrich; der Repo-Hash im
# Slug ist installationsabhängig, deshalb muss die URL überschreibbar sein.
DEFAULT_BASE_URL = "http://1bd4a9fb-victoria-metrics:8428"
REQUEST_TIMEOUT = 30
# Der Export einer einzelnen Entität wird zeitfensterweise geholt, damit auch
# eine sehr lange Historie (retention 99y) nicht in einer einzigen Antwort
# landet. Ein Jahr statt weniger Tage: gemessen 502.209 Punkte (zwei Jahre,
# Minutentakt) in 1,5 s — das Fenster ist also nicht der Engpass, wohl aber die
# Anzahl leerer Anfragen, wenn ein Zeitraum von zehn Jahren abgefragt wird.
HISTORY_CHUNK = timedelta(days=365)
# Verfügbarkeit: so viele Messreihen-Namen pro Instant-Query.
ENTITY_BATCH_SIZE = 40
# Messreihen-Suffix der numerischen Zustandsreihe, siehe Moduldocstring.
VALUE_SUFFIX = "_value"


class VmApiError(RuntimeError):
    """VictoriaMetrics nicht erreichbar oder die Anfrage schlägt fehl."""


def normalize_base_url(base_url: str | None) -> str:
    url = (base_url or "").strip() or DEFAULT_BASE_URL
    if "://" not in url:
        url = "http://" + url
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise VmApiError(tr("Ungültige VictoriaMetrics-Adresse"))
    return url.rstrip("/")


def _request(
    base_url: str, path: str, params: dict | None, auth: tuple[str, str] | None
) -> urllib.request.Request:
    url = normalize_base_url(base_url) + path
    if params:
        # doseq: match[] kann mehrfach vorkommen
        url += "?" + urllib.parse.urlencode(params, doseq=True)
    headers = {"Accept": "application/json", "User-Agent": "Zeitarchiv/VmImport"}
    if auth:
        raw = f"{auth[0]}:{auth[1]}".encode()
        headers["Authorization"] = "Basic " + base64.b64encode(raw).decode()
    return urllib.request.Request(url, headers=headers)


def _open(request: urllib.request.Request) -> bytes:
    # Nie den Authorization-Header loggen.
    logger.debug("VM-Anfrage · %s", request.selector.split("?", 1)[0])
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        # Vor URLError prüfen (Unterklasse), sonst ginge der Statuscode verloren.
        try:
            detail = exc.read().decode("utf-8", errors="replace").strip()[:200]
        except OSError:
            detail = ""
        raise VmApiError(
            tr("VictoriaMetrics antwortete mit Fehler {code}", code=exc.code) + (f": {detail}" if detail else "")
        ) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise VmApiError(
            tr("VictoriaMetrics nicht erreichbar: {v}", v=exc.reason if isinstance(exc, urllib.error.URLError) else exc)
        ) from exc


def _selector_name(entity_id: str) -> str:
    return entity_id + VALUE_SUFFIX


def _regex_escape(name: str) -> str:
    # Entity-IDs enthalten nur [a-z0-9_.]; im Regex muss nur der Punkt maskiert
    # werden, sonst würde er beliebige Zeichen treffen. Als Zeichenklasse statt
    # "\.": der Selektor steckt in einem Query-String-Literal, in dem
    # MetricsQL ein einfaches Backslash-Escape als Syntaxfehler ablehnt.
    return name.replace(".", "[.]")


def _clean_value(value: object) -> float | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    value = float(value)
    return round(value, 3) if math.isfinite(value) else None


def fetch_history_rows(
    entity_id: str,
    start: datetime,
    end: datetime,
    base_url: str | None = None,
    auth: tuple[str, str] | None = None,
    max_rows: int = MAX_IMPORT_ROWS_PER_ENTITY,
) -> HistoryFetchResult:
    """Liest die Rohpunkte einer Entität zeitfensterweise über
    GET /api/v1/export. Mehrere Serien mit demselben Metriknamen (Tag-Wechsel,
    siehe Moduldocstring) werden nach Zeitstempel zusammengeführt;
    gleichzeitige Punkte kollabieren auf den ersten, damit der generische
    Importkern strikt aufsteigende Zeitstempel bekommt."""
    result = HistoryFetchResult()
    points: dict[float, float] = {}
    window_start = start
    while window_start < end:
        window_end = min(window_start + HISTORY_CHUNK, end)
        body = _open(
            _request(
                base_url or DEFAULT_BASE_URL,
                "/api/v1/export",
                {
                    "match[]": f'{{__name__="{_selector_name(entity_id)}"}}',
                    "start": f"{window_start.timestamp():.3f}",
                    "end": f"{window_end.timestamp():.3f}",
                },
                auth,
            )
        )
        for line in body.splitlines():
            if not line.strip():
                continue
            try:
                series = json.loads(line)
                values, stamps = series["values"], series["timestamps"]
            except (ValueError, KeyError, TypeError) as exc:
                raise VmApiError(tr("VictoriaMetrics lieferte keine gültige Export-Antwort")) from exc
            for stamp, raw in zip(stamps, values):
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
                        tr("VictoriaMetrics-Historie enthält mehr als {limit} Datenpunkte", limit=f"{max_rows:,}".replace(",", "."))
                    )
        window_start = window_end
    result.rows = sorted(points.items())
    return result


def fetch_availability(
    entity_ids: list[str],
    start: datetime,
    end: datetime,
    base_url: str | None = None,
    auth: tuple[str, str] | None = None,
) -> dict[str, EntityAvailability]:
    """Verfügbarkeits-Vorschau für die Auswahltabelle: je Batch EINE
    Instant-Query (Anzahl, erster und letzter Zeitstempel je Messreihe über das
    gesamte Fenster) statt eines Exports je Entität — der Export ganzer
    Historien nur für eine Vorschau wäre bei retention 99y viel zu teuer.
    Serien derselben Entität (Tag-Wechsel) werden in der Query zusammengefasst.
    Gruppiert wird über die Tags ``domain``/``entity_id`` statt über
    ``__name__``: Rollup-Funktionen wie count_over_time verwerfen den
    Metriknamen, ``by (__name__)`` träfe danach ins Leere. Die volle Entity-ID
    ist ``<domain>.<entity_id-Tag>``, siehe Moduldocstring."""
    result = {eid: EntityAvailability(eid) for eid in entity_ids}
    window = max(int(end.timestamp() - start.timestamp()), 1)
    for batch_start in range(0, len(entity_ids), ENTITY_BATCH_SIZE):
        batch = entity_ids[batch_start : batch_start + ENTITY_BATCH_SIZE]
        names = "|".join(_regex_escape(_selector_name(eid)) for eid in batch)
        selector = f'{{__name__=~"{names}"}}[{window}s]'
        queries = {
            "count": f"sum by (domain, entity_id) (count_over_time({selector}))",
            "first": f"min by (domain, entity_id) (tfirst_over_time({selector}))",
            "last": f"max by (domain, entity_id) (tlast_over_time({selector}))",
        }
        by_entity: dict[str, dict[str, float]] = {}
        for key, query in queries.items():
            body = _open(
                _request(
                    base_url or DEFAULT_BASE_URL,
                    "/api/v1/query",
                    {"query": query, "time": f"{end.timestamp():.0f}"},
                    auth,
                )
            )
            try:
                payload = json.loads(body)
                rows = payload["data"]["result"]
            except (ValueError, KeyError, TypeError) as exc:
                raise VmApiError(tr("VictoriaMetrics lieferte keine gültige Antwort")) from exc
            for row in rows:
                try:
                    metric = row["metric"]
                    full_id = f'{metric["domain"]}.{metric["entity_id"]}'
                    by_entity.setdefault(full_id, {})[key] = float(row["value"][1])
                except (KeyError, IndexError, TypeError, ValueError):
                    continue
        for eid in batch:
            found = by_entity.get(eid)
            if not found or not found.get("count"):
                continue
            avail = result[eid]
            avail.count = int(found["count"])
            avail.first_ts = found.get("first")
            avail.last_ts = found.get("last")
    return result
