# Logging and ingest observability

*[Deutsche Version](../logging.md)*

Zeitarchiv writes application logs to `stdout`/`stderr`; Home Assistant
Supervisor takes care of persistent retention. In addition, the process keeps
a thread-safe ring buffer with at most 2,000 entries for the local live view.
The log page can switch between the two sources:

- **Live:** fast, without a Supervisor call, current process only and a
  limited buffer;
- **Supervisor history:** reaches further back, depending on Supervisor and its
  retention.

The live view refreshes every 15 seconds. Successful calls of `/api/logs`,
`/api/health` and the debug status do not themselves produce access log lines.
Errors remain visible.

## Log page: level, area and retention

The log page filters by **level** and by **area** (logger group). Both apply to the
display, the download and the Supervisor history, and combine with the search. The
mapping goes by logger name; anything unknown falls into “Other”, so a new logger stays
visible.

| Area | Loggers |
| --- | --- |
| AI access | `ai_routes`, `ai_settings_routes` |
| Ingest & API | `api_routes`, `storage.ingestion`, `storage.hotbuffer`, `trace` |
| Maintenance & backup | `background`, `housekeeping_routes`, `index_optimization`, `supervisor_stats`, `storage.backup_external` |
| Import & export | `import_routes`, `export_routes`, `storage.ha_import`, `storage.ha_statistics`, `storage.vm_import`, `storage.zip_guard` |
| Edit data | `entity_delete_routes`, `marked_values_routes` |
| HTTP requests | `http`, `access` |
| Other | everything else (`main`, `uvicorn`, `currency`, …) |

Backup and retention both log from `background` and therefore share an area with the
rest of the maintenance. Continuation lines (tracebacks) belong to the area of the line
before them.

Retention: Zeitarchiv does not write its own log file. The ring buffer keeps the last
2,000 entries in memory and is empty after a restart. Lines below the configured level
are not stored at all. The Supervisor history only shows the current container start
(roughly the last 2,000 to 8,000 lines, at most 8 MB). How long Supervisor keeps lines
is up to Home Assistant. Only the AI counters and the AI change log (see below) are
kept permanently, in the index rather than in the log.

## Recommended configuration

In normal operation: application level `warning`, HTTP logging **Failed
requests only**. `info` documents successfully completed system and user
actions; `debug` adds technical metrics and should be enabled only
selectively.

| Level | Meaning |
| --- | --- |
| `debug` | Batch counters, runtimes, recovery and pruning details |
| `info` | successfully completed action or job |
| `warning` | conspicuous, restricted or self-repairable situation |
| `error` | failed action with operation still under control |
| Exception with stack trace | unexpected error with operation context |

## Correlation and event codes

Every HTTP request receives a random request ID. It appears in the
`X-Request-ID` response header as well as in HTTP, ingest and entity trace
logs. Important messages have a stable `event=` code and, depending on the
operation, `request_id`, `job_id`, counters and `duration_ms`/`duration_s`.
This keeps the German messages readable and at the same time specifically
searchable.

Slow successful HTTP requests become visible as a warning from two seconds
on, even if the normal access log is switched off. Recurring warnings such as
auth failures, counter decreases or conspicuous ingest rates are throttled over
time; the next visible entry states the number of suppressed repetitions.

## Ingest

A normal `/api/write` batch produces exactly one summary at `debug`: number of
events, `written`, `skipped`, `filtered`, `duplicate`, `recovered`, runtime and
events per second. Small batches produce no rate warning. Conspicuously slow
batches, high duplicate rates and almost completely filtered batches are
reported, throttled, at `warning`.

The startup recovery logs open claims, age of the oldest claim, recovered
events, affected entities and pruning. Claims in state `processing` that are at
least five minutes old are warned about separately. The authoritative state
always remains the SQLite table `ingested_events`; the log is exclusively the
observation layer.

## AI access

All messages are `info`, so they are visible only at application level `info` or `debug`.

| Event | When |
| --- | --- |
| `ai_tool_call` | an AI tool ran (tool, status, number of entities, duration) |
| `ai_tool_rejected` | call rejected with `403` (`reason=ai_disabled` or `tool_disabled`); at most once per five minutes for each reason and tool, otherwise with `unterdrueckte_wiederholungen` |
| `ai_access_changed` | release saved (`enabled`, `scope`, number of entities, `exclude_presence`, number of tools switched off); never the notes text |

Usage counters (per day, 90 days) and the last 50 changes are also kept in the index
settings and appear under Settings → AI access → Usage.

## Redaction and diagnostic tools

Before any output, bearer tokens and typical secrets (`token`, `api_token`,
`password`, `secret`) are masked from text, JSON, headers and query strings.
This applies to app, HTTP, trace, Uvicorn and FastAPI output; when reading in
Supervisor lines, masking is applied again. Timestamps use ISO 8601 with
milliseconds and local time zone.

Entity IDs and measurements are not credentials but can represent sensitive
operational information:

- The **write capture** records exactly the next batch without the
  authorization header, expires after 60 minutes at the latest and is deleted
  even without another page visit. The download is marked with
  `Cache-Control: no-store`.
- The **entity trace** runs for at most 15 minutes and shows the incoming
  value, shortened event ID and final ingest result. It deliberately stays
  visible independently of the general log level.

Start both tools only selectively and remove downloaded capture files securely
after the diagnosis.
