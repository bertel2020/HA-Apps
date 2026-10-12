# Zeitarchiv — Documentation

*[Deutsche Version](../README.md)*

For a short overview (installation, feature list) see the
[app README](../../README.en.md).

> **Language note:** All documents listed here are available in English in
> `docs/en/`; the German originals live one level up.

**Two separate products, one data flow.** These documents describe only the
**app** (`addon/`, here in the repo as `app/`). The write path on the Home
Assistant side is handled by the separately published
**[Zeitarchiv integration](https://github.com/bertel2020/HA-Zeitarchiv)**
(locally `custom_components/zeitarchiv/`, its own README, its own test run,
its own changelog) — it sends to `/api/write`
([api-reference.md](api-reference.md)) and is covered from the app's
point of view in [ingestion.md](ingestion.md) and
[architecture.md](architecture.md). Maintaining both repos
together (versioning, sync, release order): [operations.md](operations.md).

Support the project: [Buy Me a Coffee](https://buymeacoffee.com/bertel2020) ·
[Ko-fi](https://ko-fi.com/bertel2020) · [PayPal](https://paypal.me/RobertoMartins)

## For users

| Document | Contents |
| --- | --- |
| [user-guide.md](user-guide.md) | Detailed, task-oriented user guide — setup, every page in detail, settings reference, typical tasks |

## For developers and maintainers

| Document | Contents |
| --- | --- |
| [architecture.md](architecture.md) | Process model, request flow, nginx gateway, concurrency |
| [data-model.md](data-model.md) | Storage formats (hot buffer/archive/rollup), SQLite schema, deletion/retention lifecycle |
| [ingestion.md](ingestion.md) | Write path: idempotency, filter rules, counter semantics, rotation |
| [logging.md](logging.md) | Logging operations: sources, levels, redaction, correlation, ingest observability |
| [api-reference.md](api-reference.md) | REST API (`/api/write`, `/api/health`, `/api/query[-multi]`) |
| [frontend.md](frontend.md) | Template/JS architecture (Jinja, Alpine.js, htmx, ECharts) |
| [operations.md](operations.md) | Backup/restore, retention, maintenance scheduler, versioning/release |
| [security.md](security.md) | Auth model, network separation, path/zip validation, resource limits |
| [testing.md](testing.md) | Test suite overview, execution |
| [development.md](development.md) | Local setup (Docker Compose or venv), test run, version sync |
| [demo-data.md](demo-data.md) | Generating and importing synthetic demo data (`scripts/generate_demo_data.py`) |

## Rough layout

```text
app/
  main.py              FastAPI app, Ingress routes (line budget: testing.md)
  api_routes.py         Public REST API (/api/write, /api/health, /api/query*)
  import_routes.py       Ingress routes for Symcon/CSV/HA import
  report_routes.py        Ingress routes for import reports
  energiedashboard_routes.py  Ingress routes of the energy dashboard
  housekeeping_routes.py       Ingress routes of the Housekeeping page
  route_support.py              Shared helper functions for Ingress routes
  background.py                  Maintenance scheduler, backup/retention/reconciliation runs including state
  backup_scheduler.py             Scheduled backups (interval, cleanup)
  index_optimization.py            Thresholds and run of the index optimization
  ha_integration.py                 Queries to the running HA instance
  healthcheck.py                     Self-test at startup
  notices.py                          Notices in the bell panel
  tips.py                              Practical tips in the notice center
  version_check.py                      Update check against GitHub
  security.py                            Token generation/verification
  formatting.py                           Number/date/label formatting (Jinja filters)
  progress.py                              Progress and registry of running jobs
  limits.py                                 Central resource/size limits
  log_source.py, logging_setup.py            Log configuration and access (diagnostics page)
  supervisor_stats.py                         Supervisor/process metrics
  timezone_config.py                           IANA time zone handling
  version.py                                    Runtime version information
  storage/
    paths.py               Path validation (entity ID, symlink protection)
    coordinator.py          Entity/exclusive locks
    hotbuffer.py            Current month (CSV, append-only)
    rotate.py               Hot buffer → archive transition
    rollup.py               Precomputed aggregates (hour…year)
    ingestion.py             Crash-safe, idempotent write path
    query.py                 Period/window logic for charts/tables
    index.py                 SQLite index (metadata, saved objects)
    reconcile.py              Index consistency reconciliation with archive/hot buffer
    cleanup.py                 Outliers/gaps/duplicates, soft delete, purge
    entity_removal.py           Final deletion/removal of an entity
    retention.py                 Final retention enforcement
    backup.py                     ZIP export/import/restore
    symcon_import.py, csv_import.py   Data import from Symcon/CSV
    ha_import.py, ha_statistics.py     Data import from the running HA instance
    import_reports.py                   Logging of executed imports
  templates/               Jinja2 pages (server-side rendering + htmx fragments)
    base.html                 Common frame of all full pages (see frontend.md)
    _hints.html               Macros for info button and collapsible hint
  static/css/app.css        Design system (own README alongside)
    pages/<page>.css        Page-local rules, linked in the page_css block
  static/js/                Alpine.js components, ECharts wrapper, mobile
                            list view (see frontend.md)
    pages/<page>.js         Page-local script, linked at the end of the body
```

The app is a single FastAPI process (see [architecture.md](architecture.md));
nginx in front separates the Ingress and the public port at the network level,
not the application itself.
