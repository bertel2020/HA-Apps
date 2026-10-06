# Architecture

*[Deutsche Version](../architecture.md)*

## Process model

One container, two processes, one application code:

```text
┌─────────────────────────────────────────────────────────────┐
│ Container                                                    │
│                                                               │
│  nginx (gateway)                    uvicorn (app.main:app)   │
│  ├─ :8099  Ingress server    ──►    :8128 (127.0.0.1, internal)│
│  │  IP allowlist: Supervisor,                                │
│  │  localhost. Everything / → app.                           │
│  │                                                            │
│  └─ :8127  Public server     ──►    :8128                    │
│     Only /api/health, /api/write,                            │
│     /api/notices proxied. Everything                         │
│     else → 404 directly at the gateway,                      │
│     never reaches the app.                                   │
└─────────────────────────────────────────────────────────────┘
```

`run.sh` starts both processes and terminates the container as soon as one of
them ends unexpectedly (no half-healthy state). See `nginx.conf`/`run.sh` in
the repo root for the exact configuration.

**Important:** The separation between Ingress and public access is purely a
gateway decision. `app/main.py` registers all routes (UI + `/api/*`) on **one**
FastAPI instance; there is no second app instance and no route-side network
check. Anyone who bypasses nginx and reaches `127.0.0.1:8128` directly sees the
full app without IP filter (see [security.md](security.md) for the lines of
defense that still apply then: bearer token on `/api/*`, otherwise none).

## Demo mode: BASE_DIR/DATA_DIR resolution

`main.py` resolves the actual data directory once at module import in three
steps (`main.py:169-172`):

```python
BASE_DIR = Path(os.environ.get("ZEITARCHIV_DATA_DIR", "/data"))
_OPTIONS = demo_mode.load_options(BASE_DIR)
DEMO_MODE = demo_mode.resolve_demo_mode(_OPTIONS)
DATA_DIR = demo_mode.demo_dir(BASE_DIR) if DEMO_MODE else BASE_DIR
```

`BASE_DIR` remains the fixed path mounted by the Supervisor — regardless of
whether this instance is currently running in demo mode.
`demo_mode.load_options()` therefore **always** reads `options.json` from
`BASE_DIR`, never from `DATA_DIR`: `DATA_DIR` itself depends on an option
(`demo_mode`) in `options.json`, so access via `DATA_DIR` would be circular or,
in demo mode, read from the wrong, empty subfolder. `resolve_demo_mode()`
additionally checks `ZEITARCHIV_DEMO_MODE=1` as a fallback for Docker Compose/
venv operation without Supervisor, analogous to `ZEITARCHIV_TIMEZONE`.

Everything built in the module afterwards — `Index`, `StorageCoordinator`,
`IngestionService`, every route — uses `DATA_DIR` exclusively. The rest of the
code is thus structurally unaware of demo mode: it simply sees a data
directory in which either real or synthetic values lie, without any case
distinction of its own.

**No runtime switch.** `DATA_DIR` & co. are process globals that no dependency
injection layer could rebind at runtime — a change between demo and normal
operation therefore always means a complete process restart (Supervisor:
automatically after a configuration change), never an in-app click.

`app/demo_mode.py` deliberately knows **only the file system**, never the
running `Index` of a demo instance: `demo_dir_info()`/`remove_demo_dir()` must
be safely callable even when the currently running process is NOT in demo mode
(Housekeeping state "unused") and therefore has no open connection to the demo
`index.sqlite` — `Index.__init__()` always opens its file read-write and even
creates it anew if necessary, so a mere look from outside would be a real,
unwanted write access to a directory that nobody is actively using at the
moment. The generation itself (`run_generation()`) lives in
`app/demo_generation.py`, not in the CLI script `scripts/generate_demo_data.py`
— since then, the latter imports from `app/` in the opposite direction, so that
the Housekeeping routes can call the same simulation core without a backward
dependency `app/` → `scripts/` (see [demo-data.md](../demo-data.md), German).

## Request flow (write path)

```text
Zeitarchiv integration (Home Assistant)
   │ POST /api/write  { events: [...] }
   │ Authorization: Bearer <token>
   ▼
nginx :8127  (allowlist: only /api/write, /api/health)
   ▼
app.api_routes.write()
   │ Token check (secrets.compare_digest)
   │ per event:
   ▼
storage.ingestion.IngestionService.ingest()
   │ StorageCoordinator.entity(entity_id)  ← serialized per entity
   │ Idempotency claim (SQLite ingested_events)
   │ Dedup (event ID, then timestamp)
   │ Resolution/value change filter
   │ Counter decrease detection (only logged, not blocked)
   ▼
storage.rotate.rotate_if_needed()   ← month change? Hot → archive + rollup
   ▼
storage.hotbuffer.append()          ← CSV append, current month
```

Details on each step: [ingestion.md](ingestion.md).

## Request flow (read path, chart/table)

```text
Browser (Alpine.js component)
   │ GET /api/query-multi?... (charts)
   │ POST /api/query-table {...} (comparison tables)
   ▼
app.api_routes.api_query_multi() / api_query_table()
   │ @locked(...)  ← entity read lock (prevents reading during rewrite)
   ▼
storage.query.query_series()  per entity
   │ Time window from range_key + offset (query._window())
   │ Within the current period: live from hot buffer
   │ Completed periods: from precomputed rollup (rollup.py)
   ▼
JSON {series: [{points: [...]}]}  → ECharts in the browser
JSON {columns: [{series: [{aggregates: {...}}]}]} → table renderer
```

Comparison tables (`table-compute.js`) send all required entities and periods
together to `/api/query-table`. A request-local read cache prevents the same
hot buffer file from being parsed again for several columns. The server returns
per entity and period only `auto`/`avg`/`min`/`max`/`sum`; group and formula
rows remain presentation logic in the browser. Only the column/row structure is
still saved (`saved_tables`, see [data-model.md](data-model.md)).

## Concurrency

`storage/coordinator.py` (`StorageCoordinator`) is the only synchronization
primitive for file access:

- **`entity(id)` / `entities(ids)`** — per-entity locks (RLock), any number of
  different entities run in parallel. Multi-locks (`entities`) sort the IDs
  before acquiring to rule out deadlocks with overlapping operations.
- **`exclusive()`** — global maintenance lock for backup, restore, retention
  enforcement, rotation batch and purge. Waits until all running entity
  operations have completed AND meanwhile blocks new entity operations (no
  starvation through continuous write traffic).

The model is deliberately not a global lock: Home Assistant write traffic on
entity A may continue while entity B is being edited in the cleanup tool. Only
true maintenance operations (which may touch the ENTIRE inventory) pause
everything else.

Since 0.80.2, all three methods accept an optional `timeout` (default still
`None` = unlimited, unchanged for backup/retention/rotation/purge/import).
Synchronous HTTP routes run via `storage_locked()` (`route_support.py`) with a
default of 30 s: if a caller holds a lock and hangs, the waiting request fails
visibly after expiry with `CoordinatorBusy` (503) instead of blocking the
requesting HTTP worker thread forever — analogous to `IndexBusy`/`_TimeoutLock`
in the SQLite index (`storage/index.py`).

These locks are **in-process** (in-memory, `threading`). There is exactly one
app process per container — a second process on the same data (e.g. two
containers against the same `/data` volume) would bypass the coordinator's
guarantees. This is not a supported deployment.

## Event-driven background scheduler

`background.py:BackgroundService._maintenance_scheduler_loop()` runs as a
daemon thread, checked every 30 seconds, independent of page views:

- hourly statistics snapshot (`stats_snapshots`)
- hourly RAM snapshot (Supervisor API, if available)
- retention overview/duplicate overview/cleanup preview: recalculated at most
  once per hour, cached from SQLite `settings` (see
  [data-model.md](data-model.md) → "Cached previews")
- scheduled backups, scheduled retention enforcement (both: a missed run after
  downtime is caught up, never several in parallel)
- host storage space (`shutil.disk_usage(DATA_DIR)`) and the available
  integration version (GitHub raw fetch, at most once a day,
  `ha_integration.py`) — both purely informational, their failure (e.g. no
  internet) never blocks the write path

Each step runs in its own `with self._maintenance_step("<name>")`: an error is
caught and logged there (throttled, see
[operations.md](operations.md#maintenance-scheduler)), the remaining steps of
the pass continue. The loop's outer `except Exception` remains as a fallback. A
new step belongs in such a `with` block —
`tests/test_maintenance_step_isolation.py` checks this via AST.

A second, independent daemon thread
(`background.py:BackgroundService._background_storage_reconciliation()`)
reconciles the storage index entity by entity with archive/hot buffer once at
startup (afterwards it terminates) (see `storage/reconcile.py`) — runs in the
background only if the last shutdown was clean, otherwise (restore/crash)
synchronously before the first request.

Both threads belong, since 0.85.0, to the `BackgroundService` in
`background.py` instead of main.py — with them, their entire state
(backup/retention progress, cached previews, heartbeats) has moved there as
well. main.py now only holds the instance; the area modules receive it via
their `*Dependencies` dataclass. The module does not know main.py (a test
checks that it contains neither `from .main import` nor any `global` at all) so
that two services in one process do not overwrite each other.

Both threads write a timestamp on every pass or every checked entity
(`last_scheduler_tick` / `last_reconcile_tick`). If one of them stays without
progress for more than 5 minutes (deadlock on the index or storage lock), a
notice appears in the notice center (`system.scheduler_stalled` /
`system.storage_reconcile_stalled`, `notices.py`) — the app remains reachable
for all other requests, only the respective thread hangs.

## Feedback for long actions

Measured (0.84.0, inventory with 163 months of history), several actions take
considerably longer than the five to ten seconds from which a click without
feedback feels like a hang: Symcon trial run over 233 variables about 1.7
minutes, cleanup about 20 seconds (of which 15.6 s rollup recalculation), CSV
parsing of 104 MB 6.0 seconds, rollup rebuild after a type change a good 5
seconds. The most detailed progress display of the app sat, of all places, on
the fastest of these actions (backup, 2.4 s).

`progress.py` is the common foundation so that this pattern — background
thread, shared state behind a lock, htmx polling — does not arise by hand for
the sixth time:

- **`JobProgress`** holds the state of exactly one job (is it running, which
  phase, how far, what came out). It is read only via `snapshot()` so that no
  caller combines two fields from two moments.
- **Lifecycle:** `claim()` occupies (in the request thread, so that a second
  click gets an immediate answer) and otherwise raises `JobBusy`; `run()`
  executes and in any case leaves a final state; `start()` is both together in
  a daemon thread. `claim()` alone does **not** release on clean exit — only
  `run()` does, in its `finally`.
- **`track()`** for work that stays in the request thread (rotation, index
  optimization, rollup rebuild): always releases, but lets the exception
  through (the caller is a request handler with its own error handling) and
  raises no `JobBusy` when a job is already running — the order is governed by
  the locks of the `StorageCoordinator`, not by this display.
- **Registry:** `register_source(id, label, read)` and `activity_snapshot()`. A
  `JobProgress` registers itself as soon as it gets a `label`; the two older
  states (Symcon import, backup) register with a small adapter instead of being
  rewritten. `activity_snapshot()` returns only the RUNNING operations and
  never raises: the context processor calls it on every response, a broken
  source must not drag down every page.

All thirteen long actions are registered:

| Job | Runs | Triggered by |
| --- | --- | --- |
| Symcon preview, Symcon import | Background thread | Click |
| CSV import, Home Assistant import | Background thread | Click |
| Cleanup | Background thread | Click |
| Backup, retention | Background thread | Click or schedule |
| Rotation, index optimization | Request thread (`track()`) | Click |
| Storage reconciliation | Daemon thread at startup | Nothing |
| Hourly rollup backfill | Maintenance scheduler | Configuration change, minutes before |
| Rollup rebuild | Write path (`/api/write`) | Home Assistant |
| Demo data generation | Background thread (`demo_mode.demo_progress`, see above) | Click, schedule or first start in demo mode |

The bottom three rows are the actual reason for the registry: anyone who
pressed nothing does not look for an explanation for a sluggish server either.
Several of the jobs — cleanup, backup, retention, rotation, index optimization
and parts of the imports — hold `StorageCoordinator.exclusive()` in the process
and bring the entire application, including ingestion, to a standstill. This
becomes visible only in the header bar: the browser's own loading state only
ever reaches the tab in which the click happened.

**For new long actions:** create a `JobProgress` with `label` in the module's
own file (not in main.py — it has a line budget, see
[testing.md](testing.md)), wrap the work in `start()` or `track()`.
The display then arises by itself. `test_long_running_feedback.py` explicitly
lists the expected registrations: anyone who builds a long action and does not
register it has to change this list deliberately. What button, bar and bell
look like in the browser: [frontend.md](frontend.md).

## Frontend rendering

Server-side rendering (Jinja2) for the initial page build, htmx for partial
reloads (forms, polling), Alpine.js for client-side state (table/chart editors,
dropdown pickers), ECharts for diagrams. No build step, no bundlers — all JS
files are delivered unchanged under `static/js/`. All full pages inherit their
frame from `templates/base.html`; every path in the HTML carries the prefix
`{{ app_root }}`, which a context processor derives from the `X-Ingress-Path`
header. Details: [frontend.md](frontend.md).
