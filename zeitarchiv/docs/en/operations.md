# Operations

*[Deutsche Version](../operations.md)*

## Backup and restore

Implementation: `storage/backup.py`.

**Included:** `index.sqlite`, `hot/`, `archive/`, `rollup/`. **Deliberately
excluded:** `symcon_import/`/`csv_import/` (temporary upload staging area,
potentially large, can be re-uploaded at any time) and `server.log` (pure
diagnostics). Parquet files are packed with `ZIP_STORED` instead of
`ZIP_DEFLATED` — they are already zstd-compressed, a second compression pass
only costs CPU time.

Every backup contains `zeitarchiv-manifest.json` (format identifier
`zeitarchiv-portable-backup`, format version, file list with size and SHA-256
per file). `validate_backup()` checks before every restore:

1. Manifest present and format identifier correct.
2. Every listed file exists in the ZIP with exactly matching size and SHA-256.
3. (In `create_backup`/on upload additionally:) ZIP structure, unpacked total
   size and compression ratio against the limits from `app/limits.py` (see
   [security.md](security.md)).

A restore is **prepared and rollback-capable**: the current inventory is moved
into a rollback directory (`.zeitarchiv-restore-rollback-*`) before being
overwritten, not deleted. If writing the restored inventory fails, it is rolled
back from this directory. Publishing a newly created backup itself is atomic
(write to a temporary file, then `rename()`).

Both backup and restore run under `StorageCoordinator.exclusive()` (see
[architecture.md](architecture.md)) — no concurrent write traffic
during the operation.

## Retention enforcement

`storage/retention.py`, executed manually (preview + click) or scheduled (daily
or weekly at the configured local time, `settings.retention_enforcement` +
`retention_enforcement_time`; in weekly mode additionally
`retention_enforcement_weekday`). Next/last run is logged in `retention_jobs`
(survives restarts). After downtime, at most **one** missed run is caught up,
never several retroactively.

## Maintenance scheduler

`background.py:BackgroundService._maintenance_scheduler_loop()`, a single
daemon thread, checked every 30 seconds. Bundles: statistics/RAM snapshots,
cache refresh (retention overview, duplicate overview, cleanup preview — see
[data-model.md](data-model.md)), scheduled backups, scheduled
retention, the automatic, retroactive compaction of archived months
(`_run_automatic_compaction_if_due()`, Housekeeping → Compact, off by default)
and the automatic cleanup of marked records
(`_run_automatic_purge_if_due()`, Housekeeping → Storage, off by default) —
both at most once a day, under the same `StorageCoordinator.exclusive()` lock
as the respective manual action — as well as — relevant only in demo mode or
with a leftover demo instance (see [User guide → Demo
mode](user-guide.md#demo-mode)) — `_refresh_demo_dir_info_if_stale()`
(occupied-space cache for the "unused" state) and `_run_demo_append_if_due()`
(due-based automatic supplementing of the demo data). Each of these steps runs
individually guarded (`_maintenance_step(name)`): if one fails, it is logged and
the remaining steps of the same pass still run — a permanently failing step
(e.g. the duplicate overview) thus blocks neither backup schedule nor
retention, compaction or automatic cleanup. The log is throttled: the first
failure of a step appears with a traceback
(`event=maintenance_step_failed step=<name>`), afterwards at most hourly a
short line with a counter (`event=maintenance_step_still_failing`), and on
return to normal operation once `event=maintenance_step_recovered`. An error
outside the steps (`event=maintenance_scheduler_failed`) likewise does not
abort the loop.

Backup, import, rotation and retention never access the inventory at the same
time because of `StorageCoordinator.exclusive()` — they wait for each other if
necessary, never in parallel.

## What is running right now

All twelve operations that take noticeable time have, since 0.85.0, registered
with the bell in the header (section "Running now", visible on every page). For
operations, the part nobody triggered is particularly interesting: the storage
reconciliation at startup, the hourly rollup backfill in the maintenance
scheduler and the rollup rebuild that a type change from Home Assistant
triggers in the middle of the write path. If the app seems to stand still for
no reason, the bell is the first place to look — several of these operations
hold the global maintenance lock and thus also pause ingestion.

If something stays there unusually long, the next places are the stall notices
(`system.scheduler_stalled`, `system.storage_reconcile_stalled`, from 5 minutes
without progress) and the log. Technical foundation:
[architecture.md](architecture.md).

## SQLite index maintenance

The index detail page shows, using `PRAGMA freelist_count`, only completely
free pages that SQLite reuses automatically during operation. "Optimization
recommended" appears conservatively from 50 MB index size, 10 MB reclaimable
storage and 25 % free pages.

The optimization, started manually only, runs `VACUUM` under
`StorageCoordinator.exclusive()` and the index lock — for its duration the
entire application including ingestion stands still, which is why it registers
with the bell like the other long operations (see above). Beforehand, twice the
current index size plus a 16 MB safety reserve must be free; afterwards
`PRAGMA quick_check` runs. There is deliberately neither a periodic run nor an
automatic execution when deleting measurements.

## Versioning

**Canonical version:** `addon/VERSION` (SemVer, one line). Everything else is
derived from it:

```bash
python3 scripts/sync_versions.py          # update addon/config.yaml
python3 scripts/sync_versions.py --check  # check for drift, exit code 1 on deviation
```

The integration (`custom_components/zeitarchiv`) versions itself independently
via its own `manifest.json` — `sync_versions.py` only checks there for valid
SemVer but does not align it with the app version (two separate products).

`CHANGELOG.md` follows the "Keep a Changelog" convention (New/Changed/Fixed per
version, newest on top).
