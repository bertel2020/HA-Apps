"""Energiedashboard: eigenständige Sankey-Ansicht des Energieflusses (Netzbezug/
Erzeuger/Speicherentladung -> zentraler Sammelknoten (Default "Haus", frei
benennbar) -> Verbraucher/Grundlast/Einspeisung/Speicherladung), komplett
unabhängig vom regulären dashboards/dashboard_pins-
System. Ein/Aus-Schalter und Rollen-Konfiguration liegen als zwei Schlüssel in
der bereits vorhandenen generischen settings-Tabelle (Index.get_setting/
set_setting, exakt das Muster von font_scale/color_scheme/dashboard_animation)
statt eines eigenen Tables — es gibt genau eine Instanz, kein m:n-Bedarf
(dieselbe Begründung wie bei saved_charts.entity_ids als JSON-TEXT). Gleiches
Modul-Muster wie api_routes.py/report_routes.py/import_routes.py (Dependencies-
Dataclass + Service mit router()), siehe main.py "main.py-Zeilenbudget".

Perioden-Werte je Rolle nutzen ausschließlich vorhandene Bausteine —
query_series() für die Rohdaten und _table_aggregates()["auto"] für den
korrekten Perioden-Delta-Wert (behandelt Zähler-Resets bereits transparent,
siehe rollup.py) — kein eigener Subtraktions-/Aggregations-Code."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from .api_routes import _table_aggregates
from .formatting import entity_display_name
from .storage import cleanup as cleanup_mod
from .storage import query as query_mod
from .storage import rollup as rollup_mod
from .storage.index import Index

SETTING_ENABLED = "energiedashboard_enabled"
SETTING_CONFIG = "energiedashboard_config"
# Zähler-Entitäten, die als Energiedashboard-Rolle konfiguriert sind, warten
# hier auf ihren rückwirkenden Rollup-Backfill (bereits archivierte Monate),
# bevor _wartungsplaner sie einzeln nachträgt — siehe process_pending_hourly_backfill().
SETTING_HOURLY_BACKFILL_PENDING = "energiedashboard_hourly_backfill_pending"

RANGE_LABELS = {"hour": "Stunde", "day": "Tag", "month": "Monat", "year": "Jahr"}
RANGE_KEYS = tuple(RANGE_LABELS)
# Perioden-Vergleich: immer rollierend (zwei aneinander anschließende
# Fenster fester Länge, siehe compute_flow(..., continuous=True) in
# energiedashboard_data) statt kalendarisch — bleibt dadurch auch für die
# noch laufende Periode und bei dünn archivierten Zeiträumen aussagekräftig.
# Labels bewusst weiter "Vortag/Vormonat/Vorjahr" (nicht "letzte 24 Std."
# o. ä.): auf ±1 Tag/Monat/Jahr genau ist das rollierende Fenster ohnehin
# dasselbe, nur ohne Kalendergrenze als Bezugspunkt.
COMPARE_LABELS = {"hour": "Vorstunde", "day": "Vortag", "month": "Vormonat", "year": "Vorjahr"}

# Ab diesem Sensor-Alter gilt ein Wert als "seit Längerem keine neuen Daten"
# und fließt als Warnung in die Bilanz-Kachel ein — unabhängig vom gerade
# betrachteten historischen Zeitraum (auch beim Blick auf "letzten Monat" soll
# ein AKTUELL kaputter Sensor auffallen).
STALE_SECONDS = 2 * 24 * 60 * 60

# Obergrenze für die Zählerrückgang-Prüfung (Rohwerte-Scan je Rolle) — bei
# sehr langen Zeiträumen (Jahr) oder hochfrequenten Sensoren bricht die
# Prüfung dann kontrolliert ab (ResultLimitExceeded) statt die ganze Seite
# zu verlangsamen; die Kachel markiert das als "nicht geprüft" statt einen
# falschen "unauffällig"-Status vorzutäuschen. Deutlich kleiner als
# MAX_RAW_QUERY_POINTS (100.000, siehe limits.py) — hier reicht ein
# günstiger, schneller Scan, kein vollständiger Chart-Datenabruf.
RESET_CHECK_MAX_ROWS = 20_000

# Sparkline-Verfeinerung für die noch laufende Periode (siehe _entity_series):
# "Monat" bucketet auf Tagesebene, "Jahr" auf Monatsebene — der jeweils
# LETZTE (noch nicht abgeschlossene) Bucket ist am 1. eines Monats/Jahres
# dadurch der EINZIGE Bucket, eine Sparkline (braucht >=2 Punkte) bleibt also
# tagelang leer. Der feinere Zeitraum hier deckt exakt denselben Zeitraum ab
# (query_series("day"/"month", offset=0) beginnt an derselben Kalendergrenze
# wie der laufende Tages-/Monats-Bucket), ersetzt also nur dessen Auflösung,
# ohne die Summe zu verändern.
_FINER_RANGE = {"month": "day", "year": "month"}

# Tageslastprofil-Heatmap: datetime.weekday() liefert 0=Montag.
_WEEKDAY_LABELS = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
HEATMAP_DAYS = 7


DEFAULT_HUB_NAME = "Haus"

# Auffälligkeiten (Schwellenwert-Färbung): dieselbe Optik wie
# OUTLIER_THRESHOLD_LABELS (formatting.py) für den bereits vorhandenen
# Ausreißer-Schwellenwert je Entität, hier aber eigenständig (kleinere
# Auswahl — 5 %/10 % wären für einen Perioden-Gesamtwert-Vergleich zu
# empfindlich, das ist ein anderer Vergleichsmaßstab als ein einzelner
# Rohwert-Sprung) und global fürs ganze Energiedashboard statt pro Entität.
ANOMALIE_SCHWELLE_LABELS = {"off": "Aus", "25": "25 %", "50": "50 %", "100": "100 %"}
# Anzahl vorheriger Perioden, deren Schnitt als "üblicher" Vergleichswert
# dient — mehrere statt nur der einen Vorperiode, damit ein Gerät, das
# einfach nicht jeden Tag läuft (z. B. Waschmaschine), nicht bei jedem
# normalen Lauf als "Anomalie" markiert wird.
ANOMALIE_BASELINE_PERIODS = 3


def _empty_config() -> dict:
    return {
        "netzbezug": None,
        "einspeisung": None,
        "erzeuger": [],
        "speicher": [],
        "verbraucher": [],
        "verbraucher_gruppen": [],
        "hub_name": None,
        "kosten": None,
        "co2": None,
        "prognose": None,
        # Sichtbarkeit der optionalen Kacheln — alles per Default an. Fehlt
        # der Schlüssel in einer älteren gespeicherten Config (vor dieser
        # Funktion), liefert _load_config() automatisch diesen True-Default,
        # ohne Migration. Energiefluss (Sankey) ist nicht abschaltbar, daher
        # kein eigener Schlüssel dafür.
        "show_autarkie": True,
        "show_verbraucheranteile": True,
        "show_kostenanalyse": True,
        "show_co2": True,
        "show_tageslastprofil": True,
        "show_bilanz_datenqualitaet": True,
        # Schwellenwert für die Verbraucher-Auffälligkeiten-Markierung (siehe
        # ANOMALIE_SCHWELLE_LABELS) — "50" (Default an, +50 %) statt "off",
        # analog zu den übrigen Kacheln, die ebenfalls per Default sichtbar
        # sind.
        "anomalie_schwelle": "50",
    }


def _load_config(index: Index) -> dict:
    raw = index.get_setting(SETTING_CONFIG, "")
    config = _empty_config()
    if not raw:
        return config
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return config
    if isinstance(data, dict):
        config.update({key: data[key] for key in config if key in data})
    # Migration: "speicher" war früher ein einzelnes Objekt (oder None), ist
    # jetzt eine Liste (mehrere Speicher gleichzeitig). Ältere gespeicherte
    # Configs bekommen ihren einen Speicher automatisch als Ein-Element-Liste
    # zurück statt ihn beim Laden zu verlieren.
    if isinstance(config.get("speicher"), dict):
        config["speicher"] = [config["speicher"]]
    elif config.get("speicher") is None:
        config["speicher"] = []
    return config


def _save_config(index: Index, config: dict) -> None:
    index.set_setting(SETTING_CONFIG, json.dumps(config, ensure_ascii=False))


def sync_hourly_rollup_flags(index: Index, role_entity_ids: list[str]) -> None:
    """Setzt/löscht entities.hourly_rollup passend zu den aktuell als
    Energiedashboard-Rolle zugeordneten Zähler-Entitäten (Netzbezug,
    Einspeisung, Erzeuger, Verbraucher, Speicher laden/entladen — siehe
    _config_entity_roles()). Nur Zähler sind betroffen: Standard/Schalter
    bekommen über FINE_LEVEL ohnehin bereits stündliche Rollups.

    Neu geflaggte Entitäten werden zusätzlich in eine Warteschlange
    eingereiht (process_pending_hourly_backfill holt sie im Wartungsplaner
    ab) — ohne das würden nur ab jetzt archivierte Monate die neue
    Stunden-Stufe bekommen, bereits archivierte Monate blieben ohne
    Wochentags-Auswertung. Wird eine Rolle wieder entfernt, bleibt bereits
    geschriebene Stunden-Rollup-Daten unangetastet liegen (kein Rückbau) —
    seltener Fall, der Speicherplatz-Nachteil ist vernachlässigbar."""
    role_counters = set()
    for entity_id in set(role_entity_ids):
        row = index.get_entity(entity_id)
        if row is not None and row["aggregation_type"] == "counter":
            role_counters.add(entity_id)

    currently_flagged = set(index.list_hourly_rollup_entity_ids())
    newly_flagged = role_counters - currently_flagged
    no_longer_role = currently_flagged - role_counters

    for entity_id in newly_flagged:
        index.set_entity_hourly_rollup(entity_id, True)
    for entity_id in no_longer_role:
        index.set_entity_hourly_rollup(entity_id, False)

    if newly_flagged:
        try:
            pending = set(json.loads(index.get_setting(SETTING_HOURLY_BACKFILL_PENDING, "[]")))
        except (TypeError, ValueError):
            pending = set()
        pending |= newly_flagged
        index.set_setting(SETTING_HOURLY_BACKFILL_PENDING, json.dumps(sorted(pending)))


def sync_hourly_rollup_flags_for_current_config(service: "EnergieDashboardService") -> None:
    """Öffentlicher Wrapper für main.py (Startup-Hook): synchronisiert
    entities.hourly_rollup für die AKTUELL gespeicherte Konfiguration einmalig
    beim Serverstart — sonst müsste jede Installation, deren Konfiguration
    bereits vor Einführung dieses Features gespeichert wurde, das
    Setup-Formular einmal manuell erneut speichern, damit der rückwirkende
    Backfill für ihre bestehenden Rollen überhaupt startet (siehe
    sync_hourly_rollup_flags(), sonst nur bei jedem Speichern aufgerufen)."""
    index = service.deps.index
    config = _load_config(index)
    if not _is_configured(config):
        return
    sync_hourly_rollup_flags(index, [eid for eid, _ in service._config_entity_roles(config)])


def process_pending_hourly_backfill(data_dir: Path, index: Index, tz: ZoneInfo, coordinator) -> None:
    """Baut für höchstens EINE wartende Entität (siehe sync_hourly_rollup_flags())
    die Stunden-Rollup-Stufe rückwirkend aus den bereits archivierten Monaten
    auf — je Wartungslauf (main.py, alle 30s) bewusst nur eine, damit ein
    einzelner Tick auch bei einer Entität mit langer Historie nicht spürbar
    blockiert; die Warteschlange leert sich über mehrere Läufe von selbst."""
    try:
        pending = json.loads(index.get_setting(SETTING_HOURLY_BACKFILL_PENDING, "[]"))
    except (TypeError, ValueError):
        pending = []
    if not isinstance(pending, list) or not pending:
        return
    entity_id, *remaining = pending
    index.set_setting(SETTING_HOURLY_BACKFILL_PENDING, json.dumps(remaining))
    with coordinator.entity(entity_id):
        entity = index.get_entity(entity_id)
        if entity is None or not entity["hourly_rollup"]:
            return  # Rolle wurde zwischenzeitlich wieder entfernt
        rollup_mod.rebuild_entity_rollups(data_dir, entity_id, "counter", tz, hourly_rollup=True)


def refresh_heatmap_weekday_cache_if_stale(service: "EnergieDashboardService") -> None:
    """Berechnet das wochentagsweise gruppierte Tageslastprofil (Monat/Jahr,
    jeweils offset=0 — der einzige Stand, den der Wartungsplaner vorhält)
    höchstens einmal täglich im Hintergrund vor (compute_heatmap_weekday()),
    damit eine Jahresansicht nicht bei jedem Seitenaufruf über alle
    Rollen-Entitäten neu rechnet. Wird beim Speichern der Konfiguration
    invalidiert (siehe die Setup-Save-Route), damit eine geänderte
    Rollenzuordnung nicht bis zu 24h lang eine veraltete Ansicht zeigt."""
    index = service.deps.index
    config = _load_config(index)
    if not _is_configured(config):
        return
    for range_key in ("month", "year"):
        if not index.is_heatmap_weekday_stale(range_key):
            continue
        grid = service.compute_heatmap_weekday(config, range_key, 0)
        index.set_heatmap_weekday_snapshot(range_key, grid)


def _is_enabled(index: Index) -> bool:
    return index.get_setting(SETTING_ENABLED, "0") == "1"


def _is_configured(config: dict) -> bool:
    return bool(config.get("netzbezug"))


def is_energiedashboard_configured(index: Index) -> bool:
    """Öffentlicher Zugriff für main.py (Dashboards-Übersicht: Status-Text der
    festen Energiedashboard-Kachel), ohne die interne Config-Struktur nach
    main.py durchsickern zu lassen."""
    return _is_configured(_load_config(index))


def energiedashboard_role_count(index: Index) -> int:
    """Anzahl zugeordneter Rollen für die Status-Zeile der festen Kachel auf
    /dashboards — dasselbe Prinzip wie "N Kacheln" bei echten Dashboards,
    nur für Rollen statt Kacheln. Netzbezug/Einspeisung zählen je 1,
    Erzeuger/Verbraucher je Eintrag, Speicher als Ganzes 1 (nicht pro Feld)."""
    config = _load_config(index)
    count = 0
    if config.get("netzbezug"):
        count += 1
    if config.get("einspeisung"):
        count += 1
    count += len(config.get("erzeuger") or [])
    if config.get("speicher"):
        count += 1
    count += len(config.get("verbraucher") or [])
    return count


def _parse_float(text: str) -> float | None:
    text = (text or "").strip().replace(",", ".")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


@dataclass(frozen=True)
class EnergieDashboardDependencies:
    data_dir: Path
    index: Index
    tz: ZoneInfo
    templates: Jinja2Templates
    app_root_context: Callable[[Request], dict]


class EnergieDashboardService:
    def __init__(self, deps: EnergieDashboardDependencies) -> None:
        self.deps = deps

    def _entity_options(self) -> list[dict]:
        return [
            {
                "entity_id": row["entity_id"],
                "label": entity_display_name(row["entity_id"], row["friendly_name"], row["custom_name"]),
                "ha_name": row["friendly_name"] or row["entity_id"],
                "is_custom": bool(row["custom_name"]),
            }
            for row in self.deps.index.list_entities()
        ]

    def _entity_series(
        self, entity_id: str, range_key: str, offset: int, now: datetime,
        read_cache: query_mod.QueryReadCache, continuous: bool = False,
    ) -> tuple[dict[float, float], bool]:
        """(Bucket-Zeitstempel -> Perioden-Delta-Wert, veraltet?) für eine
        Rolle. Fehlende/nie archivierte Entitäten liefern ({}, True) statt
        eines Fehlers — das Dashboard degradiert (fehlende Knoten/Warnung),
        statt kaputtzugehen. Die Buckets selbst kommen 1:1 aus query_series()
        (dieselbe Zähler-Delta-Logik wie die Summe, siehe Modul-Docstring) —
        die Summe der Bucket-Werte ergibt denselben Perioden-Wert wie
        _table_aggregates()["auto"], nur mit Verlauf für die Sparkline.

        continuous: identisch zum gleichnamigen Parameter in query_series()/
        _window() — ein rollierendes Fenster fester Länge, das genau bei
        `now` (verschoben um `offset` ganze Fensterlängen) endet, statt an
        der Kalendergrenze. Nur für den Perioden-Vergleich genutzt (siehe
        compute_flow/energiedashboard_data): ein rollierendes Fenster ist
        IMMER vollständig (keine "bisher gelaufene Periode"-Problematik wie
        bei der kalendarischen Ansicht), der Vergleich bleibt dadurch auch
        z. B. am 1. eines Monats aussagekräftig."""
        if not entity_id:
            return {}, False
        result = query_mod.query_series(
            self.deps.data_dir, self.deps.index, entity_id, range_key,
            self.deps.tz, now, offset=offset, read_cache=read_cache, continuous=continuous,
        )
        series = {p["ts"]: (p["value"] or 0.0) for p in result["points"]}
        finer_range = _FINER_RANGE.get(range_key)
        if not continuous and offset == 0 and finer_range and series:
            # Letzten (noch laufenden) Bucket durch die feinere Aufschlüsselung
            # desselben Zeitraums ersetzen (siehe _FINER_RANGE oben).
            del series[max(series)]
            finer_result = query_mod.query_series(
                self.deps.data_dir, self.deps.index, entity_id, finer_range,
                self.deps.tz, now, offset=0, read_cache=read_cache,
            )
            for point in finer_result["points"]:
                series[point["ts"]] = point["value"] or 0.0
        entity = self.deps.index.get_entity(entity_id)
        stale = True
        if entity is not None and entity["last_ts"]:
            stale = (now.timestamp() - entity["last_ts"]) > STALE_SECONDS
        return series, stale

    @staticmethod
    def _series_total(series: dict[float, float]) -> float:
        return round(sum(series.values()), 3)

    @staticmethod
    def _capacity_weighted_avg(pairs: list[tuple[float, float]]) -> float | None:
        """Kapazitätsgewichteter Schnitt über mehrere Speicher (z. B. SOC %) —
        ein einfacher Durchschnitt würde "ein Speicher leer, einer voll"
        fälschlich als "50 %" ausweisen, obwohl der tatsächliche
        Gesamtfüllstand von den jeweiligen Kapazitäten abhängt. Ein Speicher
        ohne hinterlegte Kapazität geht mit Gewicht 1 ein — schlechtere
        Näherung als eine echte Kapazitätsangabe, aber besser, als ihn ganz
        aus dem Schnitt herauszulassen."""
        total_weight = sum(w for _, w in pairs)
        if total_weight <= 0:
            return None
        return sum(v * w for v, w in pairs) / total_weight

    def _monthly_sum_by_year(
        self, entity_ids: list[str], now: datetime, read_cache: query_mod.QueryReadCache,
    ) -> dict[float, float]:
        """Summierte Monats-Buckets (über ggf. mehrere Entitäten, z. B. alle
        Erzeuger zusammen) für die letzten 3 Kalenderjahre — Baustein für die
        Trend-Popups (Autarkie/Eigenverbrauch/Wirkungsgrad). "year" liefert
        Monats-Buckets, aber jeweils nur für EIN Kalenderjahr, daher über
        mehrere offsets hinweg zusammengeführt. 3 Jahre ist derselbe
        Kompromiss wie beim Wirkungsgrad-Trend: genug für eine erkennbare
        Kurve, ohne beliebig viele Einzel-Abfragen zu brauchen."""
        merged: dict[float, float] = {}
        for year_offset in range(0, -3, -1):
            for entity_id in entity_ids:
                if not entity_id:
                    continue
                series, _ = self._entity_series(entity_id, "year", year_offset, now, read_cache)
                for ts, value in series.items():
                    merged[ts] = merged.get(ts, 0.0) + value
        return merged

    @staticmethod
    def _series_merge(*series: dict[float, float], factors: list[float] | None = None) -> dict[float, float]:
        """Bucket-weise Summe (optional mit Vorzeichen/Faktor je Serie, für
        z. B. 'Ladung minus Entladung') über mehrere gleich getaktete
        Serien — alle Rollen laufen über denselben range_key/offset/now, ihre
        Bucket-Zeitstempel sind daher deckungsgleich."""
        factors = factors or [1.0] * len(series)
        merged: dict[float, float] = {}
        for one_series, factor in zip(series, factors):
            for ts, value in one_series.items():
                merged[ts] = merged.get(ts, 0.0) + factor * value
        return merged

    @staticmethod
    def _sparkline(series: dict[float, float]) -> list[float]:
        """Kumulierte Bucket-Werte statt der einzelnen Bucket-Deltas: die
        KPI-Kacheln zeigen eine Periodensumme (z. B. "Erzeugung: 8,6 kWh"),
        eine Sparkline aus den einzelnen Bucket-Deltas kann dabei fallen
        (ein sonnenärmerer Tag nach einem sonnigen) und wirkt dadurch wie ein
        Rückgang, obwohl die zugrunde liegende Größe (ein Zähler) monoton
        steigt. Kumulativ steigt die Linie durchgehend und endet exakt am
        angezeigten Periodenwert — löst nebenbei auch das Problem eines
        "abstürzenden" letzten (noch nicht abgeschlossenen) Buckets, ganz
        ohne ihn eigens verwerfen zu müssen."""
        running = 0.0
        result = []
        for ts in sorted(series):
            running += series[ts]
            result.append(round(running, 3))
        return result

    @staticmethod
    def _compare_kpi(current: dict, previous: dict) -> dict:
        """Prozentuale Veränderung je KPI gegenüber der Vorperiode.
        speicher_soc bewusst ausgelassen — das ist ein Prozentwert (Ø-
        Ladezustand), keine Energiemenge; "Veränderung in %" davon wäre eine
        Prozentpunkt-Differenz und würde neben den echten %-Änderungen der
        anderen KPIs falsch gelesen."""
        result: dict[str, dict] = {}
        for key in ("erzeugung", "verbrauch", "netzbezug", "speicher_netto", "einspeisung"):
            prev_value = previous.get(key)
            cur_value = current.get(key)
            if prev_value is None or cur_value is None:
                result[key] = {"pct": None, "abs": None}
                continue
            if key == "speicher_netto":
                # Vorzeichenbehafteter Saldo (Ladung − Entladung) statt einer
                # stets nicht-negativen Summe wie bei den übrigen KPIs — kann
                # positiv, negativ oder genau 0 sein. "%" ist dafür nie
                # zuverlässig lesbar (Division durch nahe-0, Vorzeichenwechsel
                # zwischen den Perioden), daher hier IMMER die absolute
                # kWh-Differenz statt %, unabhängig vom Vorperioden-Wert —
                # anders als unten wird das auch bei exakt 0 gezeigt (0,0 kWh
                # ist eine ebenso gültige Aussage wie jeder andere Wert).
                result[key] = {"pct": None, "abs": round(cur_value - prev_value, 2)}
                continue
            if prev_value == 0:
                # % ist bei einer Vorperiode von exakt 0 mathematisch
                # undefiniert (Division durch 0). Kommt bei diesen stets
                # nicht-negativen KPIs praktisch nie vor, aber falls doch:
                # absolute Differenz als Fallback, nur wenn sie selbst
                # ungleich 0 ist (sonst gäbe es ohnehin nichts zu berichten).
                delta = round(cur_value - prev_value, 2)
                result[key] = {"pct": None, "abs": delta if delta != 0 else None}
                continue
            result[key] = {"pct": round((cur_value - prev_value) / abs(prev_value) * 100, 1), "abs": None}
        return result

    def _display_name(self, entity_id: str, given_name: str) -> str:
        """Vorrang: eigener Name > HA-Friendly-Name > Entity-ID als letzter
        Ausweg. Serverseitig maßgeblich (das Formular füllt den Namen beim
        Auswählen zwar schon client-seitig vor, aber ein leer gelassenes oder
        wieder gelöschtes Namensfeld soll trotzdem nie die rohe Entity-ID
        zeigen, wenn ein Friendly-Name bekannt ist)."""
        given_name = (given_name or "").strip()
        if given_name:
            return given_name
        entity = self.deps.index.get_entity(entity_id)
        if entity is None:
            return entity_id
        return entity_display_name(entity_id, entity["friendly_name"], entity["custom_name"])

    def _config_entity_roles(self, config: dict) -> list[tuple[str, str]]:
        """Alle konfigurierten (entity_id, Rollen-Label)-Paare — Grundlage für
        die Datenqualitäts-Prüfungen unten, die (anders als der eigentliche
        Fluss-Aufbau) über ALLE Rollen laufen, unabhängig vom Sankey. Der
        Ladezustand (SOC) bleibt bewusst außen vor: der ist ein Gauge (%),
        keine kWh-Zähler-Rolle — Einheit/Zählertyp-Prüfungen würden ihn sonst
        fälschlich als Fehler markieren."""
        roles: list[tuple[str, str]] = []
        if config.get("netzbezug"):
            roles.append((config["netzbezug"], "Netzbezug"))
        if config.get("einspeisung"):
            roles.append((config["einspeisung"], "Einspeisung"))
        for erz in config.get("erzeuger") or []:
            entity_id = erz.get("entity_id")
            if entity_id:
                roles.append((entity_id, self._display_name(entity_id, erz.get("name", ""))))
        for verbraucher in config.get("verbraucher") or []:
            entity_id = verbraucher.get("entity_id")
            if entity_id:
                roles.append((entity_id, self._display_name(entity_id, verbraucher.get("name", ""))))
        for sp in config.get("speicher") or []:
            sp_name = sp.get("name") or "Speicher"
            if sp.get("laden_entity_id"):
                roles.append((sp["laden_entity_id"], f"{sp_name} (Ladung)"))
            if sp.get("entladen_entity_id"):
                roles.append((sp["entladen_entity_id"], f"{sp_name} (Entladung)"))
        return roles

    def _check_entity_metadata(self, entity_roles: list[tuple[str, str]]) -> dict:
        """Einheit (kWh), Aggregationstyp (Zähler) und doppelt zugeordnete
        Entitäten je Rolle — typische Einrichtungsfehler (z. B. ein
        Leistungs- statt Energiezähler zugeordnet, oder dieselbe Entität aus
        Versehen zweimal), die sonst erst an unplausiblen Summen auffallen
        würden statt direkt benannt zu werden."""
        unit_issues: list[str] = []
        type_issues: list[str] = []
        seen: dict[str, list[str]] = {}
        for entity_id, label in entity_roles:
            entity = self.deps.index.get_entity(entity_id)
            if entity is None:
                continue
            if entity["unit"] and entity["unit"] != "kWh":
                unit_issues.append(f"{label} ({entity['unit']})")
            if entity["aggregation_type"] != "counter":
                type_issues.append(label)
            seen.setdefault(entity_id, []).append(label)
        duplicate_labels = [" / ".join(labels) for labels in seen.values() if len(labels) > 1]
        return {"unit_issues": unit_issues, "type_issues": type_issues, "duplicate_labels": duplicate_labels}

    def _check_counter_resets(
        self, entity_roles: list[tuple[str, str]], window_start_ts: float, window_end_ts: float,
        now: datetime, read_cache: query_mod.QueryReadCache,
    ) -> tuple[list[str], bool]:
        """Rohwerte je Rolle im Fenster auf abnehmende Zählerstände prüfen
        (Reset/Zählertausch/Neustart der Integration). rollup.py behandelt
        das für die Summenbildung bereits transparent (Delta wird dort auf 0
        gekappt, siehe compute_fine_rollup_with_key), verwirft die
        Information dabei aber — hier unabhängig davon, rein für die
        Anzeige. Gibt zusätzlich zurück, ob wirklich JEDE Rolle geprüft
        werden konnte (False, wenn eine Prüfung wegen zu vieler Rohwerte
        abgebrochen wurde — dann lieber ehrlich "nicht geprüft" zeigen, als
        einen falschen "unauffällig"-Status vorzutäuschen)."""
        reset_labels: list[str] = []
        fully_checked = True
        for entity_id, label in entity_roles:
            try:
                rows = sorted(cleanup_mod.iter_raw_rows(
                    self.deps.data_dir, self.deps.index, entity_id, window_start_ts, window_end_ts,
                    self.deps.tz, now=now, max_rows=RESET_CHECK_MAX_ROWS,
                    hot_rows_loader=read_cache.read_hot_rows,
                ))
            except cleanup_mod.ResultLimitExceeded:
                fully_checked = False
                continue
            previous_value = None
            for _ts, value in rows:
                if previous_value is not None and value < previous_value - 0.001:
                    reset_labels.append(label)
                    break
                previous_value = value
        return reset_labels, fully_checked

    def compute_flow(
        self, config: dict, range_key: str, offset: int, continuous: bool = False, skip_quality: bool = False,
        read_cache: query_mod.QueryReadCache | None = None,
    ) -> dict:
        """read_cache optional von außen durchgereicht (main.py/hier unten:
        mehrere compute_flow()-Aufrufe desselben Requests teilen sich dann
        einen Cache, statt sich überlappende Hot-Buffer-Dateien mehrfach
        unabhängig voneinander neu einzulesen — QueryReadCache selbst bleibt
        request-lokal, siehe dessen Docstring in query.py, wird hier nur
        NICHT mehr implizit pro Aufruf neu erzeugt)."""
        if range_key not in RANGE_KEYS:
            raise HTTPException(status_code=400, detail="Ungültiger Zeitraum")
        now = datetime.now(self.deps.tz)
        if read_cache is None:
            read_cache = query_mod.QueryReadCache()
        window_start, window_end, _period_end = query_mod._window(  # noqa: SLF001 — siehe Modul-Docstring
            range_key, now.astimezone(self.deps.tz), offset, continuous,
        )

        def entity_series(entity_id: str) -> tuple[dict[float, float], bool]:
            return self._entity_series(entity_id, range_key, offset, now, read_cache, continuous=continuous)
        # Name des zentralen Sammelknotens — frei benennbar (Default "Haus"),
        # da es dafür keine feste HA-Konvention gibt (siehe Recherche zum
        # offiziellen Energy-Dashboard: der dortige Sankey benennt seinen
        # Sammelpunkt gar nicht prominent).
        hub_name = (config.get("hub_name") or "").strip() or DEFAULT_HUB_NAME

        nodes: list[dict] = []
        links: list[dict] = []
        stale_labels: list[str] = []

        def add_stale_issue(label: str, stale: bool) -> None:
            # Dieselbe Preis-Entität kann mehrfach durchlaufen ("Netzbezug
            # Kosten" und "Vermiedene Kosten" nutzen denselben Netzbezug-
            # Preis) — ohne den in-Check würde ihr Label doppelt in der
            # Meldung auftauchen.
            if stale and label not in stale_labels:
                stale_labels.append(label)

        bus_in = 0.0
        erzeuger_sum = 0.0
        erzeuger_series_list: list[dict[float, float]] = []
        erzeuger_breakdown: list[dict] = []

        netzbezug_id = config.get("netzbezug") or ""
        netzbezug_val = 0.0
        netzbezug_series: dict[float, float] = {}
        if netzbezug_id:
            netzbezug_series, stale = entity_series(netzbezug_id)
            netzbezug_val = self._series_total(netzbezug_series)
            nodes.append({
                "name": "Netzbezug", "entity_id": netzbezug_id, "role": "source",
                "value": max(netzbezug_val, 0.0), "stale": stale,
            })
            links.append({"source": "Netzbezug", "target": hub_name, "value": max(netzbezug_val, 0.0)})
            bus_in += netzbezug_val
            add_stale_issue("Netzbezug", stale)

        for erz in config.get("erzeuger") or []:
            entity_id = erz.get("entity_id")
            if not entity_id:
                continue
            name = self._display_name(entity_id, erz.get("name", ""))
            series, stale = entity_series(entity_id)
            val = self._series_total(series)
            nodes.append({
                "name": name, "entity_id": entity_id, "role": "source",
                "value": max(val, 0.0), "stale": stale,
            })
            links.append({"source": name, "target": hub_name, "value": max(val, 0.0)})
            bus_in += val
            erzeuger_sum += val
            erzeuger_series_list.append(series)
            erzeuger_breakdown.append({"name": name, "value": round(max(val, 0.0), 3)})
            add_stale_issue(name, stale)

        speicher = config.get("speicher") or {}
        speicher_name = speicher.get("name") or "Speicher"
        speicher_entladen_val = 0.0
        speicher_laden_val = 0.0
        speicher_entladen_series: dict[float, float] = {}
        speicher_laden_series: dict[float, float] = {}
        speicher_soc = None
        if speicher.get("entladen_entity_id"):
            speicher_entladen_series, stale = entity_series(speicher["entladen_entity_id"])
            speicher_entladen_val = self._series_total(speicher_entladen_series)
            label = f"{speicher_name} (Entladung)"
            nodes.append({
                "name": label, "entity_id": speicher["entladen_entity_id"], "role": "source",
                "kind": "storage_out",
                "value": max(speicher_entladen_val, 0.0), "stale": stale,
            })
            links.append({"source": label, "target": hub_name, "value": max(speicher_entladen_val, 0.0)})
            bus_in += speicher_entladen_val
            add_stale_issue(label, stale)
        if speicher.get("laden_entity_id"):
            speicher_laden_series, stale = entity_series(speicher["laden_entity_id"])
            speicher_laden_val = self._series_total(speicher_laden_series)
            add_stale_issue(f"{speicher_name} (Ladung)", stale)
        speicher_soc_kwh = None
        if speicher.get("soc_entity_id"):
            # SOC ist ein Gauge (%), nicht Zähler — "auto" ist hier der
            # Perioden-Durchschnitt, ein plausibler Kompakt-Wert für die KPI.
            soc_series, _stale = entity_series(speicher["soc_entity_id"])
            if soc_series:
                speicher_soc = round(sum(soc_series.values()) / len(soc_series), 1)
                capacity_kwh = speicher.get("capacity_kwh")
                if capacity_kwh:
                    # % ist die primäre, immer verständliche Einheit; die
                    # kWh-Entsprechung nur als Zusatz, wenn eine Kapazität
                    # hinterlegt ist — sonst gäbe es nichts, woraus sich kWh
                    # ableiten ließe.
                    speicher_soc_kwh = round(speicher_soc / 100 * capacity_kwh, 1)

        # Aktueller (Jetzt-)Füllstand, unabhängig vom gewählten Zeitraum —
        # speicher_soc oben ist der Ø-Wert ÜBER DIE PERIODE (bei Monat/Jahr
        # kann das deutlich vom tatsächlichen Stand gerade eben abweichen).
        # index.get_entity()["last_value"] wäre die billigere Variante (kein
        # Datei-Zugriff), ist aber NICHT zuverlässig: die Spalte wird nur bei
        # laufender Live-Ingestion gepflegt, nicht bei per reconcile()
        # neu aufgebauten Metadaten (z. B. nach Reparatur/Bulk-Import) — dort
        # bleibt last_value trotz vorhandener Daten None (derselbe blinde
        # Fleck, den auch die Dashboard-Kachel in main.py hat, dort mit "–"
        # sichtbar gemacht statt verschleiert). _boundary_value() liest
        # stattdessen den tatsächlich letzten Rohwert aus Hot-Buffer/Archiv.
        speicher_soc_now = None
        speicher_soc_now_kwh = None
        if speicher.get("soc_entity_id"):
            raw_value = query_mod._boundary_value(  # noqa: SLF001 — siehe Modul-Docstring
                self.deps.data_dir, speicher["soc_entity_id"], now.timestamp() + 1, self.deps.tz, read_cache,
            )
            if raw_value is not None:
                speicher_soc_now = round(raw_value, 1)
                capacity_kwh = speicher.get("capacity_kwh")
                if capacity_kwh:
                    speicher_soc_now_kwh = round(speicher_soc_now / 100 * capacity_kwh, 1)

        # Trend fürs Popup (Klick auf den Speicher-SOC-Ring) — anders als
        # Wirkungsgrad braucht SOC keine Ratio-Berechnung, der Monats-Bucket
        # ist bei einem Gauge (%) schon der Ø-Wert dieses Monats. Unabhängig
        # von Laden/Entladen, nur an soc_entity_id gebunden.
        speicher_soc_trend: list[dict] = []
        if not (continuous or skip_quality) and speicher.get("soc_entity_id"):
            soc_monthly = self._monthly_sum_by_year([speicher["soc_entity_id"]], now, read_cache)
            by_year_soc: dict[str, list[float | None]] = {}
            for ts in sorted(soc_monthly):
                dt = datetime.fromtimestamp(ts, self.deps.tz)
                year_key = dt.strftime("%Y")
                by_year_soc.setdefault(year_key, [None] * 12)[dt.month - 1] = round(soc_monthly[ts], 1)
            speicher_soc_trend = [
                {"year": year_key, "months": by_year_soc[year_key]} for year_key in sorted(by_year_soc)
            ]

        # PV-Prognose: dieselbe "aktueller Rohwert statt Perioden-Query"-Logik
        # wie beim Jetzt-Füllstand oben — eine Prognose-Entität (z. B.
        # Forecast.Solar "Geschätzte Energieerzeugung – Resttag") liefert
        # einen einzigen, ständig aktualisierten kWh-Wert, keine über Tag/
        # Monat/Jahr aggregierbare Zeitreihe. Bewusst kein fester Fallback-
        # Wert wie bei Kosten/CO2 — eine Prognose lässt sich nicht sinnvoll
        # als fixe Zahl eintragen.
        prognose = config.get("prognose") or {}
        prognose_rest_heute = None
        prognose_morgen = None
        if prognose.get("rest_heute_entity_id"):
            raw_value = query_mod._boundary_value(  # noqa: SLF001 — siehe Modul-Docstring
                self.deps.data_dir, prognose["rest_heute_entity_id"], now.timestamp() + 1, self.deps.tz, read_cache,
            )
            if raw_value is not None:
                prognose_rest_heute = round(raw_value, 1)
        if prognose.get("morgen_entity_id"):
            raw_value = query_mod._boundary_value(  # noqa: SLF001 — siehe Modul-Docstring
                self.deps.data_dir, prognose["morgen_entity_id"], now.timestamp() + 1, self.deps.tz, read_cache,
            )
            if raw_value is not None:
                prognose_morgen = round(raw_value, 1)

        speicher_efficiency = None
        speicher_efficiency_trend: list[dict] = []
        if (
            not (continuous or skip_quality)
            and speicher.get("laden_entity_id")
            and speicher.get("entladen_entity_id")
        ):
            # Selbst berechnet statt manuell im Setup einzutragen (vorher
            # "Wirkungsgrad"-Feld, ohne Auswirkung auf die Berechnung) —
            # Entladung/Ladung über die GESAMTE bisherige Historie statt nur
            # die angezeigte Periode, weil sich ein unterschiedlicher Start-/
            # End-Füllstand über viele Lade-/Entladezyklen hinweg
            # herausmittelt und so eine deutlich stabilere Schätzung ergibt.
            # "decade" (10 Jahre) ist der größte vorhandene range_key in
            # query.py und deckt die Speicher-Lebensdauer damit praktisch
            # immer komplett ab.
            laden_all, _ = self._entity_series(speicher["laden_entity_id"], "decade", 0, now, read_cache)
            entladen_all, _ = self._entity_series(speicher["entladen_entity_id"], "decade", 0, now, read_cache)
            laden_all_total = self._series_total(laden_all)
            entladen_all_total = self._series_total(entladen_all)

            # Korrektur um den aktuell noch im Speicher steckenden Füllstand:
            # ohne sie zählt "Ladung" auch Energie mit, die noch gar nicht
            # wieder entladen wurde (verfälscht die Quote vor allem bei kurzer
            # Historie oder kurz nach einer großen Ladung). Nötig dafür: SOC
            # am Anfang UND am Ende der Historie (erster/letzter Bucket
            # derselben "decade"-Abfrage) plus eine hinterlegte Kapazität, um
            # die %-Differenz in kWh umzurechnen — fehlt eins davon, bleibt es
            # bei der einfachen (unkorrigierten) Quote als Fallback.
            capacity_kwh = speicher.get("capacity_kwh")
            corrected_laden_total = laden_all_total
            if capacity_kwh and speicher.get("soc_entity_id"):
                soc_all, _ = self._entity_series(speicher["soc_entity_id"], "decade", 0, now, read_cache)
                if len(soc_all) >= 2:
                    soc_start = soc_all[min(soc_all)]
                    soc_end = soc_all[max(soc_all)]
                    delta_kwh = (soc_end - soc_start) / 100.0 * capacity_kwh
                    corrected_laden_total = laden_all_total - delta_kwh

            if corrected_laden_total > 0:
                speicher_efficiency = round(max(0.0, min(1.0, entladen_all_total / corrected_laden_total)) * 100, 1)
            elif laden_all_total > 0:
                speicher_efficiency = round(max(0.0, min(1.0, entladen_all_total / laden_all_total)) * 100, 1)

            # Trend fürs Popup (Klick auf den Wirkungsgrad-Ring): monatliche
            # statt jährliche Auflösung — "decade" liefert nur Jahres-Buckets
            # (siehe laden_all/entladen_all oben, weiterhin für die
            # Momentaufnahme genutzt), für Monate braucht es stattdessen
            # "year"-Abfragen (die liefern Monats-Buckets, aber jeweils nur
            # für EIN Kalenderjahr) über mehrere offsets. 3 Jahre zurück ist
            # ein Kompromiss: genug für eine erkennbare Kurve, ohne beliebig
            # viele Einzel-Abfragen zu brauchen. Bewusst OHNE die SOC-
            # Korrektur von oben (die lohnt sich vor allem bei kurzen
            # Fenstern; ein Monat mit normaler Nutzung hat i. d. R. schon ein
            # Vielfaches der Speicherkapazität umgesetzt) — hält den Trend
            # einfach statt pro Monat eine eigene Korrektur zu brauchen.
            monthly_laden: dict[float, float] = {}
            monthly_entladen: dict[float, float] = {}
            for year_offset in range(0, -3, -1):
                laden_year, _ = self._entity_series(speicher["laden_entity_id"], "year", year_offset, now, read_cache)
                entladen_year, _ = self._entity_series(speicher["entladen_entity_id"], "year", year_offset, now, read_cache)
                monthly_laden.update(laden_year)
                monthly_entladen.update(entladen_year)
            # Eine Zeile je Jahr mit einer festen 12-Slot-Sparkline (Jan..Dez)
            # statt einer einzigen durchgehenden Kurve über alle Monate —
            # fehlende Monate (vor Speicher-Inbetriebnahme oder noch in der
            # Zukunft, beim laufenden Jahr) bleiben als Lücke im jeweiligen
            # Slot statt die Kurve zusammenzustauchen, damit sich Jahre direkt
            # untereinander vergleichen lassen (Jan..Dez immer an derselben
            # x-Position, wie beim Tageslastprofil mit den 24 Stunden-Zellen).
            by_year: dict[str, list[float | None]] = {}
            for ts in sorted(monthly_laden):
                month_laden = monthly_laden.get(ts, 0.0)
                month_entladen = monthly_entladen.get(ts, 0.0)
                if month_laden <= 0:
                    continue
                dt = datetime.fromtimestamp(ts, self.deps.tz)
                year_key = dt.strftime("%Y")
                by_year.setdefault(year_key, [None] * 12)[dt.month - 1] = round(
                    max(0.0, min(1.0, month_entladen / month_laden)) * 100, 1
                )
            speicher_efficiency_trend = [
                {"year": year_key, "months": by_year[year_key]} for year_key in sorted(by_year)
            ]

        # Verbraucher hängen im Sankey je nach Gruppen-Zuordnung ein- oder
        # zweistufig am Bus: mit Gruppe Bus -> Gruppenname (Summe der
        # Gruppen-Mitglieder) -> einzelnes Gerät; ohne Gruppe direkt
        # Bus -> Gerät, wie Erzeuger/Einspeisung. Ersetzt den früheren
        # einzelnen "Verbraucher"-Sammelknoten für ALLE Geräte: der lag bei
        # sehr ungleichen Größenordnungen (ein Klumpen neben Einspeisung/
        # Grundlast) sichtbar quer zu ECharts' Sankey-Layout und erzeugte
        # unruhige, sich kreuzende Bänder. Mehrere, vom Nutzer frei benannte
        # Gruppen liegen typischerweise näher an der Größenordnung ihrer
        # Geschwisterknoten. Gruppen werden nach absteigendem Gesamtwert
        # sortiert (größte zuerst) — reduziert Kreuzungen zusätzlich. Der
        # Farbmix (blend) sitzt nur auf Bus -> Gruppe bzw. Bus -> Gerät
        # (ungruppiert); Gruppe -> Gerät nutzt den normalen Verlaufs-Farbton,
        # da sich "wie grün" nicht sinnvoll weiter auf einzelne Geräte
        # herunterbrechen lässt (dieselbe Begründung wie bei Grundlast).
        #
        # Kollisionshinweis: ein Gruppenname, der zufällig mit einem anderen
        # Knoten übereinstimmt (Bus-Name, "Grundlast", "Einspeisung", ein
        # anderer Rollen-Name), würde im Sankey denselben Knoten teilen —
        # bewusst nicht validiert (sehr unwahrscheinlich, kein Datenverlust,
        # nur ein optisch verwirrender Sankey).
        # Auffälligkeiten (Schwellenwert-Färbung): wie bei den Trend-Popups
        # nur für die tatsächlich angezeigte Periode nötig, nicht für die
        # verworfenen Vergleichs-/Heatmap-Hilfsaufrufe — sonst würde sich die
        # zusätzliche Baseline-Abfrage je Verbraucher (ANOMALIE_BASELINE_PERIODS
        # Perioden zurück) mit jedem dieser Aufrufe vervielfachen. Bewusst nur
        # für Verbraucher/Gruppen, NICHT für Grundlast — die ist ein
        # rechnerischer Rest ohne eigenen Sensor; ihre Baseline würde eine
        # komplette Bus-Bilanz je Vergleichsperiode neu berechnen (alle
        # Quellen erneut abfragen) und wäre damit der mit Abstand teuerste
        # Teil, für einen Wert, den man ohnehin nicht gezielt "reparieren"
        # kann.
        anomalie_schwelle = config.get("anomalie_schwelle") or "50"
        anomalie_active = not (continuous or skip_quality) and anomalie_schwelle != "off"
        anomalie_factor = 1 + int(anomalie_schwelle) / 100 if anomalie_active else None
        anomalien: list[dict] = []

        def _anomaly_baseline(entity_id: str) -> float | None:
            values = [
                self._series_total(self._entity_series(entity_id, range_key, offset - i, now, read_cache)[0])
                for i in range(1, ANOMALIE_BASELINE_PERIODS + 1)
            ]
            return sum(values) / len(values) if values else None

        verbraucher_sum = 0.0
        verbraucher_display_sum = 0.0
        verbraucher_series_list: list[dict[float, float]] = []
        verbraucher_breakdown: list[dict] = []
        # Bucket-Serien je Verbraucher (+ später Grundlast) für die
        # Kosten-Spalte in der Verbraucheranteile-Tabelle — Name als Schlüssel
        # statt Index, weil verbraucher_breakdown weiter unten sortiert wird.
        verbraucher_series_by_name: dict[str, dict[float, float]] = {}
        gruppen_totals: dict[str, float] = {}
        gruppen_baseline_totals: dict[str, float] = {}
        for verbraucher in config.get("verbraucher") or []:
            entity_id = verbraucher.get("entity_id")
            if not entity_id:
                continue
            name = self._display_name(entity_id, verbraucher.get("name", ""))
            gruppe = (verbraucher.get("gruppe") or "").strip() or None
            series, stale = entity_series(entity_id)
            val = self._series_total(series)
            display_val = max(val, 0.0)
            node = {
                "name": name, "entity_id": entity_id, "role": "sink",
                "value": display_val, "stale": stale,
            }
            if anomalie_active:
                # Baseline == 0 (Gerät lief in keiner der Vergleichsperioden)
                # bewusst nicht bewertet — sonst würde ein Gerät, das schlicht
                # nicht jeden Tag läuft (Waschmaschine), bei praktisch jedem
                # normalen Lauf als "unendlich % über dem Schnitt" markiert.
                baseline = _anomaly_baseline(entity_id)
                if gruppe and baseline is not None:
                    gruppen_baseline_totals[gruppe] = gruppen_baseline_totals.get(gruppe, 0.0) + baseline
                if baseline and display_val > baseline * anomalie_factor:
                    pct = round((display_val / baseline - 1) * 100)
                    node["anomaly"] = True
                    node["anomaly_pct"] = pct
                    node["anomaly_baseline"] = round(baseline, 3)
                    if not gruppe:
                        anomalien.append({
                            "name": name, "value": round(display_val, 3),
                            "baseline": round(baseline, 3), "pct": pct,
                        })
            nodes.append(node)
            if gruppe:
                links.append({"source": gruppe, "target": name, "value": display_val})
                gruppen_totals[gruppe] = gruppen_totals.get(gruppe, 0.0) + display_val
            else:
                links.append({"source": hub_name, "target": name, "value": display_val})
            verbraucher_sum += val
            verbraucher_display_sum += display_val
            verbraucher_series_list.append(series)
            verbraucher_breakdown.append({
                "name": name, "value": round(display_val, 3), "entity_id": entity_id, "gruppe": gruppe,
            })
            verbraucher_series_by_name[name] = series
            add_stale_issue(name, stale)
        for gruppe_name in sorted(gruppen_totals, key=gruppen_totals.get, reverse=True):  # type: ignore[arg-type]
            total = gruppen_totals[gruppe_name]
            if total <= 0:
                continue
            gruppe_node = {"name": gruppe_name, "role": "sink", "blend": True, "value": round(total, 3)}
            if anomalie_active:
                gruppe_baseline = gruppen_baseline_totals.get(gruppe_name)
                if gruppe_baseline and total > gruppe_baseline * anomalie_factor:
                    pct = round((total / gruppe_baseline - 1) * 100)
                    gruppe_node["anomaly"] = True
                    gruppe_node["anomaly_pct"] = pct
                    gruppe_node["anomaly_baseline"] = round(gruppe_baseline, 3)
                    anomalien.append({
                        "name": gruppe_name, "value": round(total, 3),
                        "baseline": round(gruppe_baseline, 3), "pct": pct,
                    })
            nodes.append(gruppe_node)
            links.append({"source": hub_name, "target": gruppe_name, "value": round(total, 3)})
        anomalien.sort(key=lambda a: a["pct"], reverse=True)

        einspeisung_id = config.get("einspeisung") or ""
        einspeisung_val = 0.0
        einspeisung_series: dict[float, float] = {}
        if einspeisung_id:
            einspeisung_series, stale = entity_series(einspeisung_id)
            einspeisung_val = self._series_total(einspeisung_series)
            nodes.append({
                "name": "Einspeisung", "entity_id": einspeisung_id, "role": "sink",
                "value": max(einspeisung_val, 0.0), "stale": stale,
            })
            links.append({"source": hub_name, "target": "Einspeisung", "value": max(einspeisung_val, 0.0)})
            add_stale_issue("Einspeisung", stale)

        if speicher.get("laden_entity_id"):
            label = f"{speicher_name} (Ladung)"
            nodes.append({
                "name": label, "entity_id": speicher["laden_entity_id"], "role": "sink",
                "kind": "storage_in",
                "value": max(speicher_laden_val, 0.0), "stale": False,
            })
            links.append({"source": hub_name, "target": label, "value": max(speicher_laden_val, 0.0)})

        # Grundlast ist der algebraische Rest, kein eigener Sensor — negativ
        # bedeutet: Verbraucher+Einspeisung+Speicherladung übersteigen den Bus,
        # physikalisch unmöglich und damit das eigentliche Bilanz-Warnsignal
        # (falsches Vorzeichen, doppelt gezählter Sensor, o. ä.).
        grundlast = bus_in - verbraucher_sum - einspeisung_val - speicher_laden_val
        grundlast_negative = grundlast < -0.01
        grundlast_display = max(grundlast, 0.0)
        nodes.append({"name": "Grundlast", "role": "sink", "blend": True, "value": round(grundlast_display, 3)})
        links.append({"source": hub_name, "target": "Grundlast", "value": round(grundlast_display, 3)})

        nodes.insert(0, {"name": hub_name, "role": "bus", "value": round(max(bus_in, 0.0), 3)})

        # Anteil "grüner" (nicht aus dem Netz stammender) Energie am Bus —
        # EIN Wert für die ganze Periode (nicht je Verbraucher, das ließe
        # sich nach der Vermischung am Bus nicht mehr zurückrechnen), färbt
        # im Frontend die Flüsse Bus→Verbraucher/Grundlast als PV/Netz-
        # Farbverlauf statt einer Einheitsfarbe (siehe renderChart()).
        green_ratio = None
        if bus_in > 0:
            green_ratio = round(max(0.0, min(1.0, (bus_in - max(netzbezug_val, 0.0)) / bus_in)), 3)

        erzeugung_total = round(erzeuger_sum, 3)
        verbrauch_total = round(verbraucher_sum + grundlast_display, 3)
        netzbezug_total = round(max(netzbezug_val, 0.0), 3)
        einspeisung_total = round(max(einspeisung_val, 0.0), 3)

        # Autarkiegrad: welcher Anteil des Verbrauchs kam NICHT aus dem Netz.
        # Eigenverbrauchsquote: welcher Anteil der Erzeugung wurde selbst
        # verbraucht (nicht eingespeist). Die beiden Standard-Kennzahlen aus
        # HA's eigenem Energy-Dashboard — bewusst aus den ohnehin schon
        # berechneten KPI-Summen abgeleitet, kein eigener Datenpfad. Auf
        # 0..100 gedeckelt (Rundungs-/Vorzeichenrauschen bei sehr kleinen
        # Summen könnte sonst leicht über/unter die plausible Spanne rutschen).
        autarkie = None
        if verbrauch_total > 0:
            autarkie = round(max(0.0, min(1.0, 1 - netzbezug_total / verbrauch_total)) * 100, 1)
        eigenverbrauch = None
        if erzeugung_total > 0:
            eigenverbrauch = round(max(0.0, min(1.0, (erzeugung_total - einspeisung_total) / erzeugung_total)) * 100, 1)

        # Trends fürs Popup (Klick auf den Autarkie-/Eigenverbrauch-Ring) —
        # dieselben Formeln wie oben, aber monatlich statt für die ganze
        # Periode. Statt die komplette Bus-/Grundlast-Bilanz je Monat neu
        # aufzubauen (aufwendig), wird dieselbe Erhaltungs-Identität wie oben
        # genutzt: bus_in = Netzbezug + Erzeugung + Speicher-Entladung,
        # Verbrauch = bus_in − Einspeisung − Speicherladung (genau das, was
        # verbrauch_total oben auch ist — Verbraucher+Grundlast zusammen).
        autarkie_trend: list[dict] = []
        eigenverbrauch_trend: list[dict] = []
        if not (continuous or skip_quality):
            erzeuger_ids = [erz.get("entity_id") for erz in (config.get("erzeuger") or []) if erz.get("entity_id")]
            netzbezug_monthly = self._monthly_sum_by_year([netzbezug_id], now, read_cache)
            erzeugung_monthly = self._monthly_sum_by_year(erzeuger_ids, now, read_cache)
            einspeisung_monthly = self._monthly_sum_by_year([config.get("einspeisung") or ""], now, read_cache)
            speicher_entladen_monthly = self._monthly_sum_by_year(
                [speicher.get("entladen_entity_id") or ""], now, read_cache
            )
            speicher_laden_monthly = self._monthly_sum_by_year(
                [speicher.get("laden_entity_id") or ""], now, read_cache
            )
            by_year_autarkie: dict[str, list[float | None]] = {}
            by_year_eigenverbrauch: dict[str, list[float | None]] = {}
            for ts in sorted(set(netzbezug_monthly) | set(erzeugung_monthly)):
                netzbezug_month = max(netzbezug_monthly.get(ts, 0.0), 0.0)
                erzeugung_month = erzeugung_monthly.get(ts, 0.0)
                einspeisung_month = max(einspeisung_monthly.get(ts, 0.0), 0.0)
                entladen_month = speicher_entladen_monthly.get(ts, 0.0)
                laden_month = speicher_laden_monthly.get(ts, 0.0)
                bus_in_month = netzbezug_month + erzeugung_month + entladen_month
                verbrauch_month = bus_in_month - einspeisung_month - laden_month
                dt = datetime.fromtimestamp(ts, self.deps.tz)
                year_key = dt.strftime("%Y")
                if verbrauch_month > 0:
                    by_year_autarkie.setdefault(year_key, [None] * 12)[dt.month - 1] = round(
                        max(0.0, min(1.0, 1 - netzbezug_month / verbrauch_month)) * 100, 1
                    )
                if erzeugung_month > 0:
                    by_year_eigenverbrauch.setdefault(year_key, [None] * 12)[dt.month - 1] = round(
                        max(0.0, min(1.0, (erzeugung_month - einspeisung_month) / erzeugung_month)) * 100, 1
                    )
            autarkie_trend = [
                {"year": year_key, "months": by_year_autarkie[year_key]} for year_key in sorted(by_year_autarkie)
            ]
            eigenverbrauch_trend = [
                {"year": year_key, "months": by_year_eigenverbrauch[year_key]}
                for year_key in sorted(by_year_eigenverbrauch)
            ]

        # Verbraucheranteile: dieselben Verbraucher-Werte wie im Sankey, plus
        # Grundlast als gleichwertiger Eintrag (beide zusammen ergeben immer
        # genau die Verbrauch-KPI-Summe) — sortiert nach Anteil, größter zuerst.
        verbraucher_breakdown.append({"name": "Grundlast", "value": round(grundlast_display, 3)})
        for item in verbraucher_breakdown:
            item["share"] = round(item["value"] / verbrauch_total * 100, 1) if verbrauch_total > 0 else None
        verbraucher_breakdown.sort(key=lambda item: item["value"], reverse=True)

        # Sparklines je KPI-Kachel — Bucket-Verlauf statt nur Periodensumme,
        # aus denselben query_series()-Punkten, die für die Summen ohnehin
        # schon geladen wurden (kein zweiter Fetch).
        erzeugung_series = self._series_merge(*erzeuger_series_list) if erzeuger_series_list else {}
        verbraucher_total_series = self._series_merge(*verbraucher_series_list) if verbraucher_series_list else {}
        bus_in_series = self._series_merge(
            netzbezug_series, erzeugung_series, speicher_entladen_series
        )
        grundlast_series = self._series_merge(
            bus_in_series, verbraucher_total_series, einspeisung_series, speicher_laden_series,
            factors=[1.0, -1.0, -1.0, -1.0],
        )
        grundlast_series_clamped = {ts: max(v, 0.0) for ts, v in grundlast_series.items()}
        verbraucher_series_by_name["Grundlast"] = grundlast_series_clamped
        verbrauch_series = self._series_merge(verbraucher_total_series, grundlast_series_clamped)
        speicher_netto_series = self._series_merge(
            speicher_laden_series, speicher_entladen_series, factors=[1.0, -1.0]
        )

        # Kosten: bucket-weise Preis×Energie statt Periodensumme×Ø-Preis —
        # macht auch dynamische/variable Tarife (z. B. Spotpreis-Sensoren)
        # korrekt mit, nicht nur einen über die Periode konstanten Preis.
        # PV-Eigenverbrauch ist keine eigene Sensor-Rolle (dafür gibt es
        # i. d. R. keinen HA-Sensor), sondern dieselbe Ableitung wie bei der
        # Eigenverbrauchsquote: Erzeugung minus Einspeisung, je Bucket auf
        # 0 gekappt (kann durch Messungenauigkeiten sonst leicht negativ
        # werden). Übersprungen bei continuous/skip_quality — dieselbe
        # Begründung wie bei den Datenqualitäts-Prüfungen: nur für die
        # tatsächlich angezeigte Periode nötig, nicht für die verworfenen
        # Vergleichs-/Heatmap-Hilfsaufrufe.
        kosten = config.get("kosten") or {}
        co2 = config.get("co2") or {}
        netzbezug_cost = None
        einspeisung_revenue = None
        vermiedene_kosten = None
        co2_ausstoss = None
        co2_vermieden = None
        if not (continuous or skip_quality):
            def _bucket_factor(
                energy_series: dict[float, float],
                factor_entity_id: str | None,
                fixed_factor: float | None,
                label: str,
            ) -> float | None:
                # Gemeinsame bucket-weise Faktor×Energie-Rechnung für Kosten
                # (€/kWh) UND CO2 (g/kWh) — Entität hat Vorrang vor dem festen
                # Faktor (bucket-genau, macht auch variable/dynamische Tarife
                # bzw. eine live CO2-Intensität korrekt mit), der feste Wert
                # ist nur der Ersatz ohne passende Entität. Bei Kosten liegt
                # der feste Wert schon in Euro vor (Formular nimmt Cent
                # entgegen, siehe energiedashboard_setup_save), bei CO2 direkt
                # in g/kWh — hier keine weitere Umrechnung nötig.
                if factor_entity_id:
                    factor_series, stale = entity_series(factor_entity_id)
                    add_stale_issue(label, stale)
                    if not factor_series:
                        return None
                    return round(
                        sum(value * factor_series[ts] for ts, value in energy_series.items() if ts in factor_series), 3
                    )
                if fixed_factor is not None:
                    return round(sum(energy_series.values()) * fixed_factor, 3)
                return None

            netzbezug_cost = _bucket_factor(
                netzbezug_series, kosten.get("preis_netzbezug"), kosten.get("preis_netzbezug_fixed"), "Preis Netzbezug"
            )
            if netzbezug_cost is not None:
                netzbezug_cost = round(netzbezug_cost, 2)
            einspeisung_revenue = _bucket_factor(
                einspeisung_series, kosten.get("preis_einspeisung"), kosten.get("preis_einspeisung_fixed"), "Preis Einspeisung"
            )
            if einspeisung_revenue is not None:
                einspeisung_revenue = round(einspeisung_revenue, 2)

            # PV-Eigenverbrauch ist keine eigene Sensor-Rolle (dafür gibt es
            # i. d. R. keinen HA-Sensor), sondern dieselbe Ableitung wie bei
            # der Eigenverbrauchsquote: Erzeugung minus Einspeisung, je Bucket
            # auf 0 gekappt (kann durch Messungenauigkeiten sonst leicht
            # negativ werden) — EINMAL berechnet, für "Vermiedene Kosten" UND
            # "Vermiedenes CO2" gemeinsam genutzt.
            pv_eigenverbrauch_series = {
                ts: max(v, 0.0)
                for ts, v in self._series_merge(erzeugung_series, einspeisung_series, factors=[1.0, -1.0]).items()
            }
            # Vermiedene Kosten braucht KEINEN eigenen Preis (das frühere
            # "PV-Eigenverbrauch-Preis"-Feld wurde entfernt) — der Wert
            # selbst verbrauchten PV-Stroms entspricht per Definition dem,
            # was man sonst für dieselbe Menge Netzbezug gezahlt hätte, also
            # PV-Eigenverbrauch (kWh) × Netzbezug-Preis, mit demselben
            # Preis/derselben Preis-Entität wie "Netzbezug Kosten" oben.
            if kosten.get("preis_netzbezug") or kosten.get("preis_netzbezug_fixed") is not None:
                vermiedene_kosten = _bucket_factor(
                    pv_eigenverbrauch_series,
                    kosten.get("preis_netzbezug"),
                    kosten.get("preis_netzbezug_fixed"),
                    "Preis Netzbezug",
                )
                if vermiedene_kosten is not None:
                    vermiedene_kosten = round(vermiedene_kosten, 2)

            # CO2-Bilanz: dieselbe Faktor×Energie-Logik wie Kosten, aber ein
            # einziger Faktor (g CO2/kWh) für den bezogenen Netzstrom — PV und
            # Speicher gelten als emissionsfrei, brauchen also keinen eigenen
            # Faktor. Ergebnis liegt in Gramm vor und wird erst fürs Anzeigen
            # (kpi.co2_ausstoss/co2_vermieden, bereits durch 1000 geteilt) in
            # kg umgerechnet — die Rohsumme in Gramm ist die genauere
            # Zwischengröße, falls hier später noch weitergerechnet wird.
            if co2.get("faktor_netzbezug_entity") or co2.get("faktor_netzbezug_fixed") is not None:
                co2_ausstoss_g = _bucket_factor(
                    netzbezug_series, co2.get("faktor_netzbezug_entity"), co2.get("faktor_netzbezug_fixed"), "CO2-Faktor Netzbezug"
                )
                co2_vermieden_g = _bucket_factor(
                    pv_eigenverbrauch_series, co2.get("faktor_netzbezug_entity"), co2.get("faktor_netzbezug_fixed"), "CO2-Faktor Netzbezug"
                )
                co2_ausstoss = round(co2_ausstoss_g / 1000.0, 2) if co2_ausstoss_g is not None else None
                co2_vermieden = round(co2_vermieden_g / 1000.0, 2) if co2_vermieden_g is not None else None

            # Kosten je Verbraucher (Verbraucheranteile-Tabelle) — wie bei
            # "Einsparung" mit dem Netzbezug-Preis bewertet: es gibt keine
            # Möglichkeit nachzuvollziehen, welche einzelnen kWh eines
            # Verbrauchers aus PV bzw. Netz stammen, daher wird die volle
            # Verbrauchsmenge zum Netzbezug-Preis bewertet — dieselbe
            # Konvention wie bei den meisten Smart-Plug-/Kostenrechnern.
            # Grundlast bekommt genauso eine Kosten-Spalte (eigene Bucket-
            # Serie, siehe grundlast_series_clamped oben).
            if kosten.get("preis_netzbezug") or kosten.get("preis_netzbezug_fixed") is not None:
                for item in verbraucher_breakdown:
                    series = verbraucher_series_by_name.get(item["name"])
                    if series is None:
                        continue
                    item_kosten = _bucket_factor(
                        series, kosten.get("preis_netzbezug"), kosten.get("preis_netzbezug_fixed"), "Preis Netzbezug"
                    )
                    item["kosten"] = round(item_kosten, 2) if item_kosten is not None else None
        net_cost = (
            round((netzbezug_cost or 0.0) - (einspeisung_revenue or 0.0), 2)
            if netzbezug_cost is not None or einspeisung_revenue is not None
            else None
        )

        # Metadaten-/Zählerrückgang-Prüfungen nur für die tatsächlich
        # angezeigte Periode — energiedashboard_data ruft compute_flow()
        # zusätzlich zweimal mit continuous=True nur für den rollierenden
        # Perioden-Vergleich auf, und compute_heatmap() ruft compute_flow()
        # bis zu 7× (einmal je Tag) nur für die kpi_series (deren quality-
        # Feld in beiden Fällen verworfen wird); den Rohwerte-Scan dafür
        # unnötig zu vervielfachen wäre reine Verschwendung.
        if continuous or skip_quality:
            entity_roles: list[tuple[str, str]] = []
            metadata = {"unit_issues": [], "type_issues": [], "duplicate_labels": []}
            reset_labels: list[str] = []
            resets_fully_checked = True
        else:
            entity_roles = self._config_entity_roles(config)
            metadata = self._check_entity_metadata(entity_roles)
            reset_labels, resets_fully_checked = self._check_counter_resets(
                entity_roles, window_start.timestamp(), window_end.timestamp(), now, read_cache
            )

        # Dynamische Bilanz-Beschreibung mit den tatsächlich zugeordneten
        # Rollen-Namen (Vorbild: Mockup-Text "Dach-PV + Balkon-PV + Netzbezug
        # entsprechen Verbrauch + Speicherladung + Einspeisung") statt einer
        # generischen Standardformulierung — macht die Prüfung nachvollziehbar
        # statt nur "ja/nein".
        balance_sources = [
            self._display_name(erz["entity_id"], erz.get("name", ""))
            for erz in (config.get("erzeuger") or []) if erz.get("entity_id")
        ]
        if speicher.get("entladen_entity_id"):
            balance_sources.append(f"{speicher_name} (Entladung)")
        if netzbezug_id:
            balance_sources.append("Netzbezug")
        balance_sinks = ["Verbrauch"]
        if speicher.get("laden_entity_id"):
            balance_sinks.append("Speicherladung")
        if einspeisung_id:
            balance_sinks.append("Einspeisung")
        balance_description = (
            f"{' + '.join(balance_sources)} entsprechen {' + '.join(balance_sinks)}, innerhalb der Toleranz. "
            "Nicht einzeln gemessene Lasten erscheinen als „Grundlast“."
            if balance_sources else
            "Verbraucher, Einspeisung und Speicherladung übersteigen nicht den Energiebus."
        )

        # Speicher: über einen längeren Zeitraum kann nicht mehr entladen als
        # geladen worden sein (Wirkungsgrad ≤ 100 %) — eine spürbare
        # Überschreitung deutet auf vertauschte Ladung/Entladung-Zuordnung hin.
        speicher_entladen_exceeds = (
            bool(speicher) and speicher_entladen_val > speicher_laden_val + 0.05
            and speicher.get("laden_entity_id") and speicher.get("entladen_entity_id")
        )

        # Immer alle Prüfungen zeigen (nicht nur bei Problemen) — ein Sankey,
        # der scheinbar exakt aufgeht, aber auf veralteten, falsch
        # vorzeichenbehafteten oder falsch zugeordneten Werten beruht, ist
        # schlimmer als gar keiner. Die Kachel soll aktiv zeigen, WAS geprüft
        # wurde, nicht nur schweigen, wenn nichts auffällt.
        quality_checks = [
            {
                "label": "Sensorwerte aktuell",
                "ok": not stale_labels,
                "detail": (
                    "Veraltet (>2 Tage ohne neue Werte): " + ", ".join(stale_labels)
                    if stale_labels
                    else "Alle zugeordneten Sensoren melden aktuelle Werte."
                ),
            },
            {
                "label": "Grundlast plausibel",
                "ok": not grundlast_negative,
                "detail": (
                    f"Grundlast wäre rechnerisch negativ ({round(grundlast, 2)} kWh) — "
                    "Zuordnung oder Vorzeichen der Rollen prüfen."
                    if grundlast_negative
                    else balance_description
                ),
            },
            {
                "label": "Einheit korrekt (kWh)",
                "ok": not metadata["unit_issues"],
                "detail": (
                    "Nicht in kWh: " + ", ".join(metadata["unit_issues"])
                    if metadata["unit_issues"]
                    else "Alle zugeordneten Rollen sind in kWh."
                ),
            },
            {
                "label": "Zähler-Typ korrekt",
                "ok": not metadata["type_issues"],
                "detail": (
                    "Kein Zähler (Summe ergibt hier keinen Sinn): " + ", ".join(metadata["type_issues"])
                    if metadata["type_issues"]
                    else "Alle zugeordneten Rollen sind Zähler (steigende Gesamtsumme)."
                ),
            },
            {
                "label": "Keine doppelt zugeordneten Entitäten",
                "ok": not metadata["duplicate_labels"],
                "detail": (
                    "Mehrfach zugeordnet: " + "; ".join(metadata["duplicate_labels"])
                    if metadata["duplicate_labels"]
                    else "Jede Entität ist nur einer Rolle zugeordnet."
                ),
            },
            {
                "label": "Keine Zählerrücksetzungen",
                "ok": not reset_labels,
                "detail": (
                    "Abnehmender Zählerstand erkannt (Reset/Tausch?): " + ", ".join(reset_labels)
                    if reset_labels
                    else (
                        "Keine abnehmenden Zählerstände im Zeitraum."
                        if resets_fully_checked
                        else "Keine abnehmenden Zählerstände in den geprüften Rollen "
                        "(bei mind. einer Rolle wegen der Datenmenge nicht vollständig geprüft)."
                    )
                ),
            },
        ]
        if speicher_entladen_exceeds:
            quality_checks.append({
                "label": "Speicher-Wirkungsgrad plausibel",
                "ok": False,
                "detail": (
                    f"{speicher_name}: Entladung ({round(speicher_entladen_val, 2)} kWh) übersteigt "
                    f"Ladung ({round(speicher_laden_val, 2)} kWh) — Ladung/Entladung vertauscht?"
                ),
            })
        quality_plausible = all(check["ok"] for check in quality_checks)

        return {
            "range": range_key,
            "range_label": RANGE_LABELS[range_key],
            "offset": offset,
            "unit": "kWh",
            "window_start_ts": window_start.timestamp(),
            "window_end_ts": window_end.timestamp(),
            "nodes": nodes,
            "links": links,
            "green_ratio": green_ratio,
            "speicher_efficiency_trend": speicher_efficiency_trend,
            "speicher_soc_trend": speicher_soc_trend,
            "autarkie_trend": autarkie_trend,
            "eigenverbrauch_trend": eigenverbrauch_trend,
            "kpi": {
                "erzeugung": erzeugung_total,
                "verbrauch": verbrauch_total,
                "netzbezug": netzbezug_total,
                # None statt 0.0, wenn gar keine Lade-/Entladung-Rolle
                # zugeordnet ist — sonst nicht von einem echten "0,0 kWh
                # diese Periode" zu unterscheiden (siehe x-show in
                # _energiedashboard_view.html, das darauf die Sichtbarkeit
                # der Speicher-Kachel steuert).
                "speicher_netto": (
                    round(speicher_laden_val - speicher_entladen_val, 3)
                    if speicher.get("laden_entity_id") or speicher.get("entladen_entity_id")
                    else None
                ),
                "speicher_soc": speicher_soc,
                "speicher_soc_kwh": speicher_soc_kwh,
                "speicher_soc_now": speicher_soc_now,
                "speicher_soc_now_kwh": speicher_soc_now_kwh,
                "speicher_efficiency": speicher_efficiency,
                "prognose_rest_heute": prognose_rest_heute,
                "prognose_morgen": prognose_morgen,
                "einspeisung": einspeisung_total,
                "autarkie": autarkie,
                "eigenverbrauch": eigenverbrauch,
                "netzbezug_cost": netzbezug_cost,
                "einspeisung_revenue": einspeisung_revenue,
                "vermiedene_kosten": vermiedene_kosten,
                "net_cost": net_cost,
                "co2_ausstoss": co2_ausstoss,
                "co2_vermieden": co2_vermieden,
            },
            "kpi_series": {
                "erzeugung": self._sparkline(erzeugung_series),
                "verbrauch": self._sparkline(verbrauch_series),
                "netzbezug": self._sparkline(netzbezug_series),
                "speicher_netto": self._sparkline(speicher_netto_series),
                "einspeisung": self._sparkline(einspeisung_series),
            },
            "verbraucher_breakdown": verbraucher_breakdown,
            "erzeuger_breakdown": erzeuger_breakdown,
            "anomalien": anomalien,
            "quality": {"plausible": quality_plausible, "checks": quality_checks},
        }

    def compute_heatmap(
        self, config: dict, days: int = HEATMAP_DAYS, read_cache: query_mod.QueryReadCache | None = None,
    ) -> dict:
        """Tageslastprofil: Verbrauch je Stunde für die letzten `days` Tage.
        Kein zweiter Datenpfad — nutzt dieselbe (kumulierte) "Verbrauch"-
        Sparkline wie die KPI-Kachel je Tag und rechnet sie durch
        Rückwärts-Differenzieren wieder in einzelne Stunden-Deltas um, statt
        die Rollen-Summen ein zweites Mal separat zu holen. Der heutige Tag
        (offset=0) liefert dabei naturgemäß nur Stunden bis "jetzt" — auf 24
        Einträge mit None für die noch bevorstehenden Stunden aufgefüllt,
        damit jede Zeile gleich viele Zellen hat (Frontend blendet None-
        Zellen nur aus, statt die Zeile zu verkürzen).

        read_cache optional durchgereicht — die HEATMAP_DAYS-vielen
        compute_flow()-Aufrufe hier fragen dieselben Rollen-Entitäten für oft
        benachbarte (häufig sogar in derselben Monatsdatei liegende) Tage ab;
        ein gemeinsamer Cache erspart das mehrfache Neueinlesen."""
        if read_cache is None:
            read_cache = query_mod.QueryReadCache()
        rows: list[dict] = []
        max_value = 0.0
        for offset in range(0, -days, -1):
            flow = self.compute_flow(config, "day", offset, skip_quality=True, read_cache=read_cache)
            cumulative = flow["kpi_series"]["verbrauch"]
            hourly: list[float | None] = []
            previous = 0.0
            for value in cumulative:
                delta = round(value - previous, 3)
                hourly.append(delta)
                previous = value
                max_value = max(max_value, delta)
            while len(hourly) < 24:
                hourly.append(None)
            day_local = datetime.fromtimestamp(flow["window_start_ts"], self.deps.tz)
            rows.append({"label": _WEEKDAY_LABELS[day_local.weekday()], "hours": hourly})
        rows.reverse()  # älteste Tag zuerst
        return {"rows": rows, "max_value": round(max_value, 3)}

    def compute_heatmap_weekday(
        self, config: dict, range_key: str, offset: int = 0,
        read_cache: query_mod.QueryReadCache | None = None,
    ) -> dict:
        """Tageslastprofil bei Monat/Jahr: Verbrauch je Wochentag/Stunde, über
        alle im Zeitraum liegenden Kalendertage gemittelt (Mo-So statt sieben
        konkreter Kalendertage wie bei compute_heatmap()) — Grundlage für den
        Wochentags-Modus des Tageslastprofils.

        Baut dieselbe Verbrauch-Formel wie compute_flow() nach (Verbraucher-
        Summe + geklemmte Grundlast), aber aus Stunden-Buckets über den
        GESAMTEN Zeitraum (query_hourly_counter_series(), gespeist aus der
        zusätzlichen stunde.parquet-Stufe, siehe entities.hourly_rollup) statt
        über HEATMAP_DAYS einzelne compute_flow()-Aufrufe je Kalendertag wie
        compute_heatmap() — nur so lässt sich nach Wochentag UND Stunde
        gruppieren, nicht nur nach Kalendertag, und bleibt bei einer
        Jahresansicht trotzdem mit wenigen Parquet-Lesevorgängen günstig statt
        365 Einzel-Tagesabfragen."""
        if range_key not in ("month", "year"):
            raise HTTPException(status_code=400, detail="Nur 'month' oder 'year' unterstützt")
        if read_cache is None:
            read_cache = query_mod.QueryReadCache()
        now = datetime.now(self.deps.tz)
        window_start, window_end, _period_end = query_mod._window(range_key, now, offset)  # noqa: SLF001 — siehe Modul-Docstring

        def hourly(entity_id: str | None) -> dict[float, float]:
            if not entity_id:
                return {}
            entity = self.deps.index.get_entity(entity_id)
            if entity is None or entity["aggregation_type"] != "counter":
                # Fehlkonfigurierte Nicht-Zähler-Rolle — wird bereits an
                # anderer Stelle (_check_entity_metadata) als Einrichtungsfehler
                # gemeldet, hier einfach ignorieren statt falsche Zähler-
                # Delta-Logik auf Rohwerte anzuwenden.
                return {}
            return query_mod.query_hourly_counter_series(
                self.deps.data_dir, self.deps.index, entity_id, window_start, window_end,
                self.deps.tz, now, read_cache,
            )

        speicher = config.get("speicher") or {}
        netzbezug_series = hourly(config.get("netzbezug"))
        erzeuger_series_list = [
            hourly(erz.get("entity_id")) for erz in (config.get("erzeuger") or []) if erz.get("entity_id")
        ]
        speicher_entladen_series = hourly(speicher.get("entladen_entity_id"))
        speicher_laden_series = hourly(speicher.get("laden_entity_id"))
        verbraucher_series_list = [
            hourly(verbraucher.get("entity_id"))
            for verbraucher in (config.get("verbraucher") or [])
            if verbraucher.get("entity_id")
        ]
        einspeisung_series = hourly(config.get("einspeisung"))

        erzeugung_series = self._series_merge(*erzeuger_series_list) if erzeuger_series_list else {}
        verbraucher_total_series = self._series_merge(*verbraucher_series_list) if verbraucher_series_list else {}
        bus_in_series = self._series_merge(netzbezug_series, erzeugung_series, speicher_entladen_series)
        grundlast_series = self._series_merge(
            bus_in_series, verbraucher_total_series, einspeisung_series, speicher_laden_series,
            factors=[1.0, -1.0, -1.0, -1.0],
        )
        grundlast_clamped = {ts: max(v, 0.0) for ts, v in grundlast_series.items()}
        verbrauch_series = self._series_merge(verbraucher_total_series, grundlast_clamped)

        buckets: dict[tuple[int, int], list[float]] = {}
        for ts, value in verbrauch_series.items():
            local = datetime.fromtimestamp(ts, self.deps.tz)
            buckets.setdefault((local.weekday(), local.hour), []).append(value)

        max_value = 0.0
        rows: list[dict] = []
        for weekday in range(7):
            hours: list[float | None] = []
            for hour in range(24):
                values = buckets.get((weekday, hour))
                if not values:
                    hours.append(None)
                    continue
                avg = round(sum(values) / len(values), 3)
                hours.append(avg)
                max_value = max(max_value, avg)
            rows.append({"label": _WEEKDAY_LABELS[weekday], "hours": hours})

        return {"rows": rows, "max_value": round(max_value, 3)}

    def _page_context(self, request: Request) -> dict:
        config = _load_config(self.deps.index)
        return {
            "configured": _is_configured(config),
            "enabled": _is_enabled(self.deps.index),
            "config": config,
            **self.deps.app_root_context(request),
        }

    def router(self) -> APIRouter:
        router = APIRouter()
        deps = self.deps

        @router.get("/energiedashboard", response_class=HTMLResponse)
        def energiedashboard_page(request: Request) -> HTMLResponse:
            return deps.templates.TemplateResponse(
                request, "energiedashboard.html", self._page_context(request)
            )

        @router.post("/energiedashboard/enable")
        def energiedashboard_enable() -> dict:
            deps.index.set_setting(SETTING_ENABLED, "1")
            return {"enabled": True}

        @router.post("/energiedashboard/disable")
        def energiedashboard_disable() -> dict:
            # Konfiguration bleibt erhalten — Deaktivieren blendet die Funktion
            # nur aus, erneutes Aktivieren zeigt sofort wieder denselben Stand.
            deps.index.set_setting(SETTING_ENABLED, "0")
            return {"enabled": False}

        @router.get("/energiedashboard/setup", response_class=HTMLResponse)
        def energiedashboard_setup_form(request: Request) -> HTMLResponse:
            config = _load_config(deps.index)
            return deps.templates.TemplateResponse(
                request, "_energiedashboard_setup.html",
                {
                    "config": config, "entity_options": self._entity_options(),
                    "anomalie_schwelle_options": list(ANOMALIE_SCHWELLE_LABELS.items()),
                    **deps.app_root_context(request),
                },
            )

        @router.post("/energiedashboard/setup", response_class=HTMLResponse)
        def energiedashboard_setup_save(
            request: Request,
            hub_name: str = Form(""),
            netzbezug: str = Form(""),
            einspeisung: str = Form(""),
            erzeuger_entity_id: list[str] = Form([]),
            erzeuger_name: list[str] = Form([]),
            speicher_name: str = Form(""),
            speicher_laden_entity_id: str = Form(""),
            speicher_entladen_entity_id: str = Form(""),
            speicher_soc_entity_id: str = Form(""),
            speicher_capacity_kwh: str = Form(""),
            verbraucher_entity_id: list[str] = Form([]),
            verbraucher_name: list[str] = Form([]),
            verbraucher_gruppe: list[str] = Form([]),
            verbraucher_gruppen: list[str] = Form([]),
            preis_netzbezug: str = Form(""),
            preis_einspeisung: str = Form(""),
            preis_netzbezug_fixed: str = Form(""),
            preis_einspeisung_fixed: str = Form(""),
            co2_faktor_netzbezug_entity: str = Form(""),
            co2_faktor_netzbezug_fixed: str = Form(""),
            prognose_rest_heute_entity_id: str = Form(""),
            prognose_morgen_entity_id: str = Form(""),
            show_autarkie: str = Form(""),
            show_verbraucheranteile: str = Form(""),
            show_kostenanalyse: str = Form(""),
            show_co2: str = Form(""),
            show_tageslastprofil: str = Form(""),
            show_bilanz_datenqualitaet: str = Form(""),
            anomalie_schwelle: str = Form("50"),
        ) -> HTMLResponse:
            if not netzbezug.strip():
                raise HTTPException(status_code=422, detail="Netzbezug ist Pflicht")
            if anomalie_schwelle not in ANOMALIE_SCHWELLE_LABELS:
                raise HTTPException(status_code=422, detail="Ungültige Auffälligkeiten-Schwelle")

            def pairs(entity_ids: list[str], names: list[str], gruppen: list[str] | None = None) -> list[dict]:
                # Reihen ohne gewählte Entität (z. B. eine per "+ hinzufügen"
                # angelegte, dann leer gelassene Zeile) werden stillschweigend
                # übersprungen statt einer leeren Rolle gespeichert zu werden.
                # gruppen (nur für Verbraucher) ist ein optionaler, parallel
                # zu entity_ids/names übermittelter dritter Formular-Array —
                # ohne ihn (Erzeuger-Aufruf) bekommt kein Eintrag ein
                # "gruppe"-Feld.
                rows = []
                for idx, eid in enumerate(entity_ids):
                    if not eid.strip():
                        continue
                    row = {
                        "entity_id": eid.strip(),
                        "name": (names[idx] if idx < len(names) else "").strip(),
                    }
                    if gruppen is not None:
                        row["gruppe"] = (gruppen[idx] if idx < len(gruppen) else "").strip() or None
                    rows.append(row)
                return rows

            speicher = None
            if speicher_laden_entity_id.strip() or speicher_entladen_entity_id.strip() or speicher_soc_entity_id.strip():
                speicher = {
                    "name": speicher_name.strip(),
                    "laden_entity_id": speicher_laden_entity_id.strip(),
                    "entladen_entity_id": speicher_entladen_entity_id.strip(),
                    "soc_entity_id": speicher_soc_entity_id.strip(),
                    "capacity_kwh": _parse_float(speicher_capacity_kwh),
                }

            def _cent_to_euro(text: str) -> float | None:
                # Formular nimmt den festen Preis bewusst in Cent/kWh entgegen
                # (z. B. "29" statt "0,29") — gespeichert wird er trotzdem in
                # Euro/kWh, damit die Config durchgängig dieselbe Einheit wie
                # die Preis-Entitäten verwendet (siehe _bucket_cost).
                cent = _parse_float(text)
                return round(cent / 100.0, 6) if cent is not None else None

            kosten = None
            preis_netzbezug_fixed_val = _cent_to_euro(preis_netzbezug_fixed)
            preis_einspeisung_fixed_val = _cent_to_euro(preis_einspeisung_fixed)
            if (
                preis_netzbezug.strip() or preis_einspeisung.strip()
                or preis_netzbezug_fixed_val is not None
                or preis_einspeisung_fixed_val is not None
            ):
                kosten = {
                    "preis_netzbezug": preis_netzbezug.strip() or None,
                    "preis_einspeisung": preis_einspeisung.strip() or None,
                    "preis_netzbezug_fixed": preis_netzbezug_fixed_val,
                    "preis_einspeisung_fixed": preis_einspeisung_fixed_val,
                }

            # CO2-Faktor wird — anders als der Preis — direkt in g/kWh
            # eingegeben und auch so gespeichert; es gibt hier keine
            # Cent/Euro-artige Doppeleinheit, die eine Umrechnung bräuchte.
            co2 = None
            co2_faktor_netzbezug_fixed_val = _parse_float(co2_faktor_netzbezug_fixed)
            if co2_faktor_netzbezug_entity.strip() or co2_faktor_netzbezug_fixed_val is not None:
                co2 = {
                    "faktor_netzbezug_entity": co2_faktor_netzbezug_entity.strip() or None,
                    "faktor_netzbezug_fixed": co2_faktor_netzbezug_fixed_val,
                }

            # Kein fester Fallback-Wert wie bei Kosten/CO2 — eine Prognose
            # lässt sich nicht sinnvoll als fixe Zahl eintragen, nur Entität.
            prognose = None
            if prognose_rest_heute_entity_id.strip() or prognose_morgen_entity_id.strip():
                prognose = {
                    "rest_heute_entity_id": prognose_rest_heute_entity_id.strip() or None,
                    "morgen_entity_id": prognose_morgen_entity_id.strip() or None,
                }

            config = {
                "hub_name": hub_name.strip() or None,
                "netzbezug": netzbezug.strip(),
                "einspeisung": einspeisung.strip() or None,
                "erzeuger": pairs(erzeuger_entity_id, erzeuger_name),
                "speicher": speicher,
                "verbraucher": pairs(verbraucher_entity_id, verbraucher_name, verbraucher_gruppe),
                # dict.fromkeys() statt set() — entfernt Duplikate (z. B. wenn
                # eine Gruppe angelegt, aber nie einem Verbraucher zugewiesen
                # wurde und trotzdem zusätzlich noch im Verwalten-Popup
                # erscheint), behält aber die Reihenfolge aus dem Formular bei.
                "verbraucher_gruppen": list(dict.fromkeys(g.strip() for g in verbraucher_gruppen if g.strip())),
                "kosten": kosten,
                "co2": co2,
                "prognose": prognose,
                "show_autarkie": show_autarkie == "on",
                "show_verbraucheranteile": show_verbraucheranteile == "on",
                "show_kostenanalyse": show_kostenanalyse == "on",
                "show_co2": show_co2 == "on",
                "show_tageslastprofil": show_tageslastprofil == "on",
                "show_bilanz_datenqualitaet": show_bilanz_datenqualitaet == "on",
                "anomalie_schwelle": anomalie_schwelle,
            }
            _save_config(deps.index, config)
            sync_hourly_rollup_flags(deps.index, [eid for eid, _ in self._config_entity_roles(config)])
            deps.index.invalidate_heatmap_weekday_snapshots()
            return deps.templates.TemplateResponse(
                request, "_energiedashboard_view.html",
                {"configured": _is_configured(config), "config": config},
            )

        @router.get("/energiedashboard/data")
        def energiedashboard_data(range: str = "day", offset: int = 0) -> dict:  # noqa: A002
            config = _load_config(deps.index)
            if not _is_configured(config):
                raise HTTPException(status_code=409, detail="Noch nicht eingerichtet")
            # Ein Cache für alle drei compute_flow()-Aufrufe dieses Requests
            # (aktuell/rollierend-aktuell/rollierend-vorherig) — deren
            # Fenster überlappen oder grenzen direkt aneinander an, ohne
            # geteilten Cache läse jeder Aufruf dieselben Hot-Buffer-Dateien
            # unabhängig neu ein.
            read_cache = query_mod.QueryReadCache()
            current = self.compute_flow(config, range, offset, read_cache=read_cache)
            # Perioden-Vergleich immer rollierend statt kalendarisch: zwei
            # rollierende Fenster fester Länge (continuous=True), direkt
            # aneinander anschließend — unabhängig davon, ob die betrachtete
            # Periode selbst kalendarisch/noch laufend oder abgeschlossen
            # ist. Löst zugleich das Problem, dass ein kalendarischer
            # Vergleich (z. B. "Tag" am Monatsersten gegen exakt denselben
            # Zeitabschnitt des Vortags) auf dünn archivierte Zeiträume
            # treffen und leer bleiben kann — ein rollierendes Fenster endet
            # dagegen immer direkt an der Grenze zum jeweils anderen Fenster.
            rolling_current = self.compute_flow(config, range, offset, continuous=True, read_cache=read_cache)
            rolling_previous = self.compute_flow(config, range, offset - 1, continuous=True, read_cache=read_cache)
            current["kpi_compare"] = self._compare_kpi(rolling_current["kpi"], rolling_previous["kpi"])
            current["compare_label"] = COMPARE_LABELS[range]
            return current

        @router.get("/energiedashboard/heatmap")
        def energiedashboard_heatmap(range: str = "week", offset: int = 0) -> dict:  # noqa: A002
            config = _load_config(deps.index)
            if not _is_configured(config):
                raise HTTPException(status_code=409, detail="Noch nicht eingerichtet")
            # Tag/Woche: unverändert sieben konkrete Kalendertage (Kachel ist
            # hier bewusst vom Zeitraum-Umschalter entkoppelt). Monat/Jahr:
            # Wochentags-Mittel über den gesamten gewählten Zeitraum, siehe
            # compute_heatmap_weekday(). offset=0 kommt dabei nach Möglichkeit
            # aus dem täglich im Hintergrund berechneten Cache (siehe
            # refresh_heatmap_weekday_cache_if_stale()) statt bei jedem
            # Seitenaufruf neu über alle Rollen-Entitäten zu rechnen.
            if range in ("month", "year"):
                if offset == 0:
                    cached = deps.index.get_heatmap_weekday_snapshot(range)
                    if cached is not None:
                        return cached["grid"]
                return self.compute_heatmap_weekday(config, range, offset)
            return self.compute_heatmap(config)

        return router
