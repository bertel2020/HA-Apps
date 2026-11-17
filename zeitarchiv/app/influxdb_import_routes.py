"""InfluxDB-Import (1.x): Reiter, Verfügbarkeitsprüfung, Dry Run und Import.

Ausgelagert aus import_routes.py (dort wäre das Pendant zu den
VictoriaMetrics-Routen nochmals rund 400 Zeilen gewachsen). Der Dienst nutzt
den ImportService nur lesend — Abhängigkeiten (Index, Koordinator, Vorlagen)
und die Hilfen für bekannte Entitäten und die Verfügbarkeitsanzeige kommen von
dort; Zustand (Fortschritt, Verfügbarkeits-Cache) liegt hier.

Anders als beim VictoriaMetrics-Import werden die Entitäten nacheinander
abgerufen, geschrieben und wieder freigegeben, statt zuerst ALLE im
Arbeitsspeicher zu sammeln: Bei einer langen Historie (gemeldet: rund 400 Mio
Zeilen für etwa 200 Entitäten) wäre das nicht tragbar. Der Abruf läuft vor
jeder Sperre, die Entitätssperre hält nur das Schreiben eines einzelnen
Abschnitts.

Große Entitäten werden in Abschnitte geteilt (influxdb_import.plan_sections):
Eine Entität mit mehr als 10 Mio Punkten wurde früher abgewiesen und musste von
Hand zeitraumweise importiert werden. Jetzt wird je Abschnitt abgerufen,
geschrieben und freigegeben; der Import ist zwischen den Abschnitten
abbrechbar und lässt sich wiederholen (vorhandene Monate werden übersprungen).

Das Passwort verlässt die Anfrage nie: Es steht weder im Verfügbarkeits-Cache
noch im Importbericht noch in einem Log, und die Seite schreibt es nicht
zurück in das Formular."""

from __future__ import annotations

import dataclasses
import logging
import threading
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse

from .formatting import (
    entity_display_name,
    format_int,
    format_time,
    format_timestamp,
    format_type,
)
from .i18n import N_, tr
from .progress import JobBusy, JobProgress
from .storage import ha_import, import_last, import_reports, influxdb_import, symcon_import
from .storage.index import STATE_TYPE

logger = logging.getLogger(__name__)

# Wie beim VictoriaMetrics-Reiter: keine Quellen-/Auflösungswahl, InfluxDB hält
# jeden Zustandswechsel vor. "max" ist nur durch die Zahl der Abfragefenster
# begrenzt (influxdb_import.HISTORY_CHUNK).
INFLUX_RANGE_PRESETS = {
    "max": N_("Gesamte Historie (max.)"),
    "365d": N_("Letztes Jahr"),
    "90d": N_("Letzte 90 Tage"),
    "30d": N_("Letzte 30 Tage"),
    "custom": N_("Eigener Zeitraum …"),
}
INFLUX_RANGE_PRESET_DAYS = {"30d": 30, "90d": 90, "365d": 365}
INFLUX_MAX_RANGE_DAYS = 3650
# Ab wann die angezeigte Verfügbarkeit als veraltet gilt (wie beim HA-Import).
AVAILABILITY_STALE_SECONDS = 15 * 60


def _merge(parts: list):  # noqa: ANN202 — ImportPlan/ImportResult
    """Fasst die Ergebnisse mehrerer Abschnitte einer Entität zusammen: Zahlen
    werden addiert, Listen verkettet (ohne Doppelte, in Reihenfolge), bei Texten
    und sonstigen Werten gilt der erste nicht leere."""
    merged = dataclasses.replace(parts[0])
    for part in parts[1:]:
        for field in dataclasses.fields(merged):
            current, other = getattr(merged, field.name), getattr(part, field.name)
            if isinstance(current, bool):
                continue
            if isinstance(current, int):
                setattr(merged, field.name, current + other)
            elif isinstance(current, list):
                setattr(merged, field.name, current + [item for item in other if item not in current])
            elif not current and other:
                setattr(merged, field.name, other)
    return merged


@dataclasses.dataclass
class _Span:
    """Erster und letzter Zeitstempel und Zahl der gelesenen Werte einer
    Entität über alle Abschnitte (die Zeilen selbst werden freigegeben)."""

    first_ts: float | None = None
    last_ts: float | None = None
    count: int = 0

    def add(self, history: ha_import.HistoryFetchResult) -> None:
        if not history.rows:
            return
        self.first_ts = history.rows[0][0] if self.first_ts is None else min(self.first_ts, history.rows[0][0])
        self.last_ts = history.rows[-1][0] if self.last_ts is None else max(self.last_ts, history.rows[-1][0])
        self.count += len(history.rows)


@dataclasses.dataclass(frozen=True)
class _Config:
    """Eingaben des Formulars. ``password`` bleibt auf diese Anfrage (bzw. die
    Dauer des Hintergrundauftrags) beschränkt; ``public()`` ist die Fassung für
    Bericht und Cache-Schlüssel."""

    entity_ids: list[str]
    range_preset: str
    date_from: str
    date_to: str
    include_existing_months: bool
    base_url: str
    database: str
    username: str
    password: str
    verify_tls: bool

    @property
    def auth(self) -> tuple[str, str] | None:
        return (self.username, self.password) if (self.username or self.password) else None

    def connection_key(self) -> tuple:
        custom = self.range_preset == "custom"
        return (
            self.base_url, self.database, self.username, self.verify_tls,
            self.range_preset, self.date_from if custom else "", self.date_to if custom else "",
        )

    def public(self) -> dict:
        return {
            "entity_ids": self.entity_ids,
            "base_url": self.base_url,
            "database": self.database,
            "username": self.username,
            "verify_tls": self.verify_tls,
            "range_preset": self.range_preset,
            "date_from": self.date_from,
            "date_to": self.date_to,
            "include_existing_months": self.include_existing_months,
        }


class InfluxImportService:
    def __init__(self, owner) -> None:  # noqa: ANN001 — ImportService; Importzyklus vermeiden
        self.owner = owner
        self.deps = owner.deps
        self._progress = JobProgress("influxdb-import", unit=N_("Entitäten"), label=N_("InfluxDB-Import"))
        # Abbruch durch den Nutzer: wird vor jedem Abschnitt und im Abruf geprüft,
        # nie während eines Schreibvorgangs.
        self._cancel = threading.Event()
        # Genau ein Eintrag (zuletzt geprüfte Adresse/Datenbank/Zeitraum), wie
        # beim VictoriaMetrics-Reiter.
        self._availability_lock = threading.Lock()
        self._availability: dict | None = None

    # ------------------------------------------------------------------ Formular

    def _config(self, form) -> _Config:
        range_preset = str(form.get("range_preset") or "max")
        if range_preset not in INFLUX_RANGE_PRESETS:
            range_preset = "max"
        return _Config(
            entity_ids=[str(v).strip() for v in form.getlist("entity_ids") if str(v).strip()],
            range_preset=range_preset,
            date_from=str(form.get("date_from") or ""),
            date_to=str(form.get("date_to") or ""),
            include_existing_months=form.get("include_existing_months") == "on",
            base_url=str(form.get("base_url") or "").strip() or influxdb_import.DEFAULT_BASE_URL,
            database=str(form.get("database") or "").strip() or influxdb_import.DEFAULT_DATABASE,
            username=str(form.get("username") or "").strip(),
            password=str(form.get("password") or ""),
            verify_tls=form.get("verify_tls") == "on",
        )

    @staticmethod
    def _date_range(range_preset: str, date_from: str, date_to: str) -> tuple[datetime, datetime]:
        """UTC-Fenster; date_to inklusiv gemeint (deshalb +1 Tag), nie über
        "jetzt" hinaus."""
        now = datetime.now(timezone.utc)
        if range_preset == "max":
            return now - timedelta(days=INFLUX_MAX_RANGE_DAYS), now
        if range_preset in INFLUX_RANGE_PRESET_DAYS:
            return now - timedelta(days=INFLUX_RANGE_PRESET_DAYS[range_preset]), now
        try:
            start = datetime.strptime(date_from, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            start = now - timedelta(days=30)
        try:
            end = datetime.strptime(date_to, "%Y-%m-%d").replace(tzinfo=timezone.utc) + timedelta(days=1)
        except ValueError:
            end = now
        end = min(end, now)
        if end <= start:
            end = start + timedelta(days=1)
        return start, end

    # ------------------------------------------------------------------ Kontext

    def context(
        self,
        selected_ids: set[str] | None = None,
        config: _Config | None = None,
    ) -> dict:
        """Auswahlliste für den InfluxDB-Reiter — wie beim HA- und
        VictoriaMetrics-Import aus index.list_entities() (nur in Zeitarchiv
        bekannte Entitäten). Die Verfügbarkeitsspalte bleibt bis zum
        expliziten Klick leer (jede Prüfung ist eine Netzwerkabfrage). Bewusst
        KEINE Erreichbarkeitsprüfung beim Seitenaufbau: GET /import würde
        sonst bei jedem Aufruf auf eine womöglich nicht erreichbare Adresse
        warten."""
        now = datetime.now(timezone.utc)
        config = config or self._config(_EmptyForm())
        with self._availability_lock:
            entry = self._availability
        if entry is not None and entry["key"] != config.connection_key():
            entry = None
        availability: dict[str, ha_import.EntityAvailability] = entry["availability"] if entry else {}
        checked_at = entry["checked_at"] if entry else None
        entities = []
        entity_rows = self.deps.index.list_entities()
        areas, categories, category_of = self.owner._area_category_lookup(entity_rows)
        last_imports = import_last.load(self.deps.data_dir)
        for row in entity_rows:
            entity_id = row["entity_id"]
            avail = availability.get(entity_id)
            available_range = available_count = available_label = None
            if avail is not None:
                if avail.has_data:
                    available_range, available_count = self.owner._ha_availability_range_and_count(avail)
                else:
                    available_label = tr("Keine Daten im gewählten Zeitraum")
            entities.append({
                "entity_id": entity_id,
                "friendly_name": entity_display_name(entity_id, row["friendly_name"], row["custom_name"]),
                "unit": row["unit"],
                "aggregation_type": row["aggregation_type"],
                "type_label": format_type(row["aggregation_type"]),
                "has_data": avail.has_data if avail is not None else None,
                "available_label": available_label,
                "available_range": available_range,
                "available_count": available_count,
                "available_first_ts": avail.first_ts if avail is not None else None,
                "area": (areas.get(entity_id) or {}).get("area"),
                "category": categories.get(category_of.get(entity_id)),
                **self.owner._last_import_fields(last_imports.get(entity_id)),
            })
        return {
            "influx_has_areas": bool(areas),
            "influx_has_categories": bool(categories),
            "influx_base_url": config.base_url,
            "influx_default_base_url": influxdb_import.DEFAULT_BASE_URL,
            "influx_database": config.database,
            "influx_default_database": influxdb_import.DEFAULT_DATABASE,
            "influx_username": config.username,
            "influx_verify_tls": config.verify_tls,
            "influx_entities": entities,
            "influx_selected_ids": selected_ids or set(),
            "influx_availability_checked": entry is not None,
            "influx_availability_error": entry["error"] if entry else None,
            "influx_availability_checked_at_label": (
                f"{format_timestamp(checked_at, self.deps.tz)} {format_time(checked_at, self.deps.tz)}"
                if checked_at is not None else None
            ),
            "influx_availability_stale": (
                checked_at is not None and (now.timestamp() - checked_at) > AVAILABILITY_STALE_SECONDS
            ),
            "influx_range_options": list(INFLUX_RANGE_PRESETS.items()),
            "influx_range_preset": config.range_preset,
            "influx_date_from": config.date_from or (now - timedelta(days=30)).strftime("%Y-%m-%d"),
            "influx_date_to": config.date_to or now.strftime("%Y-%m-%d"),
            "influx_include_existing_months": config.include_existing_months,
        }

    def _progress_context(self) -> dict:
        return {
            **self._progress.snapshot(), "progress_id": "influx-progress", "poll_url": "import/influxdb/progress",
            "cancel_url": "import/influxdb/cancel",
        }

    # ------------------------------------------------------------------ Abruf

    def _state_entity_ids(self, entity_ids: list[str]) -> set[str]:
        result = set()
        for entity_id in entity_ids:
            entity = self.deps.index.get_entity(entity_id)
            if entity is not None and entity["aggregation_type"] == STATE_TYPE:
                result.add(entity_id)
        return result

    def _fetch_history(self, entity_id: str, config: _Config, start: datetime, end: datetime, dry: bool):
        mapper = (
            ha_import.dry_state_mapper_for(self.deps.index, entity_id) if dry
            else ha_import.state_mapper_for(self.deps.index, entity_id)
        )
        return influxdb_import.fetch_history_rows(
            entity_id, start, end, config.base_url, config.database, config.auth, config.verify_tls,
            state_mapper=mapper, should_cancel=None if dry else self._cancel.is_set,
        )

    def _sections(
        self, entity_id: str, config: _Config, start: datetime, end: datetime,
    ) -> list[tuple[datetime, datetime]]:
        """Abschnitte der Entität: ein Abschnitt bis zur Zielgröße, sonst ganze
        lokale Monate (siehe influxdb_import.plan_sections)."""
        months = influxdb_import.fetch_monthly_counts(
            entity_id, start, end, self.deps.tz, config.base_url, config.database, config.auth,
            config.verify_tls, state=bool(self._state_entity_ids([entity_id])),
        )
        return influxdb_import.plan_sections(months, start, end, self.deps.tz)

    def _section_label(self, section: tuple[datetime, datetime]) -> str:
        """„2025-01 – 2025-03“ (lokale Monate, Ende einschließlich)."""
        first = section[0].astimezone(self.deps.tz)
        last = (section[1] - timedelta(seconds=1)).astimezone(self.deps.tz)
        return f"{first:%Y-%m}" if (first.year, first.month) == (last.year, last.month) else f"{first:%Y-%m} – {last:%Y-%m}"

    def _fetch_availability(
        self, entity_ids: list[str], config: _Config, start: datetime, end: datetime,
    ) -> tuple[dict[str, ha_import.EntityAvailability], str | None]:
        try:
            return influxdb_import.fetch_availability(
                entity_ids, start, end, config.base_url, config.database, config.auth, config.verify_tls,
                state_entity_ids=self._state_entity_ids(entity_ids),
            ), None
        except (influxdb_import.InfluxApiError, ValueError) as exc:
            logger.warning(
                "InfluxDB-Verfügbarkeitsprüfung fehlgeschlagen · Entitäten=%d · %s", len(entity_ids), exc
            )
            return {}, str(exc)

    def _available_label(self, span: _Span) -> str:
        if span.first_ts is None or span.last_ts is None:
            return tr("Keine Werte im gewählten Zeitraum gefunden")
        return (
            f"{format_timestamp(span.first_ts, self.deps.tz)} – {format_timestamp(span.last_ts, self.deps.tz)} · "
            f"{format_int(span.count)} {tr('Werte')}"
        )

    def _item_head(self, entity_id: str, span: _Span) -> dict:
        entity = self.deps.index.get_entity(entity_id)
        return {
            "entity_id": entity_id,
            "friendly_name": entity_display_name(
                entity_id,
                entity["friendly_name"] if entity else None,
                entity["custom_name"] if entity else None,
            ),
            "available_label": self._available_label(span),
        }

    # ------------------------------------------------------------------ Routen

    def register(self, router: APIRouter) -> None:
        deps = self.deps

        @router.post("/import/influxdb/connection", response_class=HTMLResponse)
        async def import_influxdb_connection(request: Request) -> HTMLResponse:
            """Verbindungstest: Adresse, Zugangsdaten und Datenbank, ohne etwas
            zu lesen oder zu schreiben. Liefert nur die Ergebniszeile, die
            Sektion wird nicht neu gezeichnet (Markierungen und Passwort bleiben)."""
            config = self._config(await request.form())
            try:
                version, domains = await run_in_threadpool(
                    influxdb_import.check_connection, config.base_url, config.database, config.auth, config.verify_tls
                )
            except influxdb_import.InfluxApiError as exc:
                logger.warning("InfluxDB-Verbindungstest fehlgeschlagen · %s", exc)
                context = {"ok": False, "message": str(exc)}
            else:
                parts = [tr("Verbindung hergestellt")]
                if version:
                    parts.append(tr("InfluxDB {version}", version=version))
                parts.append(tr("Datenbank „{database}“ enthält Daten aus {count} Domains", database=config.database, count=len(domains)))
                logger.info("InfluxDB-Verbindungstest erfolgreich · Version=%s · Domains=%d", version or "?", len(domains))
                context = {"ok": True, "message": " · ".join(parts)}
            return deps.templates.TemplateResponse(request, "_connection_check_result.html", context)

        @router.post("/import/influxdb/availability", response_class=HTMLResponse)
        async def import_influxdb_availability(request: Request) -> HTMLResponse:
            """Verfügbarkeits-Vorschau der markierten Entitäten — expliziter
            Klick, jede Prüfung ist eine Netzwerkabfrage. Rendert die ganze
            Sektion neu und reicht die angehakten Entitäten durch, damit die
            Auswahl erhalten bleibt."""
            form = await request.form()
            config = self._config(form)
            # Ohne Markierung prüft der Knopf alle angezeigten Entitäten (check_ids),
            # genau wie beim Home-Assistant-Import — und markiert dabei nichts.
            check_ids = [str(value) for value in form.getlist("check_ids")] if not config.entity_ids else []
            known_ids, unknown_ids = self.owner._known_ha_entity_ids(config.entity_ids or check_ids)
            if unknown_ids:
                logger.warning("InfluxDB-Verfügbarkeitsprüfung: unbekannte Entitäts-IDs verworfen · %s", unknown_ids)
            if known_ids:
                start, end = self._date_range(config.range_preset, config.date_from, config.date_to)
                availability, availability_error = await run_in_threadpool(
                    self._fetch_availability, known_ids, config, start, end
                )
                # Auch ein Fehlschlag wird festgehalten, sonst fiele die Anzeige
                # nach dem Neuzeichnen kommentarlos auf "noch nie geprüft" zurück.
                with self._availability_lock:
                    self._availability = {
                        "key": config.connection_key(),
                        "checked_at": datetime.now(timezone.utc).timestamp(),
                        "availability": availability,
                        "error": availability_error,
                    }
                if availability_error is None:
                    logger.info(
                        "InfluxDB-Verfügbarkeit geprüft · Entitäten=%d · mit Daten=%d · Zeitraum=%s",
                        len(known_ids), sum(1 for a in availability.values() if a.has_data), config.range_preset,
                    )
            context = self.context(selected_ids=set(config.entity_ids), config=config)
            if not known_ids:
                context["influx_availability_error"] = tr("Keine Entität zum Prüfen: nichts markiert und kein Treffer in der Liste.")
            return deps.templates.TemplateResponse(request, "_influxdb_import_section.html", context)

        @router.post("/import/influxdb/dry-run", response_class=HTMLResponse)
        async def import_influxdb_dry_run(request: Request) -> HTMLResponse:
            """Vorschau ohne Schreibvorgang: holt dieselben Rohdaten wie der
            Import und plant sie gegen das vorhandene Archiv — je Entität und
            ohne die Zeilen aller Entitäten gleichzeitig zu halten."""
            config = self._config(await request.form())
            entity_ids, unknown_ids = self.owner._known_ha_entity_ids(config.entity_ids)
            items: list[dict] = []
            errors: list[str] = [
                tr("{entity_id}: nicht in Zeitarchiv bekannt", entity_id=entity_id) for entity_id in unknown_ids
            ]
            if not entity_ids:
                if not unknown_ids:
                    errors.append(tr("Bitte mindestens eine Entität auswählen."))
                return deps.templates.TemplateResponse(
                    request, "_influxdb_import_dry_run.html", {"items": items, "errors": errors}
                )

            start, end = self._date_range(config.range_preset, config.date_from, config.date_to)

            def plan_all() -> None:
                for entity_id in entity_ids:
                    try:
                        sections = self._sections(entity_id, config, start, end)
                    except (influxdb_import.InfluxApiError, ValueError) as exc:
                        logger.warning("InfluxDB-Historie für %s nicht abrufbar · %s", entity_id, exc)
                        errors.append(f"{entity_id}: {exc}")
                        continue
                    span, plans = _Span(), []
                    for number, section in enumerate(sections, 1):
                        prefix = (
                            f"{entity_id} ({tr('Abschnitt')} {number}/{len(sections)}, {self._section_label(section)})"
                            if len(sections) > 1 else entity_id
                        )
                        try:
                            history = self._fetch_history(entity_id, config, section[0], section[1], dry=True)
                        except (influxdb_import.InfluxApiError, ValueError) as exc:
                            logger.warning("InfluxDB-Historie für %s nicht abrufbar · %s", entity_id, exc)
                            errors.append(f"{prefix}: {exc}")
                            continue
                        span.add(history)
                        with deps.coordinator.entities([entity_id]):
                            try:
                                plans.append(symcon_import.plan_import_rows(
                                    deps.data_dir, deps.index, history.rows, entity_id, deps.tz,
                                    source_label=entity_id, skipped_rows=history.skipped,
                                    include_existing_months=config.include_existing_months,
                                ))
                            except ValueError as exc:
                                errors.append(f"{prefix}: {exc}")
                                continue
                    if plans:
                        items.append({
                            **self._item_head(entity_id, span), "plan": _merge(plans), "sections": len(sections),
                        })

            await run_in_threadpool(plan_all)
            logger.debug(
                "InfluxDB-Dry-Run · Entitäten=%d · geplante Zeilen=%d · Fehler=%d",
                len(items),
                sum(i["plan"].rows_to_import + i["plan"].rows_to_merge + i["plan"].rows_to_update for i in items),
                len(errors),
            )
            return deps.templates.TemplateResponse(
                request, "_influxdb_import_dry_run.html", {"items": items, "errors": errors}
            )

        @router.post("/import/influxdb/start", response_class=HTMLResponse)
        async def import_influxdb_start(request: Request) -> HTMLResponse:
            """Läuft im Hintergrund mit Fortschritt je Entität: der Abruf (der
            langsame Teil) liegt vor jeder Sperre, exclusive() hält nur das
            Schreiben einer einzelnen Entität."""
            started_at = datetime.now(timezone.utc)
            config = self._config(await request.form())
            entity_ids, unknown_ids = self.owner._known_ha_entity_ids(config.entity_ids)
            vorab_fehler = [
                tr("{entity_id}: nicht in Zeitarchiv bekannt", entity_id=entity_id) for entity_id in unknown_ids
            ]
            if not entity_ids:
                if not unknown_ids:
                    vorab_fehler.append(tr("Bitte mindestens eine Entität auswählen."))
                return deps.templates.TemplateResponse(
                    request, "_influxdb_import_result.html", {"items": [], "errors": vorab_fehler}
                )

            def write_report(items: list[dict], errors: list[str], reconciliation_report: dict | None) -> None:
                """Auch nach einem Fehlschlag: der Bericht ist die einzige
                dauerhafte Spur des Versuchs. Ohne Passwort."""
                with deps.coordinator.exclusive():
                    report_results = []
                    for item in items:
                        result_payload = dataclasses.asdict(item["result"])
                        result_payload["available_label"] = item["available_label"]
                        if item.get("sections", 1) > 1:
                            result_payload["sections"] = item["sections"]
                        report_results.append(result_payload)
                    import_reports.create(
                        deps.data_dir,
                        source_type="influxdb",
                        started_at=started_at,
                        source={"filename": "InfluxDB", "size_bytes": 0},
                        configuration=config.public(),
                        results=report_results,
                        errors=errors,
                        reconciliation=reconciliation_report,
                    )

            def run_import() -> dict:
                items: list[dict] = []
                errors: list[str] = list(vorab_fehler)
                reconciliation_report = None
                written: list[str] = []
                cancelled = False
                self._cancel.clear()
                logger.info(
                    "InfluxDB-Import gestartet · Entitäten=%d · Zeitraum=%s", len(entity_ids), config.range_preset,
                )
                start, end = self._date_range(config.range_preset, config.date_from, config.date_to)
                self._progress.set_phase(
                    N_("Daten werden aus InfluxDB geholt und geschrieben…"), len(entity_ids), unit=N_("Entitäten"),
                )
                try:
                    for nummer, entity_id in enumerate(entity_ids):
                        if self._cancel.is_set():
                            cancelled = True
                            break
                        self._progress.advance(nummer, entity_id)
                        try:
                            sections = self._sections(entity_id, config, start, end)
                        except (influxdb_import.InfluxApiError, ValueError) as exc:
                            logger.warning("InfluxDB-Historie für %s nicht abrufbar · %s", entity_id, exc)
                            errors.append(f"{entity_id}: {exc}")
                            continue
                        span, results = _Span(), []
                        for number, section in enumerate(sections, 1):
                            if self._cancel.is_set():
                                cancelled = True
                                break
                            section_text = f"{tr('Abschnitt')} {number}/{len(sections)}, {self._section_label(section)}"
                            prefix = f"{entity_id} ({section_text})" if len(sections) > 1 else entity_id
                            if len(sections) > 1:
                                self._progress.advance(nummer, f"{entity_id} · {section_text}")
                            try:
                                history = self._fetch_history(entity_id, config, section[0], section[1], dry=False)
                            except influxdb_import.ImportCancelled:
                                cancelled = True
                                break
                            except (influxdb_import.InfluxApiError, ValueError) as exc:
                                logger.warning("InfluxDB-Historie für %s nicht abrufbar · %s", entity_id, exc)
                                errors.append(f"{prefix}: {exc}")
                                continue
                            span.add(history)
                            # Entitätssperre statt exclusive(): geschrieben wird genau diese Entität
                            # (Archiv, Rollups, Hot Buffer, Indexzeilen); die globale Sperre hielte
                            # sonst auch Live-Aufnahme und Oberfläche für die ganze Schreibdauer an.
                            with deps.coordinator.entity(entity_id):
                                try:
                                    results.append(symcon_import.import_rows(
                                        deps.data_dir, deps.index, history.rows, entity_id, deps.tz,
                                        source_label=entity_id, skipped_rows=history.skipped,
                                        include_existing_months=config.include_existing_months,
                                    ))
                                except ValueError as exc:
                                    errors.append(f"{prefix}: {exc}")
                                    continue
                            # Die Zeilen sind geschrieben; nur die kleine Ergebnisübersicht bleibt.
                            del history
                        if results:
                            written.append(entity_id)
                            items.append({
                                **self._item_head(entity_id, span), "result": _merge(results),
                                "sections": len(sections),
                            })
                        if cancelled:
                            errors.append(tr("Import abgebrochen. Bereits geschriebene Monate bleiben erhalten."))
                            break
                    self._progress.advance(len(entity_ids), "")
                    if written:
                        with deps.coordinator.exclusive():
                            reconciliation_report = deps.run_storage_reconciliation(
                                entity_ids=sorted(written), repair=True
                            )
                    results = [item["result"] for item in items]
                    logger.info(
                        "InfluxDB-Import abgeschlossen · Entitäten=%d · Zeilen importiert=%d · "
                        "Zeilen zusammengeführt=%d · bestehende Monate ergänzt=%d · Fehler=%d",
                        len(results),
                        sum(r.rows_imported for r in results),
                        sum(r.rows_merged for r in results),
                        sum(r.rows_updated for r in results),
                        len(errors),
                    )
                    if results and not errors and sum(
                        r.rows_imported + r.rows_merged + r.rows_updated for r in results
                    ) == 0:
                        # Unterscheidet "nichts Neues seit dem letzten Import" von
                        # einem stillen Fehlschlag, der wie ein Erfolg aussähe.
                        logger.warning(
                            "InfluxDB-Import ohne Fehler abgeschlossen, aber 0 Zeilen geschrieben · Entitäten=%d",
                            len(results),
                        )
                except Exception as exc:  # noqa: BLE001 — landet als Text im Ergebnis
                    logger.exception("InfluxDB-Import unerwartet fehlgeschlagen")
                    errors.append(tr("Import abgebrochen: {exc}", exc=exc))

                try:
                    self._progress.set_phase(N_("Importbericht wird geschrieben…"))
                    write_report(items, errors, reconciliation_report)
                except Exception:  # noqa: BLE001 — ein fehlender Bericht darf den Import nicht kippen
                    logger.exception("InfluxDB-Importreport konnte nicht gespeichert werden")
                return {"items": items, "errors": errors}

            try:
                self._progress.start(run_import, logger)
            except JobBusy:
                logger.info("InfluxDB-Import bereits aktiv · event=influxdb_import_already_running")
            return deps.templates.TemplateResponse(request, "_job_progress.html", self._progress_context())

        @router.post("/import/influxdb/cancel", response_class=HTMLResponse)
        def import_influxdb_cancel(request: Request) -> HTMLResponse:
            """Bricht zwischen zwei Abschnitten bzw. im Abruf ab; ein Schreibvorgang
            läuft immer zu Ende. Der Knopf zeigt danach wieder die Fortschrittsanzeige."""
            if self._progress.snapshot()["running"]:
                self._cancel.set()
                logger.info("InfluxDB-Import: Abbruch angefordert · event=influxdb_import_cancel_requested")
            return deps.templates.TemplateResponse(request, "_job_progress.html", self._progress_context())

        @router.get("/import/influxdb/progress", response_class=HTMLResponse)
        def import_influxdb_progress(request: Request) -> HTMLResponse:
            """Poll-Ziel des InfluxDB-Imports — Anzeige oder Endergebnis,
            letzteres ohne hx-trigger, wodurch das Polling von selbst endet."""
            stand = self._progress.snapshot()
            if not stand["started"]:
                return HTMLResponse("")
            if stand["running"]:
                return deps.templates.TemplateResponse(request, "_job_progress.html", self._progress_context())
            ergebnis = dict(stand["result"] or {"items": [], "errors": []})
            if stand["error"]:
                ergebnis["errors"] = [*ergebnis.get("errors", []), f"Abgebrochen: {stand['error']}"]
            return deps.templates.TemplateResponse(request, "_influxdb_import_result.html", ergebnis)


class _EmptyForm:
    """Standardwerte für den Seitenaufbau, bevor ein Formular gesendet wurde."""

    def get(self, key, default=None):  # noqa: ANN001
        # Die Zertifikatsprüfung ist standardmäßig an.
        return "on" if key == "verify_tls" else default

    def getlist(self, key):  # noqa: ANN001
        return []
