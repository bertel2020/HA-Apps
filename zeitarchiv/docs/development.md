# Lokal entwickeln

Im Verzeichnis `addon/`:

```bash
docker compose -f docker-compose.dev.yml up --build
```

Die Entwicklungsumgebung bindet Port `8127` ausschließlich an Loopback und
verwendet den Token `devtoken`. Ein Testevent lässt sich so senden:

```bash
curl -X POST http://127.0.0.1:8127/api/write \
  -H "Authorization: Bearer devtoken" \
  -H "Content-Type: application/json" \
  -d '{"events":[{"entity_id":"sensor.test","domain":"sensor","ts":1755000000,"value":21.4,"state_class":"measurement","unit":"°C"}]}'
```

Alternativ ohne Docker:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
ZEITARCHIV_DATA_DIR=/tmp/zeitarchiv-data \
ZEITARCHIV_API_TOKEN=devtoken \
  .venv/bin/uvicorn app.main:app --port 8127
```

## Tests

Die vollständige Testsuite wird aus dem Repository-Stamm gestartet (Details
zu Abdeckung und Aufbau: [testing.md](testing.md)):

```bash
python3 -m pytest -q
```

## Versionierung

Die kanonische Produktversion steht in `addon/VERSION`. Synchronisation und
Driftprüfung (Details: [operations.md](operations.md)):

```bash
python3 scripts/sync_versions.py
python3 scripts/sync_versions.py --check
```

## Demo-Daten

Für ein Datenverzeichnis mit realistisch aussehenden Beispieldaten (ohne
echte Home-Assistant-Anbindung) siehe [demo-data.md](demo-data.md).
