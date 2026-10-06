# Tests

*[Deutsche Version](../testing.md)*

A shared test suite for app **and** integration lives under `tests/` in the
repository root (not inside `addon/`) — both products are tested together,
since the integration simulates real requests against the app's typical
response shapes.

```bash
python3 -m pytest -q
```

## Sync into the product repos

`tests/` is transferred file by file to `HA-Apps/zeitarchiv/tests/` (app) or
`HA-Zeitarchiv/tests/` (integration), classified automatically by their actual
imports (`scripts/sync_tests.py`, analogous to `scripts/sync_versions.py`):

```bash
python3 scripts/sync_tests.py          # transfers, lists changes
python3 scripts/sync_tests.py --check  # check only, exit code 1 on drift
```

A file that cannot be assigned unambiguously (signals for both target repos or
for neither) aborts the run instead of being silently skipped — details and the
two documented exceptions (`test_routes.py`, `test_metadata_and_versions.py`)
in the script's docstring.

No `pytest.ini`/`pyproject.toml` — standard discovery via `tests/test_*.py`.
`tests/_pkg.py` registers `custom_components.zeitarchiv` as a namespace package
**without** executing its `__init__.py`, so that integration modules
(`const.py`, `events.py`, `filtering.py`, `queue_writer.py`, …) can be imported
in isolation without needing a real `homeassistant` package (which is not
installed in the test environment).

## Coverage focus areas (selection)

| File | Checks |
| --- | --- |
| `test_ingestion.py` | Idempotency, dedup, crash recovery (see [ingestion.md](ingestion.md)) |
| `test_logging.py`, `test_api_observability.py`, `test_logs_template.py` | Secret redaction, ISO timestamps, rate limits, request correlation, capture TTL, entity trace and log sources |
| `test_index.py` | SQLite schema, migrations, aggregations |
| `test_query.py`, `test_period_navigation.py` | Time window/range logic |
| `test_rollup.py`, `test_rotate.py` | Bucket calculation, hot → archive transition |
| `test_retention.py`, `test_cleanup.py` | Retention, soft delete/purge |
| `test_backup.py`, `test_backup_scheduler.py` | Backup format, restore validation, schedule |
| `test_notices_route_latest_backup.py`, `test_api_get_notices.py` | `latest_backup` field in `/api/notices` (last *successful* job, not the last one at all) and its parsing in the HA integration API client |
| `test_storage_coordinator.py` | `StorageCoordinator` locks (concurrency, timeout/`CoordinatorBusy`) |
| `test_http_middleware.py` | Security headers, `X-Request-ID`, access log correlation (global ASGI middleware in `main.py`) |
| `test_energiedashboard_config_schema.py` | `energiedashboard_config` `schema_version` (downgrade protection) |
| `test_security.py` | Token generation/comparison |
| `test_paths.py` | Entity ID validation, symlink/traversal protection |
| `test_csv_import.py`, `test_import_reports.py` | Import pipeline |
| `test_route_modules.py`, `test_metadata_and_versions.py` | Route registration, version consistency (`sync_versions.py`) |
| `test_base_template.py` | The shared page frame keeps its promises (`<!doctype>`, stylesheet, font size block) — **and no page repeats them** |
| `test_selfhosted_fonts.py` | The fonts come from `static/fonts/`: files present and used, paths Ingress-proof, no external host, CSP correspondingly narrow |
| `test_asset_versions.py` | The cache buster follows the content, not the timestamp — per file individually, and every `asset()` path exists |
| `test_ingress_prefix.py` | URL prefix under Ingress: what is checked is the *resolution* of every asset reference (`urljoin` against the header path), not its spelling |
| `test_requirements_lock.py` | `requirements.txt` completely pinned and consistent with the Dockerfile |
| `test_page_scripts.py` | Page-local JavaScript lives as a file: nothing remains in the template that *does* something |
| `test_hint_roles.py`, `test_hint_toggle.py` | Roles of the hint texts (warning/status never fold away), info button including 44 px hit area |
| `test_unstorable_measurements.py` | NaN/Inf are sorted out on receipt instead of archived |
| `test_csv_import_locking.py` | The CSV import locks only its own entity and passes the rows through without a copy |
| `test_zip_guard.py` | ZIP entry limit takes effect **before** the central directory is read |
| `test_marked_points.py` | Ranges marked for deletion: adjacent marks become one band (measured threshold, 5-minute cadence does not fall apart), widely separated ones stay separate; duplicates count per occurrence; band list capped, total count not |
| `test_rows_filter_and_menu_clamp.py` | Mark filter as dropdown with hit counts (empty categories not selectable), value table stays a table on mobile, action row as the table's header, options menu clamped to the viewport |
| `test_seitengrund_und_kartenschatten.py` | The page background darkens downward — and `--bg-shade` is a raw `rgba` per scheme, not a color token (which inverts in dark schemes and would eat the card edge); shadow only on free-standing surfaces, not on nested ones |
| `test_hilfe_popover.py`, `test_import_anleitungen_einklappbar.py` | The help box at the "i" of the HA import options hangs on the block on mobile instead of on the badge (otherwise it sticks out of the picture); the three "How … works" guides collapse only below the breakpoint |
| `test_entity_chart_toolbar.py` | A second click on the active period step jumps back to now (replacement for the "Now" button, template in the energy dashboard), Compare/Options right-aligned |
| `test_chart_zoom.py` | Chart zoom: wheel alone continues to scroll the page, one-finger swipe stays with the phone, threshold (evaluated exactly once) and timeline exception, dragging out an area via Shift (brush instead of the ineffective toolbox, icons unsubscribed in two places, keyboard state via keydown/keyup/blur), hint line below instead of in the card — and that no other chart of the app gets a zoom |
| `test_*_template.py`, `test_*_breadcrumbs.py` | Template rendering/structure without a running server (Jinja rendered directly and checked for expected fragments) |
| `test_long_running_feedback.py` | Feedback for long actions: that **every** one of them registers with the bell (list explicitly in the test), that `JobProgress` leaves a final state in any case, that no bar claims a total that nobody knows — and that the display is there **during** the maintenance lock, not only afterwards |
| `test_config_flow_sortable_entities.py`, `test_options_transfer.py` | Integration-side config flow logic |

## What is deliberately missing here

No browser/E2E test (no Playwright/Selenium) — Alpine.js/htmx interactions are
verified manually in the browser. Template tests check rendered HTML output,
not client-side behavior.

## `main.py` line budget

`test_route_modules.py::test_main_keeps_external_api_and_report_routes_out_of_the_monolith`
enforces an upper bound for `len(main.py.splitlines())` as an architecture
guard against uncontrolled growth of the monolith. Extracted are `/api/*`
(`api_routes.py`), import reports (`report_routes.py`), since 0.51.0 the
complete Symcon/CSV/Home Assistant import (`import_routes.py`), since 0.82.0
the Housekeeping page (`housekeeping_routes.py`) and since 0.85.0 the
background work (`background.py`: maintenance scheduler, backup, retention and
reconciliation runs including their state — 623 lines fewer in main.py).
Pattern in each case: a `*Dependencies` frozen dataclass plus a `*Service` —
for the route modules with `.router()`, which registers the routes as nested
closures (`ReportService`/`ImportService` as a template for further
extractions).

**The threshold has been lowered twice, not raised:** from 5,850 on September
7, 2026 to 5,700, on September 8 to **5,150**. As of 0.85.0: **about 5,100
lines**, test green, a good 50 lines of buffer.

The reason for this bookkeeping is stated in the comment on the test itself and
is worth repeating here: at 5,850, the comment there said the next step was a
`housekeeping_routes.py` of its own and **not** another raising. Exactly that
then happened — but the comment did not know it and still named the way out as
available. Whoever would have breached the limit next would have read an
instruction already carried out and, lacking an alternative, raised the number
after all. **Anyone who carries out an extraction therefore records it in the
test comment as done.**

The next cut is named accordingly and is harder than the previous two: the
**template contexts**. Of the roughly 5,100 lines, about 1,730 are route
functions (34 %) across 112 routes; the rest are mostly context builders
(`_rows_fragment`, `_dashboard_tiles_context`, `_entities_table_response` …).
They are more tightly interlinked with the routes than the background work was
— a cut there first needs an answer to what a context builder may know about the
request.
