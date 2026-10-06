# Developing locally

*[Deutsche Version](../development.md)*

In the `addon/` directory:

```bash
docker compose -f docker-compose.dev.yml up --build
```

The development environment binds port `8127` to loopback only and uses the
token `devtoken`. A test event can be sent like this:

```bash
curl -X POST http://127.0.0.1:8127/api/write \
  -H "Authorization: Bearer devtoken" \
  -H "Content-Type: application/json" \
  -d '{"events":[{"entity_id":"sensor.test","domain":"sensor","ts":1755000000,"value":21.4,"state_class":"measurement","unit":"°C"}]}'
```

Alternatively without Docker:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
ZEITARCHIV_DATA_DIR=/tmp/zeitarchiv-data \
ZEITARCHIV_API_TOKEN=devtoken \
  .venv/bin/uvicorn app.main:app --port 8127
```

## Tests

The complete test suite is started from the repository root (details on
coverage and structure: [testing.md](testing.md)):

```bash
python3 -m pytest -q
```

## Versioning

The canonical product version is in `addon/VERSION`. Synchronization and drift
check (details: [operations.md](operations.md)):

```bash
python3 scripts/sync_versions.py
python3 scripts/sync_versions.py --check
```

## Release

1. Raise `addon/VERSION`, run `python3 scripts/sync_versions.py`, add to
   `CHANGELOG.md`.
2. Synchronize to `HA-Apps/zeitarchiv/` — copy changed files, tests via
   `python3 scripts/sync_tests.py` (check run: `--check`). The version table in
   `HA-Apps/README.md` also names the app version and is not touched by any
   script. Then commit and push.
3. The "Zeitarchiv Image" workflow (`.github/workflows/zeitarchiv-image.yml` in
   the HA-Apps repo) then builds the images for `amd64` and `aarch64` via
   buildx/QEMU and publishes them as
   `ghcr.io/bertel2020/zeitarchiv-{arch}:<VERSION>` (additionally `latest`).
   The Supervisor pulls them via `image:` in `config.yaml`. A few minutes lie
   between push and green run; anyone who updates in this window gets a pull
   error and can simply try again afterwards.

## Demo data

For a data directory with realistic-looking sample data (without a real Home
Assistant connection), see [demo-data.md](../demo-data.md) (German).
