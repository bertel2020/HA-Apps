# Write path (ingestion)

*[Deutsche Version](../ingestion.md)*

`storage/ingestion.py` — `IngestionService.ingest()` is the only way a value
becomes permanent. Both `/api/write` (integration) and import (Symcon/CSV)
ultimately pass through here.

## Idempotency

Every event carries an `event_id` (generated stably on the integration side,
survives retries of the same transmission). If it is missing (older integration
version), `legacy_event_id()` deterministically generates a SHA-256 hash from
the remaining fields.

Flow per event, under the **entity lock** (`StorageCoordinator.entity()`, see
[architecture.md](architecture.md)):

1. **Claim** in `ingested_events` (SQLite, `event_id` as primary key). If the
   claim already exists with status `done` → `duplicate`, return immediately.
2. An **existing, still open** claim (status `processing`, can only stem from a
   crash between file append and DB commit) triggers a targeted existence check
   in hot CSV/archive Parquet → if found, `recovered` (the value had already
   been written, only the DB completion was missing).
3. New claim: **timestamp duplicate check**. For the normal, monotonically
   increasing live path, a comparison with `entities.last_ts` is sufficient (no
   file I/O); only if the new timestamp is not beyond the index maximum is the
   file actually searched.
4. **Resolution filter**, type-dependent (see
   [data-model.md](data-model.md#resolution-write-throttle)): for
   counters and switches, `should_accept_write` throttles to a fixed time grid
   and discards values in between without replacement (switch resolution is
   locked to `raw` anyway, so it never throttles). Standard entities with
   resolution ≠ `raw`, on the other hand, let every value through and only
   afterwards condense it window by window into one avg/min/max row
   (`resolution.py`) — no `skipped` here.
5. **Value change filter** (`should_accept_value`) — skips subsequent values
   that are equal after rounding, but keeps a sign of life at least every six
   hours (prevents a chart from showing a gap for a sensor that has been
   unchanged for days).
6. **Counter decrease detection**: for `state_class = total_increasing`, a
   lower subsequent value is only **logged** (`logger.warning`, visible under
   "Counter decreases" in the cleanup), never blocked — a real counter reset
   (device replacement, restart) is a valid value.
7. **Rotation check** (`rotate.rotate_if_needed()`) — month change since this
   entity's last write?
8. **Append** to the hot CSV (`hotbuffer.append()`), claim set to `done`.

Return values (also the HTTP response of `/api/write` per event, aggregated as
counters): `written`, `duplicate`, `recovered`, `skipped` (resolution),
`filtered` (value change).

## Crash recovery

`IngestionService.recover_pending()` runs at app start: iterates all
`processing` claims (by definition at most those that were in progress at the
last crash) and completes them if the value is in fact already in the file. No
data loss, no duplication — the same mechanism as step 2 above, just once
instead of per event.

The idempotency table itself does not grow without limit: every 10,000
completed events (`_PRUNE_EVERY_COMPLETIONS`), entries older than 7 days
(`_IDEMPOTENCY_RETENTION_SECONDS`) are removed.

## Observability

The normal write path produces no log line per measurement. Instead, per
`/api/write` batch, a summary with request ID, result counters (`written`,
`duplicate`, `recovered`, `skipped`, `filtered`), runtime and throughput is
written at `debug`. Conspicuously slow batches as well as high duplicate or
filter rates become visible as a throttled warning only above sensible minimum
sizes.

At startup, the recovery reports number and age of open claims, recovered
events, affected entities and cleaned-up ledger entries. A `processing` claim
from five minutes of age produces a warning; open claims are never deleted by
the pruning of completed events.

The deliberately started entity trace supplements incoming data with the final
ingest result and a shortened event ID. Details on log levels, event codes and
data protection are in [logging.md](logging.md).

## New entity

`Index.get_or_create_entity()` creates a new row in `entities` as soon as the
Home Assistant integration sends a previously unknown entity ID. Resolution/
retention take over the global defaults from **Settings → Archiving** — later
changes to the defaults never affect entities already known, only newly added
ones.

If the aggregation type of a known entity changes (e.g. Home Assistant suddenly
delivers a different `state_class`), this triggers the same `on_type_change`
callback as a manual change: complete rollup recalculation
(`rollup.rebuild_entity_rollups()`), since the bucket sizes are not compatible
between the types (see [data-model.md](data-model.md)).

This is the only slow operation of the app that nobody triggered at all —
measured at a good five seconds in the middle of the write path, with the
entity lock held the whole time. Since 0.85.0 it therefore registers with the
bell (`_rebuild_after_type_change()` in `storage/ingestion.py`, registry in
`progress.py`, background in [architecture.md](architecture.md)); without
that it would be an inexplicable pause in ingestion.
