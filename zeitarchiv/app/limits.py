"""Zentrale Ressourcenlimits für öffentliche und Ingress-Eingaben."""

MAX_WRITE_EVENTS = 1_000

# Grenzen für ein einzelnes Ingest-Event (EventIn in api_routes.py).
#
# Ein Zeitstempel außerhalb dieses Fensters ist kein Messwert, sondern ein
# Fehler an der Quelle: Millisekunden statt Sekunden (2026 wären rund
# 1,79e12, also weit über der Obergrenze), ein Rechenfehler oder ein leeres
# Feld, das als 0 durchgereicht wurde. /api/write ist der Live-Weg aus Home
# Assistant — Historie kommt über die Import-Assistenten, die eigene Regeln
# haben. Deshalb sind die Grenzen hier bewusst eng.
MIN_EVENT_TS = 946_684_800.0    # 2000-01-01T00:00:00Z
MAX_EVENT_TS = 4_102_444_800.0  # 2100-01-01T00:00:00Z
# domain/state_class/unit/friendly_name. Eine gemeinsame, großzügige Grenze
# statt vier fachlich hergeleiteter: es geht darum, dass Index und Logs nicht
# durch ein 100.000 Zeichen langes Feld aufgebläht werden, nicht darum, HAs
# Vokabular nachzubilden.
MAX_EVENT_TEXT_LENGTH = 255
MAX_MULTI_QUERY_ENTITIES = 25
MAX_RAW_QUERY_POINTS = 100_000
MAX_UI_ANALYSIS_ROWS = 500_000
MAX_EXPORT_ROWS = 5_000_000
MAX_IMPORT_ROWS_PER_ENTITY = 10_000_000

MAX_ZIP_UPLOAD_BYTES = 2 * 1024 * 1024 * 1024
MAX_CSV_UPLOAD_BYTES = 256 * 1024 * 1024
MAX_SETTINGS_UPLOAD_BYTES = 16 * 1024 * 1024
MAX_ZIP_MEMBERS = 500_000_000
MAX_ZIP_UNCOMPRESSED_BYTES = 5 * 1024 * 1024 * 1024
MAX_ZIP_COMPRESSION_RATIO = 200
