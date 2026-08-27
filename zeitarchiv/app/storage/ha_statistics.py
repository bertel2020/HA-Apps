"""HA-Langzeitstatistik importieren: liest über die Home-Assistant-Core-
WebSocket-API (Supervisor-Proxy, ws://supervisor/core/websocket,
SUPERVISOR_TOKEN — dieselbe Authentifizierung wie ha_import.py über die
REST-API, nur ein anderes Protokoll) die von HAs Recorder aggregierten
Stunden-/Tageswerte (`recorder/statistics_during_period`) ein.

Ergänzt ha_import.py (Rohhistorie), das HA standardmäßig nur ~10 Tage
vorhält: die Statistik-Tabelle bereinigt HA per Voreinstellung NIE
automatisch (anders als Rohzustände/die 5-Minuten-Kurzzeitstatistik),
deckt also auch deutlich ältere Zeiträume ab — dafür nur als Stunden-
bzw. Tages-Aggregat (Mittelwert oder fortlaufende Summe je Fenster),
nicht als Einzelmesswert. Nur Entitäten mit HA-`state_class` (i. d. R.
`sensor.*`) haben überhaupt Statistiken; binäre Domains (SWITCH_DOMAINS
in ha_import.py) so gut wie nie.

Nur die WebSocket-API kennt `recorder/statistics_during_period` und
`recorder/list_statistic_ids` — anders als die Rohhistorie gibt es dafür
keinen REST-Endpunkt unter /api/history/*.

Gibt wie ha_import.py bereits geparste (ts, value)-Zeilen im selben
HistoryFetchResult/EntityAvailability-Format zurück (aus ha_import.py
importiert, nicht neu definiert), damit import_routes.py beide Quellen
über denselben generischen Importkern (symcon_import.py) abwickeln kann
— der Unterschied "Rohhistorie vs. Statistik" bleibt auf diese beiden
Fetch-Module beschränkt."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta

from websockets.exceptions import WebSocketException
from websockets.sync.client import connect

from ..limits import MAX_IMPORT_ROWS_PER_ENTITY
from .ha_import import EntityAvailability, HaApiError, HistoryFetchResult

# Dieselbe Zurückhaltung wie ha_import.py: nur DEBUG, nie WARNING/höher —
# das bleibt Aufgabe der aufrufenden Route (import_routes.py).
logger = logging.getLogger(__name__)

CORE_WS_URL = "ws://supervisor/core/websocket"
REQUEST_TIMEOUT = 20
# Deutlich kleinere Batches/Fenster als ha_import.ENTITY_BATCH_SIZE bei
# "hour": ein Jahr Stundenwerte für 40 Entitäten in einer Antwort wäre
# mehrere zehntausend Objekte — CHUNK_DAYS begrenzt die Fenstergröße je
# WS-Anfrage, unabhängig von der Batchgröße.
STATISTIC_BATCH_SIZE = 40
CHUNK_DAYS = {"hour": 30, "day": 366}
# websockets' Standard (1 MiB) reicht für einen vollen Stunden-Chunk mit
# STATISTIC_BATCH_SIZE Entitäten nicht sicher aus (30 Tage × 24 × 40 ≈
# 29.000 Punkte) — großzügig genug angehoben, bleibt aber eine feste
# Obergrenze gegen eine pathologisch große Antwort.
MAX_MESSAGE_BYTES = 64 * 1024 * 1024
# Schutz gegen eine denkbare Endlosschleife in _ws_call(), falls der
# Server aus irgendeinem Grund dauerhaft Nachrichten ohne passende id
# schickt (z. B. unerwartete Events) — recv(timeout=…) begrenzt bereits
# die Wartezeit je Nachricht, dieser Zähler zusätzlich die Gesamtzahl.
MAX_MESSAGES_PER_CALL = 2000

PERIODS = {"hour": "Stundenwerte", "day": "Tageswerte"}
DEFAULT_PERIOD = "hour"


@dataclass
class StatisticMeta:
    """Aus recorder/list_statistic_ids: ob/wie eine Entität überhaupt
    Langzeitstatistik führt. has_mean gehört zu state_class=measurement
    (z. B. Temperatur), has_sum zu total/total_increasing (z. B. Energie-
    zähler) — nie beides bei derselben Entität."""

    statistic_id: str
    has_mean: bool = False
    has_sum: bool = False
    unit: str | None = None

    @property
    def supported(self) -> bool:
        return self.has_mean or self.has_sum


def _token() -> str:
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        raise HaApiError("Supervisor ist in dieser Umgebung nicht verfügbar")
    return token


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _ws_error_detail(response: dict) -> str:
    error = response.get("error")
    if isinstance(error, dict) and error.get("message"):
        return f": {error['message']}"
    return ""


def _ws_call(commands: list[dict]) -> list[dict]:
    """Öffnet EINE WebSocket-Verbindung, authentifiziert sich und sendet alle
    commands sequenziell (jeweils über eine eigene id referenziert) — billiger
    als eine Verbindung je Befehl, wichtig für fetch_statistics_availability()
    mit mehreren Batches/Zeitfenstern. Gibt für JEDEN command ein Ergebnis-
    Dict zurück (in derselben Reihenfolge) und lässt success=False-Antworten
    unverändert durch — der Aufrufer entscheidet selbst, ob/wie er einen
    einzelnen fehlgeschlagenen Befehl behandelt, ein Fehlschlag bei einem
    Zeitfenster/Batch darf nicht die übrigen ungültig machen."""
    if not commands:
        return []
    # _token() VOR connect(): ohne SUPERVISOR_TOKEN (z. B. lokale Entwicklung
    # ohne Supervisor) sonst ein sinnloser Verbindungsversuch gegen den
    # ws://supervisor-Hostnamen, der bis zu REQUEST_TIMEOUT Sekunden hängt,
    # bevor derselbe Fehler doch nur "Supervisor nicht verfügbar" wäre —
    # ha_import.py._get() prüft das aus demselben Grund vor jedem urlopen().
    token = _token()
    logger.debug("HA-WS-Anfrage · Befehle=%d", len(commands))
    try:
        with connect(
            CORE_WS_URL,
            open_timeout=REQUEST_TIMEOUT,
            close_timeout=5,
            max_size=MAX_MESSAGE_BYTES,
            user_agent_header="Zeitarchiv/HaImport",
        ) as ws:
            auth_msg = json.loads(ws.recv(timeout=REQUEST_TIMEOUT))
            if auth_msg.get("type") != "auth_required":
                raise HaApiError("Home-Assistant-WebSocket-API antwortete unerwartet")
            ws.send(json.dumps({"type": "auth", "access_token": token}))
            auth_result = json.loads(ws.recv(timeout=REQUEST_TIMEOUT))
            if auth_result.get("type") != "auth_ok":
                raise HaApiError("Home-Assistant-WebSocket-API: Authentifizierung fehlgeschlagen")

            for msg_id, command in enumerate(commands, start=1):
                ws.send(json.dumps({**command, "id": msg_id}))

            results: dict[int, dict] = {}
            pending = set(range(1, len(commands) + 1))
            reads = 0
            while pending:
                reads += 1
                if reads > MAX_MESSAGES_PER_CALL:
                    raise HaApiError("Home-Assistant-WebSocket-API: keine vollständige Antwort erhalten")
                response = json.loads(ws.recv(timeout=REQUEST_TIMEOUT))
                resp_id = response.get("id")
                if resp_id in pending:
                    results[resp_id] = response
                    pending.discard(resp_id)
            # Nur fürs DEBUG-Log gezählt — die "result"-Form unterscheidet
            # sich je Befehlstyp: recorder/statistics_during_period liefert
            # ein dict {statistic_id: [Punkte]}, recorder/list_statistic_ids
            # dagegen direkt eine Liste. Beide Formen robust behandeln statt
            # eine davon anzunehmen, sonst crasht ausgerechnet das Logging
            # selbst (AttributeError: 'list' object has no attribute
            # 'values') statt nur eine ungenaue Zahl zu liefern.
            entries = 0
            for r in results.values():
                if not r.get("success"):
                    continue
                result = r.get("result")
                if isinstance(result, dict):
                    entries += sum(len(v) for v in result.values() if isinstance(v, list))
                elif isinstance(result, list):
                    entries += len(result)
            logger.debug("HA-WS-Antwort · Befehle=%d · Einträge=%d", len(commands), entries)
            return [results[i] for i in range(1, len(commands) + 1)]
    except HaApiError:
        raise
    except TimeoutError as exc:
        raise HaApiError("Home-Assistant-WebSocket-API antwortete nicht rechtzeitig") from exc
    except (WebSocketException, OSError) as exc:
        raise HaApiError(f"Home-Assistant-WebSocket-API nicht erreichbar: {exc}") from exc
    except (ValueError, KeyError) as exc:
        raise HaApiError("Home-Assistant-WebSocket-API lieferte eine unerwartete Antwort") from exc


def fetch_statistic_meta(entity_ids: list[str]) -> dict[str, StatisticMeta]:
    """Ob/wie die übergebenen Entitäten überhaupt Langzeitstatistik führen —
    recorder/list_statistic_ids kennt keinen Filterparameter für einzelne
    IDs, liefert also immer ALLE dem Recorder bekannten Statistik-Serien;
    die Filterung auf die hier interessierenden entity_ids passiert deshalb
    lokal nach der Antwort."""
    result = {eid: StatisticMeta(eid) for eid in entity_ids}
    wanted = set(entity_ids)
    if not wanted:
        return result
    responses = _ws_call([{"type": "recorder/list_statistic_ids"}])
    response = responses[0]
    if not response.get("success"):
        raise HaApiError(f"Home-Assistant-API antwortete{_ws_error_detail(response)}")
    for entry in response.get("result") or []:
        if not isinstance(entry, dict):
            continue
        sid = entry.get("statistic_id")
        if sid not in wanted:
            continue
        result[sid] = StatisticMeta(
            statistic_id=sid,
            has_mean=bool(entry.get("has_mean")),
            has_sum=bool(entry.get("has_sum")),
            unit=entry.get("statistics_unit_of_measurement") or entry.get("unit_of_measurement"),
        )
    return result


def _statistic_value(entry: dict) -> float | None:
    """Ein Statistik-Fenster (Stunde/Tag) liefert je nach state_class
    entweder mean (measurement, z. B. Temperatur) oder sum (total/
    total_increasing, z. B. fortlaufender Energiezähler) — nie beides
    sinnvoll gleichzeitig. mean hat Vorrang, weil ein vorhandenes sum bei
    measurement-Entitäten (falls HA es doch mitliefert) hier nichts
    Sinnvolles bedeutet."""
    mean = entry.get("mean")
    if isinstance(mean, (int, float)):
        return round(float(mean), 3)
    total = entry.get("sum")
    if isinstance(total, (int, float)):
        return round(float(total), 3)
    return None


def _bucket_ts(entry: dict) -> float | None:
    """"start" ist in aktuellen HA-Versionen ein Unix-Zeitstempel in
    Millisekunden (int/float); ältere WS-API-Antworten lieferten dafür eine
    ISO-8601-Zeichenkette — beide Formen werden akzeptiert, damit ein
    HA-Versionswechsel des Antwortformats nicht sofort zu einem stillen
    Totalausfall führt."""
    start = entry.get("start")
    if isinstance(start, (int, float)):
        return start / 1000.0 if start > 1e12 else float(start)
    if isinstance(start, str):
        try:
            return datetime.fromisoformat(start.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


def _chunk_ranges(start: datetime, end: datetime, period: str) -> list[tuple[datetime, datetime]]:
    chunk = timedelta(days=CHUNK_DAYS.get(period, CHUNK_DAYS["hour"]))
    ranges = []
    window_start = start
    while window_start < end:
        window_end = min(window_start + chunk, end)
        ranges.append((window_start, window_end))
        window_start = window_end
    return ranges


def fetch_statistics_rows(
    entity_id: str,
    start: datetime,
    end: datetime,
    period: str = DEFAULT_PERIOD,
    max_rows: int = MAX_IMPORT_ROWS_PER_ENTITY,
) -> HistoryFetchResult:
    """Liest Langzeitstatistik einer einzelnen Entität zeitfensterweise über
    recorder/statistics_during_period. Anders als fetch_history_rows() gibt
    es hier keine Fenstergrenzen-Duplikate zu bereinigen — jedes Fenster
    (Stunde/Tag) liefert je Bucket höchstens einen Punkt, Buckets über
    mehrere Chunks hinweg überschneiden sich nie."""
    result = HistoryFetchResult()
    for window_start, window_end in _chunk_ranges(start, end, period):
        responses = _ws_call([{
            "type": "recorder/statistics_during_period",
            "start_time": _iso(window_start),
            "end_time": _iso(window_end),
            "statistic_ids": [entity_id],
            "period": period,
            "types": ["mean", "sum"],
        }])
        response = responses[0]
        if not response.get("success"):
            raise HaApiError(f"Home-Assistant-API antwortete{_ws_error_detail(response)}")
        entries = (response.get("result") or {}).get(entity_id) or []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            ts = _bucket_ts(entry)
            if ts is None:
                result.skipped += 1
                continue
            value = _statistic_value(entry)
            if value is None:
                result.skipped += 1
                continue
            result.rows.append((ts, value))
            if len(result.rows) > max_rows:
                raise ValueError(
                    f"HA-Langzeitstatistik enthält mehr als {max_rows:,} Datenpunkte".replace(",", ".")
                )
    result.rows.sort(key=lambda row: row[0])
    return result


def fetch_statistics_availability(
    entity_ids: list[str], start: datetime, end: datetime, period: str = DEFAULT_PERIOD
) -> dict[str, EntityAvailability]:
    """Verfügbarkeits-Vorschau für die Auswahltabelle, Pendant zu
    ha_import.fetch_availability() — EIN gebündelter Abruf für beliebig
    viele Entitäten je Zeitfenster (Batchgröße siehe STATISTIC_BATCH_SIZE),
    plus eine vorgeschaltete recorder/list_statistic_ids-Abfrage, damit
    EntityAvailability.supported zwischen "keine Statistik im Zeitraum" und
    "diese Entität führt grundsätzlich keine Langzeitstatistik" unterscheiden
    kann (letzteres i. d. R. bei Domains ohne state_class, z. B. binary_sensor)."""
    meta = fetch_statistic_meta(entity_ids)
    result = {
        eid: EntityAvailability(eid, supported=meta[eid].supported) for eid in entity_ids
    }
    supported_ids = [eid for eid in entity_ids if meta[eid].supported]
    for batch_start in range(0, len(supported_ids), STATISTIC_BATCH_SIZE):
        batch = supported_ids[batch_start : batch_start + STATISTIC_BATCH_SIZE]
        for window_start, window_end in _chunk_ranges(start, end, period):
            responses = _ws_call([{
                "type": "recorder/statistics_during_period",
                "start_time": _iso(window_start),
                "end_time": _iso(window_end),
                "statistic_ids": batch,
                "period": period,
                "types": ["mean", "sum"],
            }])
            response = responses[0]
            if not response.get("success"):
                raise HaApiError(f"Home-Assistant-API antwortete{_ws_error_detail(response)}")
            payload = response.get("result") or {}
            for entity_id in batch:
                avail = result[entity_id]
                for entry in payload.get(entity_id) or []:
                    if not isinstance(entry, dict):
                        continue
                    ts = _bucket_ts(entry)
                    if ts is None or _statistic_value(entry) is None:
                        continue
                    if avail.first_ts is None or ts < avail.first_ts:
                        avail.first_ts = ts
                    if avail.last_ts is None or ts > avail.last_ts:
                        avail.last_ts = ts
                    avail.count += 1
    return result
