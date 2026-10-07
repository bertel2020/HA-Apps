# API reference

*[Deutsche Version](../api-reference.md)*

Two audiences, two reachabilities (see [security.md](security.md)):

- **Public, port `8127`** (only for the Zeitarchiv integration): `/api/write`, `/api/health`, `/api/notices`.
- **Ingress only, port `8099`**: everything else, including `/api/query`,
  `/api/query-multi` and `/api/query-table` — these are NOT a public API but
  are called by the browser JS of the same Ingress session. No SemVer stability
  promise; they can change between versions.

All endpoints are in `app/api_routes.py`; see there for the implementation.

## `POST /api/write`

Authentication: `Authorization: Bearer <token>` (see
[security.md](security.md)).

```json
{
  "events": [
    {
      "event_id": "optional-stable-id",
      "entity_id": "sensor.aussentemperatur",
      "domain": "sensor",
      "ts": 1755000000,
      "value": 21.4,
      "state_class": "measurement",
      "unit": "°C",
      "friendly_name": "Außentemperatur"
    }
  ]
}
```

| Field | Required | Note |
| --- | --- | --- |
| `event_id` | no | 1–80 characters, `[A-Za-z0-9_-]+`. If missing, it is derived deterministically from the other fields (see [ingestion.md](ingestion.md)) |
| `entity_id` | yes | Home Assistant format, 3–240 characters, `^[a-z][a-z0-9_]*\.[a-z0-9_]+$` |
| `domain` | yes | Home Assistant domain (`sensor`, `binary_sensor`, …) |
| `ts` | yes | Unix timestamp (seconds, float) |
| `value` | yes | Numeric. Switches are transmitted as `0.0`/`1.0` |
| `state_class` | no | Among other things, `total_increasing` controls the counter semantics |
| `unit`, `friendly_name` | no | Display only, feed into no calculation |

At most `MAX_WRITE_EVENTS` (1,000) events per batch (`app/limits.py`).

**Response:** counters per result category, aggregated over the batch:

```json
{"written": 8, "skipped": 0, "filtered": 1, "duplicate": 0, "recovered": 0}
```

Categories and their meaning: [ingestion.md](ingestion.md).

## `GET /api/health`

Authenticated like `/api/write`. Response: `{"status": "ok", "version":
"<current app version>", "demo_mode": bool}` (e.g. `"0.60.0"`) — `version` is
read at runtime from the installed app, not hard-coded. Used by the integration
for the reauth flow (wrong token → 401).

`demo_mode` is deliberately included here — unlike the other fields — **also in
the 401 case** in the body (`detail` becomes `{"message": "...", "demo_mode":
bool}` instead of a plain string): the only endpoint an integration can still
reach with a token that has just been rejected, and thus the only place where
it can distinguish before a reauth whether the rejection is due to a real token
problem or to the instance currently running in demo mode (see
`custom_components/zeitarchiv/queue_writer.py::_probe_demo_mode()` there).

All three public endpoints additionally accept the optional header
`X-Zeitarchiv-Integration-Version` — the integration uses it to send along its
own version (`app/ha_integration.py`). Purely informational: the app shows it
in Settings → Connection and reports via notice when the integration is
outdated or a newer version would be available. No influence on auth or write
path; if it is missing, nothing changes.

**No enforced version dependency between app and integration, in either
direction.** `MIN_SUPPORTED_INTEGRATION_VERSION` (`app/ha_integration.py`) is a
purely informational minimum for the notice mentioned above, not a gate — auth
decides exclusively via the bearer token. Conversely, the integration reads the
`version` field from `/api/health` nowhere. `scripts/sync_versions.py` checks
the integration version (`manifest.json`) only for valid SemVer but does not
align it with the app version (see [operations.md](operations.md)). Every new
field on this page (`demo_mode` here and in `/api/notices` below,
`latest_backup`) is therefore deliberately coded so that an older integration
against a newer app (missing field → defined default/`None`) and a newer
integration against an older app (missing field → fallback to previous
behavior) work equally well, without either side having to know the other's
version.

## `GET /api/notices`

Authenticated like `/api/write`. Response: `{"notices": [...], "latest_backup":
{...} | null, "demo_mode": bool}`.

`notices` is the same filtered (mute-cleaned) notice list as in the bell icon of
the Zeitarchiv UI (see `notices.py`, `collect_notices()`). Each notice: `id`,
`severity` (`info`/`warn`/`error`), `title`, `detail`, `meta`, `link`,
`mutable`.

`latest_backup` describes the last **successful** backup — `filename`,
`size_bytes`, `finished_at` (Unix timestamp) — or `null` if none has succeeded
yet (see `notices.latest_backup_info()`,
`Index.get_last_successful_backup_job()`). Deliberately only the last
successful run, not the last run at all: a failed or still running job has no
real, copyable file.

`demo_mode` mirrors the add-on option of the same name (see [User guide → Demo
mode](user-guide.md#demo-mode)) — `true` as long as this app instance runs with
synthetic showcase/test data instead of the real archive data. If the field is
missing (older app version before 0.91.0), consumers assume `false`.

Basis for the HA integration: it polls this endpoint (60 s) and turns `notices`
into Home Assistant Repairs (critical cases) as well as `binary_sensor` entities
(automatable persistent states) on the Zeitarchiv device, `latest_backup` into
the sensor entity "Latest backup" — basis for an automation blueprint that
triggers an offsite target for the backup file (Zeitarchiv itself does not
speak S3/WebDAV/SMB) — and `demo_mode` into the sensor entity "Operating mode".

## `GET /api/query` (Ingress-internal)

Single entity, for the entity's own chart page.

| Parameter | Default | Meaning |
| --- | --- | --- |
| `entity_id` | — | Required |
| `range` | `day` | `hour`\|`day`\|`week`\|`month`\|`year`\|`decade` |
| `offset` | `0` | `0` = current period, `-1` = previous, never positive |
| `continuous` | `false` | `true` = rolling window (e.g. "last 24 h" instead of "today calendar day") |
| `compare` | `false` | Additionally `compare_points` for previous period/previous year |
| `compare_mode` | `previous` | `previous`\|`year` — `year` remains valid for every period; the interface does not offer it for `year`/`decade` (duplicate or overlapping there) |
| `raw` | `false` | Raw values instead of bucket aggregation (`query_raw_series`, limited to `MAX_RAW_QUERY_POINTS`) |
| `chart_type` | `null` | `line`\|`bar`, overrides the automatic choice |

**Response** (core fields): `points` (`[{ts, value, min, max}]` — `min`/`max`
only for aggregated buckets, from the bucket's actual raw values, see
[data-model.md](data-model.md)), `window_start`/`window_end`,
`period_end`, `is_current`, `aggregation_type`, `chart_type`.

## `GET /api/query-multi` (Ingress-internal)

Like `/api/query`, but `entity_ids` (comma-separated, max.
`MAX_MULTI_QUERY_ENTITIES` = 25) instead of `entity_id`, additionally
`year_over_year` (bool). Used by multi-entity charts. Response:
`{series: [...], window_start, window_end, period_end,
is_current}`, one entry in `series` per entity with `friendly_name`, `unit`,
`decimals`, `display_mode`, `aggregation_type`, `chart_type`, `points`.

## `POST /api/query-table` (Ingress-internal)

Loads all periods of a comparison table in one shared request (at most 25
entities and 100 columns). Example:

```json
{
  "entity_ids": ["sensor.ertrag", "sensor.verbrauch"],
  "columns": [
    {"range_key": "day", "offset": 0, "year_over_year": false},
    {"range_key": "day", "offset": -1, "year_over_year": false, "same_elapsed": true}
  ]
}
```

The response contains, per column, the resolved time window and, per entity,
the metadata as well as `aggregates` with `auto`, `avg`, `min`, `max` and
`sum`. Complete point series are not transmitted. A request-local read cache
reuses source files across all columns; groups and formula rows are then
computed by `table-compute.js` in the browser.

`same_elapsed` (bool, default `false`) trims, for a past column (offset < 0),
its time window to the same elapsed duration as a simultaneously queried column
of the same period type with offset 0 — the "same point in time" comparison
(e.g. "previous day until 2 pm" instead of the whole previous day, while the
current day is still running). `table-compute.js` sets the flag automatically,
`table_editor.html` needs no option of its own for it.

## Error formats

FastAPI standard: `4xx`/`5xx` with `{"detail": "..."}`. Auth errors always
`401`. Validation errors (Pydantic) `422`. Batches/queries that are too large
`413`. When saving dashboards, charts and tables: name already taken `409`,
name too long `400` (see
[data-model.md](data-model.md#unique-names-dashboards-saved_charts-saved_tables)).
The `detail` message is in both cases worded for direct display in the
interface.

### Language of the error texts

The texts (`detail` as well as title and description of the notices from
`/api/notices`) come in the language of the request:

1. If the setting `language` is fixed to German or English, it applies.
2. With "Automatic" (the default) the `Accept-Language` header decides (`en`,
   `en-GB`, `de-DE` …).
3. Without a matching header — that is, for scripts that send none — the app
   answers in **German**. Anyone who wants English texts sends
   `Accept-Language: en`.

The integration sends the language of Home Assistant along, so that repair
notices and sensor attributes appear in its language. Errors generated by
FastAPI itself are excepted: validation errors (`422`, a list instead of text),
"Not Found" and "Method Not Allowed" are always English. For evaluation in
scripts the HTTP status is authoritative, not the text.
