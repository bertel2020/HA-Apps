"""Import-Report-Aufbereitung und zugehörige HTTP-Routen."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from .formatting import format_int, format_size, format_value
from .storage import import_reports
from .storage.coordinator import StorageCoordinator


STATUS_LABELS = {
    "success": "Erfolgreich",
    "partial": "Teilweise erfolgreich",
    "no_changes": "Keine Änderungen",
    "failed": "Fehlgeschlagen",
}
SOURCE_LABELS = {"symcon": "Symcon", "csv": "CSV", "ha": "Home Assistant"}
SORT_COLUMNS = [
    ("finished_at", "Zeitpunkt"), ("source_type", "Quelle"),
    ("status", "Status"), ("targets", "Ziele"),
    ("rows_written", "Importiert"), ("rows_skipped", "Übersprungen"),
    ("duration_seconds", "Dauer"),
]


@dataclass(frozen=True)
class ReportDependencies:
    data_dir: Path
    tz: ZoneInfo
    coordinator: StorageCoordinator
    templates: Jinja2Templates
    app_root_context: Callable[[Request], dict]


class ReportService:
    def __init__(self, deps: ReportDependencies) -> None:
        self.deps = deps

    def view(self, report: dict) -> dict:
        try:
            finished = datetime.fromisoformat(report["finished_at"]).astimezone(self.deps.tz)
            finished_label, finished_date = (
                finished.strftime("%d.%m.%Y %H:%M:%S"), finished.date().isoformat()
            )
        except (KeyError, TypeError, ValueError):
            finished_label, finished_date = "—", ""
        summary = report.get("summary", {})
        rows_written = int(summary.get("rows_imported", 0)) + int(summary.get("rows_merged", 0))
        rows_skipped = int(summary.get("rows_duplicate", 0)) + int(summary.get("rows_invalid", 0))
        duration_seconds = float(report.get("duration_seconds", 0) or 0)
        # *_label-Varianten fürs Template (NUMBER_LOCALE-Format, siehe
        # formatting.py) — rows_written/rows_skipped selbst bleiben rohe int,
        # weil context() sie als Sortierschlüssel braucht (sort_value()).
        return {
            **report,
            "finished_label": finished_label,
            "finished_date": finished_date,
            "source_label": SOURCE_LABELS.get(report.get("source_type"), "Unbekannt"),
            "status_label": STATUS_LABELS.get(report.get("status"), "Unbekannt"),
            "source_size": format_size(int(report.get("source", {}).get("size_bytes", 0) or 0)),
            "rows_written": rows_written,
            "rows_skipped": rows_skipped,
            "rows_written_label": format_int(rows_written),
            "rows_skipped_label": format_int(rows_skipped),
            "duration_label": format_value(duration_seconds, decimals=1),
            "duration_label_precise": format_value(duration_seconds, decimals=3),
            "summary_label": {
                "targets": format_int(int(summary.get("targets", 0))),
                "rows_imported": format_int(int(summary.get("rows_imported", 0))),
                "rows_merged": format_int(int(summary.get("rows_merged", 0))),
                "rows_duplicate": format_int(int(summary.get("rows_duplicate", 0))),
                "rows_invalid": format_int(int(summary.get("rows_invalid", 0))),
            },
            "results": [
                {
                    **result,
                    "rows_imported_label": format_int(int(result.get("rows_imported", 0))),
                    "rows_merged_label": format_int(int(result.get("rows_merged", 0))),
                    "duplicate_rows_label": format_int(int(result.get("duplicate_rows", 0))),
                    "skipped_rows_label": format_int(int(result.get("skipped_rows", 0))),
                }
                for result in report.get("results", [])
            ],
        }

    @staticmethod
    def _paginate(rows: list, page: int, page_size: int) -> tuple[list, dict]:
        page_size = 1000 if page_size <= 0 else min(page_size, 1000)
        total = len(rows)
        total_pages = max(1, (total + page_size - 1) // page_size)
        page = max(1, min(page, total_pages))
        return rows[(page - 1) * page_size:page * page_size], {
            "page": page, "page_size": page_size, "total": total, "total_pages": total_pages,
        }

    def context(
        self, source: str = "all", status: str = "all", search: str = "",
        date_from: str = "", date_to: str = "", sort: str = "finished_at",
        direction: str = "desc", page: int = 1, page_size: int = 50,
    ) -> dict:
        with self.deps.coordinator.exclusive():
            all_reports = [self.view(report) for report in import_reports.list_all(self.deps.data_dir)]
        needle = search.strip().lower()

        def matches(report: dict) -> bool:
            return not (
                (source != "all" and report.get("source_type") != source)
                or (status != "all" and report.get("status") != status)
                or (date_from and report["finished_date"] < date_from)
                or (date_to and report["finished_date"] > date_to)
                or (needle and needle not in json.dumps(report, ensure_ascii=False).lower())
            )

        sort = sort if sort in dict(SORT_COLUMNS) else "finished_at"
        direction = direction if direction in ("asc", "desc") else "desc"
        matched = [report for report in all_reports if matches(report)]

        def sort_value(report: dict):
            value = report.get("summary", {}).get("targets", 0) if sort == "targets" else report.get(sort)
            return value if value is not None else ""

        matched.sort(key=sort_value, reverse=direction == "desc")
        reports, pagination = self._paginate(matched, page, page_size)
        columns = [{
            "key": key, "label": label,
            "next_dir": "asc" if sort == key and direction == "desc" else "desc",
            "active": sort == key,
            "arrow": ("↑" if direction == "asc" else "↓") if sort == key else "",
        } for key, label in SORT_COLUMNS]
        return {
            "reports": reports,
            "total_reports": len(all_reports),
            "counts": {key: sum(r.get("status") == key for r in all_reports) for key in STATUS_LABELS},
            "source": source, "status": status, "search": search,
            "date_from": date_from, "date_to": date_to, "sort": sort,
            "direction": direction, "columns": columns, "pagination": pagination,
            "status_options": STATUS_LABELS,
        }

    def router(self) -> APIRouter:
        router = APIRouter()
        deps = self.deps

        @router.get("/reports", response_class=RedirectResponse)
        def reports_page(
            request: Request, source: str = "all", status: str = "all", search: str = "",
            date_from: str = "", date_to: str = "", page: int = 1, page_size: int = 50,
        ) -> RedirectResponse:
            query = urlencode({
                "tab": "reports", "source": source, "status": status, "search": search,
                "date_from": date_from, "date_to": date_to, "page": page, "page_size": page_size,
            })
            return RedirectResponse(
                url=f"{deps.app_root_context(request)['app_root']}/import?{query}", status_code=307
            )

        @router.get("/reports/{report_id}", response_class=HTMLResponse)
        def report_detail(request: Request, report_id: str) -> HTMLResponse:
            with deps.coordinator.exclusive():
                report = import_reports.load(deps.data_dir, report_id)
            if report is None:
                raise HTTPException(status_code=404, detail="Report nicht gefunden")
            return deps.templates.TemplateResponse(
                request, "report_detail.html", {"report": self.view(report), "base": ".."}
            )

        @router.get("/reports/{report_id}/download")
        def report_download(report_id: str) -> Response:
            with deps.coordinator.exclusive():
                path = import_reports.download_path(deps.data_dir, report_id)
                if path is None:
                    raise HTTPException(status_code=404, detail="Report nicht gefunden")
                content = path.read_bytes()
            return Response(content=content, media_type="application/json", headers={
                "Content-Disposition": f'attachment; filename="{path.name}"'
            })

        @router.post("/reports/{report_id}/delete", response_class=RedirectResponse)
        def report_delete(request: Request, report_id: str) -> RedirectResponse:
            with deps.coordinator.exclusive():
                if not import_reports.delete(deps.data_dir, report_id):
                    raise HTTPException(status_code=404, detail="Report nicht gefunden")
            return RedirectResponse(
                url=f"{deps.app_root_context(request)['app_root']}/import?tab=reports", status_code=303
            )

        @router.post("/reports/delete-all", response_class=RedirectResponse)
        def reports_delete_all(request: Request) -> RedirectResponse:
            with deps.coordinator.exclusive():
                import_reports.delete_all(deps.data_dir)
            return RedirectResponse(
                url=f"{deps.app_root_context(request)['app_root']}/import?tab=reports", status_code=303
            )

        return router
