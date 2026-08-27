"""HA-Recorder-Rohhistorie importieren: liest über die Home-Assistant-Core-API
(Supervisor-Proxy, SUPERVISOR_TOKEN — dasselbe Zugriffsmuster wie
supervisor_stats.py) die vorhandene Zustandshistorie einer HA-Instanz direkt
ein, statt wie beim Symcon-/CSV-Import auf eine hochgeladene Datei angewiesen
zu sein. Gedacht für den Einstieg ohne Symcon: HA hält Rohhistorie
standardmäßig nur ~10 Tage vor, das deckt trotzdem den typischen "Zeitarchiv
gerade installiert" Fall ab.

Nur bereits in Zeitarchiv bekannte Entitäten stehen zur Auswahl (siehe
main.py::_ha_import_context — die Liste kommt aus index.list_entities(),
nicht aus /api/states): die HA-Integration hat sie bereits gefiltert/
konfiguriert, ein zusätzlicher Entdeckungsmechanismus über die Core-API wäre
nur eine zweite, potenziell abweichende Quelle der Wahrheit. Dieses Modul
braucht die HA-Core-API deshalb ausschließlich für den eigentlichen
Historienabruf, nicht für die Entitätsliste.

Gibt wie csv_import.py bereits geparste (ts, value)-Zeilen zurück, die
unverändert an symcon_import.py::plan_import_rows()/import_rows() (den
generischen, herkunftsunabhängigen Importkern) weitergereicht werden.

Wertnormalisierung bewusst identisch zum Live-Pfad
(custom_components/zeitarchiv/events.py::build_event), damit ein historisch
importierter und ein live aufgezeichneter Punkt für denselben Zustand
denselben Wert ergeben."""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from ..limits import MAX_IMPORT_ROWS_PER_ENTITY

# Abweichend von der sonstigen storage/*.py-Konvention (dort loggt nie das
# Modul selbst, nur der aufrufende Routen-Handler, siehe import_routes.py):
# dieses Modul spricht als einziges storage/*-Modul ein externes, unter
# eigener Kontrolle des Nutzers stehendes System über das Netzwerk an. Ein
# Fehlschlag kann mitten in mehreren Zeitfenstern/Batches auftreten — der
# Aufrufer sieht dann nur noch die eine, zusammengefasste Ausnahme, nicht
# mehr, welche der vorherigen Anfragen bereits erfolgreich waren. Nur DEBUG,
# nie WARNING/höher: das bleibt Aufgabe der aufrufenden Route.
logger = logging.getLogger(__name__)

# Dieselben Domain-/Zustands-Regeln wie custom_components/zeitarchiv/const.py
# — von dort nicht importierbar, weil das Addon in einem eigenen Python-Prozess
# ohne Home-Assistant-Paket läuft.
SWITCH_DOMAINS = {"binary_sensor", "switch", "input_boolean"}
IGNORED_STATES = {"unavailable", "unknown", "none", ""}

CORE_API_BASE = "http://supervisor/core/api"
# Abfrage in Zeitfenstern statt eines einzelnen Requests über den ganzen
# gewählten Zeitraum — bei hochfrequenten Sensoren/langen Zeiträumen sonst
# eine einzelne, potenziell sehr große Antwort.
HISTORY_CHUNK = timedelta(days=7)
REQUEST_TIMEOUT = 20
# /api/history/period akzeptiert eine kommagetrennte filter_entity_id-Liste —
# fetch_availability() nutzt das, um die Verfügbarkeits-Vorschau für viele
# Zeilen der Auswahltabelle mit wenigen Requests statt einem pro Entität zu
# holen. Batchgröße begrenzt trotzdem die einzelne Anfrage/URL-Länge.
ENTITY_BATCH_SIZE = 40


class HaApiError(RuntimeError):
    """Home-Assistant-Core-API nicht erreichbar — z. B. kein Supervisor-Token
    (lokale Entwicklung ohne Supervisor) oder die Anfrage schlägt fehl."""


def token_available() -> bool:
    """Rein lokale Prüfung ohne Netzwerk-Roundtrip — ob überhaupt ein
    Supervisor-Umfeld vorliegt, für den Hinweis auf der Import-Seite, bevor
    ein Dry Run/Import tatsächlich die HA-API anspricht."""
    return bool(os.environ.get("SUPERVISOR_TOKEN"))


def _token() -> str:
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        raise HaApiError("Supervisor ist in dieser Umgebung nicht verfügbar")
    return token


def _get(path: str, params: dict | None = None) -> object:
    url = f"{CORE_API_BASE}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {_token()}",
            "Accept": "application/json",
            "User-Agent": "Zeitarchiv/HaImport",
        },
    )
    # Nie den Authorization-Header/Token loggen — nur Pfad und die für die
    # Fehlersuche relevanten Parameter (Zeitfenster, Anzahl gefilterter
    # Entitäten statt der vollen, potenziell langen ID-Liste).
    entity_count = len((params or {}).get("filter_entity_id", "").split(",")) if params and params.get("filter_entity_id") else 0
    logger.debug(
        "HA-API-Anfrage · %s · Entitäten=%d · bis=%s", path, entity_count, (params or {}).get("end_time")
    )
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
            payload = json.loads(response.read())
            entries = sum(len(e) for e in payload if isinstance(e, list)) if isinstance(payload, list) else 0
            logger.debug("HA-API-Antwort · %s · Arrays=%d · Einträge=%d", path, len(payload) if isinstance(payload, list) else 0, entries)
            return payload
    except urllib.error.HTTPError as exc:
        # HTTPError ist eine Unterklasse von URLError — MUSS deshalb vor dessen
        # except-Zweig geprüft werden, sonst verschluckt der generische Zweig
        # unten jeden HTTP-Fehlerstatus (401/403/400/404/…) ununterscheidbar
        # von einer echten Netzwerk-Nichterreichbarkeit. Gerade 401/403 sind
        # der wahrscheinlichste reale Fehlerfall: die Supervisor-Berechtigung
        # `homeassistant_api` wirkt erst nach einem Add-on-Update/Rebuild
        # (siehe CHANGELOG) — ein bloßer Prozess-Neustart mit unveränderter
        # Version reicht nicht, das SUPERVISOR_TOKEN bleibt dann ohne
        # Core-API-Berechtigung.
        detail = _http_error_detail(exc)
        # HAs "message"-Feld enthält den Statuscode meist schon als Text
        # (z. B. "401: Unauthorized") — dann nicht zusätzlich davorsetzen,
        # sonst steht der Code doppelt in der Meldung. Andernfalls (kein
        # JSON-Body, z. B. eine HTML-Fehlerseite eines vorgeschalteten
        # Proxys) bleibt der Code die einzige verlässliche Angabe.
        stripped = detail.lstrip(": ")
        message = stripped if stripped.startswith(str(exc.code)) else f"Fehler {exc.code}{detail}"
        raise HaApiError(f"Home-Assistant-API antwortete mit {message}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise HaApiError(f"Home-Assistant-API nicht erreichbar: {exc.reason if isinstance(exc, urllib.error.URLError) else exc}") from exc
    except ValueError as exc:
        raise HaApiError("Home-Assistant-API lieferte keine gültige JSON-Antwort") from exc


def _http_error_detail(exc: "urllib.error.HTTPError") -> str:
    """Home Assistants Fehlerantworten sind i. d. R. JSON mit einem
    "message"-Feld (z. B. {"message": "401: Unauthorized"}) — wird die
    Antwort mitgeliefert, ist sie für die Fehlersuche wertvoller als der
    reine Statuscode (unterscheidet z. B. "ungültiger Token" von "Endpunkt
    kennt diesen Parameter nicht"). Kein harter Fehlschlag, falls die
    Fehlerantwort aus irgendeinem Grund nicht lesbar/kein JSON ist — dann
    bleibt wenigstens der Statuscode aus dem Aufrufer."""
    try:
        body = exc.read()
    except OSError:
        return ""
    try:
        data = json.loads(body)
    except ValueError:
        text = body.decode("utf-8", errors="replace").strip()
        return f": {text[:200]}" if text else ""
    if isinstance(data, dict) and data.get("message"):
        return f": {data['message']}"
    return ""


def _parse_state(state: str, domain: str) -> float | None:
    normalized = state.strip().lower()
    if normalized in IGNORED_STATES:
        return None
    if domain in SWITCH_DOMAINS:
        if normalized not in ("on", "off"):
            return None
        return 1.0 if normalized == "on" else 0.0
    try:
        return round(float(state), 3)
    except (TypeError, ValueError):
        return None


def _iso(dt: datetime) -> str:
    return dt.isoformat()


@dataclass
class HistoryFetchResult:
    rows: list[tuple[float, float]] = field(default_factory=list)
    skipped: int = 0


def fetch_history_rows(
    entity_id: str,
    domain: str,
    start: datetime,
    end: datetime,
    max_rows: int = MAX_IMPORT_ROWS_PER_ENTITY,
) -> HistoryFetchResult:
    """Liest die Recorder-Rohhistorie einer Entität zeitfensterweise über
    GET /api/history/period. Der jeweils erste Punkt eines Fensters kann ein
    von HA synthetisch eingefügter "Zustand zu Fensterbeginn" sein — identisch
    zum letzten übernommenen Punkt (oder älter) wird er verworfen, damit an
    Fenstergrenzen keine künstlichen Duplikate/Messpunkte entstehen."""
    result = HistoryFetchResult()
    last_ts: float | None = None
    window_start = start
    while window_start < end:
        window_end = min(window_start + HISTORY_CHUNK, end)
        payload = _get(
            f"/history/period/{urllib.parse.quote(_iso(window_start), safe='')}",
            {
                "filter_entity_id": entity_id,
                "end_time": _iso(window_end),
                "minimal_response": "true",
                "no_attributes": "true",
            },
        )
        entries = payload[0] if isinstance(payload, list) and payload else []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            changed = entry.get("last_changed") or entry.get("last_updated")
            if not changed:
                result.skipped += 1
                continue
            try:
                ts = datetime.fromisoformat(str(changed).replace("Z", "+00:00")).timestamp()
            except ValueError:
                result.skipped += 1
                continue
            value = _parse_state(str(entry.get("state")), domain)
            if value is None:
                result.skipped += 1
                continue
            if last_ts is not None and ts <= last_ts:
                continue
            result.rows.append((ts, value))
            last_ts = ts
            if len(result.rows) > max_rows:
                raise ValueError(
                    f"HA-Historie enthält mehr als {max_rows:,} Datenpunkte".replace(",", ".")
                )
        window_start = window_end
    return result


@dataclass
class EntityAvailability:
    entity_id: str
    first_ts: float | None = None
    last_ts: float | None = None
    count: int = 0
    # Nur von ha_statistics.fetch_statistics_availability() gesetzt (dort
    # kann eine Entität grundsätzlich keine Langzeitstatistik führen, z. B.
    # ohne state_class) — bei der Rohhistorie hier immer True, jede Domain
    # kann grundsätzlich Zustände haben.
    supported: bool = True

    @property
    def has_data(self) -> bool:
        return self.count > 0


def fetch_availability(
    entity_domains: dict[str, str], start: datetime, end: datetime
) -> dict[str, EntityAvailability]:
    """Verfügbarkeits-Vorschau für die Auswahltabelle: EIN gebündelter
    History-Abruf für beliebig viele Entitäten gleichzeitig (HA erlaubt eine
    kommagetrennte filter_entity_id-Liste), statt eines Requests je Zeile —
    macht eine Live-Vorschau erst praktikabel, ohne die HA-Instanz mit N
    Einzelabfragen zu belasten (Batchgröße siehe ENTITY_BATCH_SIZE). Zählt nur
    tatsächlich importierbare Punkte (dieselbe Wertnormalisierung wie
    fetch_history_rows), prüft aber NICHT auf Langzeitstatistik — die
    unterstützt dieser Import bewusst (noch) nicht, siehe Moduldocstring.

    Mit minimal_response=true liefert HA nur beim jeweils ersten (und
    letzten) Punkt je Entität das volle Objekt inkl. entity_id, dazwischen
    nur state/last_changed — deshalb wird die Entität pro zurückgegebenem
    Array über dessen ERSTEN Eintrag identifiziert statt über die
    Listenposition, die bei mehreren angefragten Entitäten nicht als stabil
    garantiert ist."""
    result = {eid: EntityAvailability(eid) for eid in entity_domains}
    last_ts_by_entity: dict[str, float] = {}
    entity_ids = list(entity_domains)
    for batch_start in range(0, len(entity_ids), ENTITY_BATCH_SIZE):
        batch = entity_ids[batch_start : batch_start + ENTITY_BATCH_SIZE]
        window_start = start
        while window_start < end:
            window_end = min(window_start + HISTORY_CHUNK, end)
            payload = _get(
                f"/history/period/{urllib.parse.quote(_iso(window_start), safe='')}",
                {
                    "filter_entity_id": ",".join(batch),
                    "end_time": _iso(window_end),
                    "minimal_response": "true",
                    "no_attributes": "true",
                },
            )
            for entries in payload if isinstance(payload, list) else []:
                if not entries or not isinstance(entries, list) or not isinstance(entries[0], dict):
                    continue
                entity_id = entries[0].get("entity_id")
                domain = entity_domains.get(entity_id)
                if domain is None:
                    continue
                avail = result[entity_id]
                for entry in entries:
                    if not isinstance(entry, dict):
                        continue
                    changed = entry.get("last_changed") or entry.get("last_updated")
                    if not changed:
                        continue
                    try:
                        ts = datetime.fromisoformat(str(changed).replace("Z", "+00:00")).timestamp()
                    except ValueError:
                        continue
                    if _parse_state(str(entry.get("state")), domain) is None:
                        continue
                    prev = last_ts_by_entity.get(entity_id)
                    if prev is not None and ts <= prev:
                        continue
                    last_ts_by_entity[entity_id] = ts
                    if avail.first_ts is None:
                        avail.first_ts = ts
                    avail.last_ts = ts
                    avail.count += 1
            window_start = window_end
    return result
