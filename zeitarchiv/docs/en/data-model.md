# Data model

*[Deutsche Version](../data-model.md)*

Raw time series data lives in files (CSV/Parquet); everything structural
(metadata, saved charts/tables/dashboards, settings, job history) lives in a
single SQLite file, `index.sqlite`. This separation is central: raw data grows
without limit and is never held in SQLite, so the index stays small and quick
to query.

## Directory layout

```text
$ZEITARCHIV_DATA_DIR/
  index.sqlite            SQLite index (see below)
  hot/
    <entity_id>-<YYYY-MM>.csv       current month, uncompressed, append-only
  archive/
    <entity_id>/
      <YYYY-MM>.parquet             completed months, zstd-compressed
  rollup/
    <entity_id>/
      stunde.parquet | tag.parquet   fine rollup level (see below)
      monat.parquet
      jahr.parquet
  backups/                          created, downloadable ZIP backups
  symcon_import/, csv_import/       temporary upload staging area (not part of the backup)
```

All paths are resolved via `storage/paths.py`, which validates every entity ID
against `^[a-z][a-z0-9_]*\.[a-z0-9_]+$` and checks every constructed path via
`Path.resolve()` against leaving the respective storage area (even across
already existing symlinks). No code path builds file names from user input
without this validation.

## Hot buffer (current month)

- Format: CSV, one line `ts,value[,event_id[,min_value,max_value]]` per
  measurement point. `min_value`/`max_value` are present only on lines that the
  resolution wrote as the average of several raw values of a time window
  (standard entities, see below) — empty on every other line, and for older
  lines without the two columns `hotbuffer.iter_records()` reads them back as
  `None`.
- Deliberately **uncompressed**: Parquet cannot be appended to continuously at
  will; a crash in the middle of writing does not make a CSV unreadable, but it
  does a Parquet file without a footer.
- A new value always lands here, never directly in the archive.
- Live queries of the current period read and aggregate this file directly (no
  rollup exists for unfinished periods).

## Resolution (write throttle)

`Index.should_accept_write()` decides before every write whether an arriving
value goes into the hot buffer at all — depending on `entities.resolution` and,
since 0.98.0, on the aggregation type:

- **Counter:** fixed clock grid (`ceil(ts/interval)*interval`) instead of
  relative to the last stored value — no more phase drift after restarts/
  connection dropouts. Values between two grid points are discarded, not
  averaged (telescoping sum stays exact).
- **Standard:** no more discarding, but `storage/resolution.py` — every raw
  value is written to the hot buffer immediately (never buffered in RAM, hence
  restart-safe); when the next window boundary is crossed, a background
  mechanism condenses the raw rows of the completed window into one avg/min/max
  row (timestamp = window **end**, not start). Safety net for silent entities:
  `_flush_stale_resolution_windows()`, checked every 5 minutes in the
  maintenance scheduler.
- **Switch:** `resolution` locked to `raw` (form field disabled, server
  additionally validates) — a real state change must never be discarded by a
  time window. Duplicates (unchanged state) are still caught by the
  independent, time-window-free `should_accept_value()`.

## Rotation (hot → archive)

`storage/rotate.py`: at an entity's first write into a new calendar month (or
manually under **Housekeeping → Rotation**), the hot file(s) of the previous
month(s) are written to the archive as Parquet, their rollup rows are computed
(`rollup.append_completed_month()`), and the hot CSV file is deleted. An entity
that stops sending would otherwise never have its last hot file archived
automatically — hence the manual catch-up mechanism.

## Rollups (precomputed aggregates)

Only **completed** periods are precomputed; `query.py` always computes the
current period live from the hot buffer. Bucket size depends on the entity's
aggregation type:

| Aggregation type | Fine bucket size | File | Serves |
| --- | --- | --- | --- |
| `counter` (counter, `total`/`total_increasing`) | 5 min | `tag.parquet` | week, month |
| `standard` / `switch` (measurement/switch) | 1 min | `stunde.parquet` | week, month |

Additionally per entity `monat.parquet` (serves year, for counters also decade)
and for standard/switch `jahr.parquet` (serves decade). Each row carries
`bucket_start` (Unix timestamp UTC) plus, depending on type,
`value`/`min_value`/`max_value` (counter: sum or bucket extremes; standard:
mean or extremes) or `on_seconds` (switch: on-time in the bucket).
`min_value`/`max_value` are computed from the bucket's actual raw values —
comparison tables with aggregation "Min"/"Max" therefore read the true
extremum, not the extremum of the bucket averages.

An aggregation type change of an entity (rare, e.g. when Home Assistant changes
a `device_class`) triggers `rollup.rebuild_entity_rollups()` — complete
recalculation from the archive, since the bucket sizes are not compatible.

**Additional hourly level for energy dashboard counter roles.** If a `counter`
entity is assigned as an energy dashboard role (`entities.hourly_rollup`, set/
cleared automatically when saving the energy dashboard configuration, see
`energiedashboard_routes.sync_hourly_rollup_flags()`),
`rollup.append_completed_month()` additionally continues `stunde.parquet`
**additively** besides `tag.parquet` — `tag.parquet` remains unchanged for
these entities, no existing read path (weekly/monthly bar chart, retention)
needs to know about it. Basis for the weekday-wise aggregation of the daily
load profile (energy dashboard) over monthly/yearly periods
(`query.query_hourly_counter_series()`). Newly flagged entities have their
already archived months rebuilt retroactively: a queue (setting
`energiedashboard_hourly_backfill_pending`) is worked off by the maintenance
scheduler with one entity per 30-second tick
(`energiedashboard_routes.process_pending_hourly_backfill()`, uses
`rollup.rebuild_entity_rollups(..., hourly_rollup=True)`).

## Soft delete and purge

Cleanup (marking outliers/gaps/duplicates/repeats) is **never destructive**:
`deleted_points` in SQLite stores one row per deleted *occurrence* (not per
unique timestamp — a duplicate with two identical timestamps can thus be
removed only once, deliberately).

- **View/query:** `filter_deleted_occurrences()` filters marked occurrences out
  of every display/calculation without touching the raw file.
- **Undo:** deleting from `deleted_points`, raw file remains unchanged — possible
  at any time as long as nothing has been purged.
- **Purge** (`cleanup.purge_hot_buffer()` / `purge_archived_months()`): removes
  the marked rows physically. For the current month a CSV rewrite; for already
  archived months a Parquet rewrite **plus** recalculation of the affected
  rollup rows (`rollup.replace_month()` / `remove_month()`). Afterwards the
  operation is final. Manually via **Housekeeping → Storage** (explicit
  confirmation), or automatically from an adjustable minimum age of the mark
  (`older_than` parameter, relating to `deleted_points.deleted_at` instead of
  the time of the data point itself — a safety window so that "Undo" can still
  bring back a batch that was just marked).
- **Compaction** and **retention** also remove marks, as a side effect of a
  resolution change or a month deletion instead of an explicit deletion — see
  below.

## Compaction (retroactive compaction of archived months)

Unlike resolution (acts only on newly arriving values) and rollups (read path,
does not change the archive): `cleanup.compact_raw_values()` actually rewrites
an already archived month with a coarser resolution — type-dependent (counter:
last value per bucket plus every detected counter reset as a raw row of its own;
standard: avg/min/max per bucket; switch excluded). Controlled via
`entities.compact_target` (default `off`), triggered manually (entity's editing
area) or automatically (`background._run_automatic_compaction_if_due()`,
Housekeeping → Compact, at most once a day). `compacted_months` prevents a
second compaction of the same month (standard entities: never again; counters:
only to a still coarser target).

Since the archive file is rewritten completely during compaction,
`compact_raw_values()` automatically also removes `deleted_points` marks of the
affected month — otherwise they would remain permanently as a "deletion mark
without matching raw data row" (the timestamps they point to no longer exist
after the recalculation). A one-time run at app start
(`remove_deleted_points_for_already_compacted_months()`) cleans up the same
inventory for months already compacted before this fix.

## Retention

Unlike purge (individual, previously marked values), retention affects whole,
**never marked** periods: values older than the period configured per entity are
deleted permanently when enforcement is enabled (`storage/retention.py`). Works
exclusively on **whole months** — an archived month is deleted completely
instead of being rewritten partially, so that rollup rows can be removed
consistently by their `bucket_start` without a risky Parquet rewrite. Entities
with retention `unlimited` are exempt from any automatic enforcement.

A deleted month may nevertheless have contained marked (but not yet purged)
rows — `enforce_retention_for_entity()` therefore removes the same
`deleted_points` marks as well
(`cleanup.remove_deleted_points_for_month()`, the same pattern as with
compaction above), both for completely deleted archive months and for rows
expired by retention in the current hot buffer month. Unlike with compaction,
there is no table that records which months were deleted by retention BEFORE
this fix — the one-time catch-up run at app start
(`cleanup.remove_deleted_points_with_no_matching_row()`) therefore checks
directly against reality (hot buffer + archive months still present, the same
reconciliation logic as `preview_purge()`) instead of against a month list —
and thereby also covers any other, still unknown cause of orphaned marks.

## SQLite schema (`index.sqlite`)

Migrations run additively at startup (`ALTER TABLE ... ADD COLUMN`, checked via
`PRAGMA table_info`) — there is no version/migration number system, every new
column gets a default and an explicit existence check in `Index.__init__()`.

| Table | Purpose |
| --- | --- |
| `entities` | One row per known entity: aggregation type, resolution, `compact_target` (compaction target, see above), retention, decimal places, value filter, outlier/gap thresholds, `first_ts`/`last_ts`/`last_value` (state for idempotency and filter checks), `row_count`/`size_bytes`/`deleted_count` (for the statistics, maintained incrementally instead of being recounted on every display or joined against `deleted_points`) |
| `deleted_points` | Soft-delete marks, see above. Indexed on `(entity_id, ts)` and `(entity_id, deleted_at)` |
| `compacted_months` | One entry per already compacted month (`entity_id, year, month` → `target_resolution`, `compacted_at`) — prevents a renewed compaction (see above) and is the basis of the deletion mark catch-up run at app start |
| `entity_actions` | Log of the Correct/Add/Clean up/Compact operations (Housekeeping → Activity): `entity_id` (nullable — cleanup/automatic compaction often affect several), `action`/`trigger`/`status`/`rows_affected`, `detail` as free JSON instead of one column per action type |
| `ingested_events` | Idempotency ledger of the write path (see [ingestion.md](ingestion.md)); entries older than 7 days are pruned periodically |
| `settings` | Generic key-value store: global resolution/retention defaults, log level, color scheme, API token, as well as **cached expensive previews** and **HA integration status** (see below) |
| `stats_snapshots`, `memory_snapshots` | Hourly snapshots for statistics history graphs |
| `backup_jobs`, `retention_jobs` | Persistent job history (status, errors, metrics) — survives restarts, unlike a mere "last run" timestamp. `backup_jobs` is also the basis for `Index.get_last_successful_backup_job()` — the `latest_backup` field in `/api/notices` (see [api-reference.md](api-reference.md)) |
| `saved_charts` | Saved chart **queries** (entities + period settings), no data snapshot — values are reloaded live on every call |
| `saved_tables`, `table_columns`, `table_rows` | Comparison tables: structure (rows = quantities, columns = periods) separate from `style_json` (purely visual presentation, see [frontend.md](frontend.md)) |
| `dashboards`, `dashboard_pins` | Several named dashboards (favorite, default, Precise mode, Fill gaps); one shared pin table for charts, tables, directly pinned entities ("value tiles") AND section dividers (`item_type`/`item_id`/`item_entity_id`; `item_type='section'` carries the section name in `title`, without its own `item_entity_id`), since all of them must be sorted together |

### Unique names (`dashboards`, `saved_charts`, `saved_tables`)

The three tables each hold unique names of at most `MAX_SAVED_NAME_LENGTH` (50)
characters. The check lives in `Index._ensure_valid_name_locked()` and runs
within the same transaction as the `INSERT`/`UPDATE` it protects — so not as a
UNIQUE constraint in the table: comparison is case-insensitive and without
leading/trailing spaces, using Python's `casefold()`. Without the ICU
extension, SQLite knows only ASCII folding and would therefore consider
"Küche" and "KÜCHE" different names.

Violations raise `DuplicateNameError` or `NameTooLongError` (both
`InvalidNameError`); a central exception handler in `main.py` translates them
into `409` or `400`. The duplicate routes avoid the collision themselves by
choosing, via `copy_name_for()`, the first free name of the series "(copy)",
"(copy 2)" … and shortening the original name as far as needed for the suffix to
still fit within the length limit.

### Cached previews (`settings` table)

Three compute-intensive previews (retention overview, duplicate overview,
cleanup preview) are **not** computed live but cached as a JSON blob with a
`generated_at` timestamp in `settings` and are refreshed by the maintenance
scheduler at most hourly (or forced immediately after an actual action such as
a purge click). Reason: a naive live computation on every page visit scales with
the number of marked rows × number of archive months and became a noticeable
load-time problem of the Settings page for large entities (>500k marked rows)
(fixed in 0.40.0, see `CHANGELOG.md`).

### HA integration status (`settings` table)

Two further JSON blobs, same basic pattern as above, maintained by
`app/ha_integration.py`:

- `ha_integration_info` — `{version, last_seen}` of the integration version last
  seen (from the request header `X-Zeitarchiv-Integration-Version`, see
  [api-reference.md](api-reference.md)). With an unchanged version, rewritten
  at most every 5 minutes (throttle against unnecessary writes with frequent
  `/api/write` batches), a version change, on the other hand, always
  immediately.
- `integration_version_check_cache` — `{checked_at, latest_version}`, the
  integration version published on GitHub, updated by the background scheduler
  at most daily (same pattern as `version_check_cache` for the app's own update
  check).

## Why no larger RDBMS / no time series DB?

A deliberate decision from the original concept: Parquet+zstd delivers better
compression than a row DB for append-then-read-only workloads, needs no running
database server process, and can be copied/backed up 1:1 as a file. SQLite
handles exclusively metadata and structured, small, frequently changed
objects — never the actual time series.
