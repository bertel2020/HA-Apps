# API-Referenz

Zwei Zielgruppen, zwei Erreichbarkeiten (siehe [security.md](security.md)):

- **Öffentlich, Port `8127`** (nur für die Zeitarchiv-Integration): `/api/write`, `/api/health`.
- **Nur Ingress, Port `8099`**: alles andere, inklusive `/api/query`,
  `/api/query-multi` und `/api/query-table` — diese sind KEINE öffentliche
  API, sondern werden vom Browser-JS derselben Ingress-Session aufgerufen.
  Kein SemVer-Stabilitätsversprechen; sie können sich zwischen Versionen
  ändern.

Alle Endpunkte in `app/api_routes.py`; Implementierung siehe dort.

## `POST /api/write`

Authentifizierung: `Authorization: Bearer <token>` (siehe
[security.md](security.md)).

```json
{
  "events": [
    {
      "event_id": "optional-stabile-id",
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

| Feld | Pflicht | Hinweis |
| --- | --- | --- |
| `event_id` | nein | 1–80 Zeichen, `[A-Za-z0-9_-]+`. Fehlt sie, wird sie deterministisch aus den übrigen Feldern abgeleitet (siehe [ingestion.md](ingestion.md)) |
| `entity_id` | ja | Home-Assistant-Format, 3–240 Zeichen, `^[a-z][a-z0-9_]*\.[a-z0-9_]+$` |
| `domain` | ja | Home-Assistant-Domain (`sensor`, `binary_sensor`, …) |
| `ts` | ja | Unix-Timestamp (Sekunden, float) |
| `value` | ja | Numerisch. Schalter werden als `0.0`/`1.0` übertragen |
| `state_class` | nein | u. a. `total_increasing` steuert die Zähler-Semantik |
| `unit`, `friendly_name` | nein | Nur Anzeige, fließen in keine Berechnung ein |

Maximal `MAX_WRITE_EVENTS` (1.000) Events pro Batch (`app/limits.py`).

**Antwort:** Zähler je Ergebnis-Kategorie, aggregiert über den Batch:

```json
{"written": 8, "skipped": 0, "filtered": 1, "duplicate": 0, "recovered": 0}
```

Kategorien und ihre Bedeutung: [ingestion.md](ingestion.md).

## `GET /api/health`

Authentifiziert wie `/api/write`. Antwort: `{"status": "ok", "version": "<aktuelle App-Version>"}`
(z. B. `"0.60.0"`) — `version` wird zur Laufzeit aus der installierten App
gelesen, nicht fest kodiert.
Von der Integration für den Reauth-Fluss genutzt (falscher Token → 401).

## `GET /api/query` (Ingress-intern)

Einzelentität, für die Entitäts-eigene Chart-Seite.

| Parameter | Default | Bedeutung |
| --- | --- | --- |
| `entity_id` | — | Pflicht |
| `range` | `day` | `hour`\|`day`\|`week`\|`month`\|`year`\|`decade` |
| `offset` | `0` | `0` = aktuelle Periode, `-1` = vorherige, nie positiv |
| `continuous` | `false` | `true` = rollierendes Fenster (z. B. "letzte 24 h" statt "heute Kalendertag") |
| `compare` | `false` | Zusätzlich `compare_points` für Vorperiode/Vorjahr |
| `compare_mode` | `previous` | `previous`\|`year` |
| `raw` | `false` | Rohwerte statt Bucket-Aggregation (`query_raw_series`, begrenzt auf `MAX_RAW_QUERY_POINTS`) |
| `chart_type` | `null` | `line`\|`bar`, überschreibt die automatische Wahl |

**Antwort** (Kernfelder): `points` (`[{ts, value, min, max}]` — `min`/`max`
nur bei aggregierten Buckets, aus den tatsächlichen Rohwerten des Buckets,
siehe [data-model.md](data-model.md)), `window_start`/`window_end`,
`period_end`, `is_current`, `aggregation_type`, `chart_type`.

## `GET /api/query-multi` (Ingress-intern)

Wie `/api/query`, aber `entity_ids` (kommagetrennt, max.
`MAX_MULTI_QUERY_ENTITIES` = 25) statt `entity_id`, zusätzlich `year_over_year`
(bool). Von Multi-Entitäts-Charts genutzt. Antwort:
`{series: [...], window_start, window_end, period_end,
is_current}`, ein Eintrag in `series` je Entität mit `friendly_name`, `unit`,
`decimals`, `display_mode`, `aggregation_type`, `chart_type`, `points`.

## `POST /api/query-table` (Ingress-intern)

Lädt alle Zeiträume einer Vergleichstabelle in einer gemeinsamen Anfrage
(maximal 25 Entitäten und 100 Spalten). Beispiel:

```json
{
  "entity_ids": ["sensor.ertrag", "sensor.verbrauch"],
  "columns": [
    {"range_key": "day", "offset": 0, "year_over_year": false},
    {"range_key": "day", "offset": -1, "year_over_year": false}
  ]
}
```

Die Antwort enthält je Spalte das aufgelöste Zeitfenster und je Entität die
Metadaten sowie `aggregates` mit `auto`, `avg`, `min`, `max` und `sum`.
Vollständige Punktreihen werden nicht übertragen. Ein request-lokaler
Lese-Cache verwendet Quelldateien über alle Spalten wieder; Gruppen und
Formelzeilen berechnet anschließend `table-compute.js` im Browser.

## Fehlerformate

FastAPI-Standard: `4xx`/`5xx` mit `{"detail": "..."}`. Auth-Fehler immer
`401`. Validierungsfehler (Pydantic) `422`. Zu große Batches/Abfragen `413`.
Beim Speichern von Dashboards, Charts und Tabellen: bereits vergebener Name
`409`, zu langer Name `400` (siehe [data-model.md](data-model.md#eindeutige-namen-dashboards-saved_charts-saved_tables)).
Die `detail`-Meldung ist in beiden Fällen für die direkte Anzeige in der
Oberfläche formuliert.
