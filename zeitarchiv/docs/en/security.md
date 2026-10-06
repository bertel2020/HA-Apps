# Security

*[Deutsche Version](../security.md)*

## Network separation (nginx gateway)

See [architecture.md](architecture.md) for the diagram. Key point: the
separation between the full interface (Ingress, `:8099`) and the minimal public
API (`:8127`) is **exclusively** `nginx.conf` — no code in `app/main.py` checks
via which port a request arrived. On `:8127`, `location / { return 404; }`
answers at the gateway before the request even reaches the app; only
`/api/health` and `/api/write` are explicitly passed through there. `:8099`
additionally allows only the Supervisor Ingress IP (`172.30.32.2`) and
`127.0.0.1`.

**Consequence for changes:** a new endpoint in `main.py` that accidentally
returns sensitive data remains automatically blocked on `:8127` by the nginx
allowlist — BUT a refactoring that moves/renames `/api/health` or `/api/write`
must change `nginx.conf` in sync.

## Authentication

- A single, process-wide bearer token (`security.generate_api_token()`, 256 bits
  of entropy via `secrets.token_urlsafe`). Persisted in `settings.api_token`,
  generated once at first start (`ensure_api_token()`), viewable and
  regenerable under **Settings → Connection**.
- Comparison via `secrets.compare_digest` (timing-attack resistant), not `==`.
- Applies to `/api/write`, `/api/health` and `/api/notices` (the only routes
  that nginx lets through on `:8127` at all). The Ingress interface itself has
  **no** login of its own — authentication there is handled entirely by Home
  Assistant's Supervisor Ingress.
- Failed attempts are counted (`connection_stats.auth_failures`,
  `last_auth_failure_ts`) and shown under **Settings → Connection**. Repeated
  auth warnings are throttled; token or authorization header never appear in
  the log in the process. There is no lockout, only visibility.

## Logging and sensitive diagnostic data

A central redaction removes bearer token, `token`, `api_token`, `password` and
`secret` from text, JSON, header and query string representations before app or
third-party loggers write to `stdout`/`stderr`. When reading in the Supervisor
history, redaction is applied again as a second layer of protection.

The write capture deliberately contains raw entity IDs and measurements. It
captures exactly the next write batch, never the authorization header, expires
after 60 minutes at the latest and is deleted automatically even without
another UI call. Downloads deliver `Cache-Control: no-store`. The 15-minute
entity trace can make the same operational data visible in the log and should
be used only selectively for diagnosis. See [logging.md](logging.md).

## Path/symlink protection

`storage/paths.py` is the only place that translates entity IDs into file
paths:

- `validate_entity_id()` — format whitelist
  (`^[a-z][a-z0-9_]*\.[a-z0-9_]+$`), max. 240 characters. No `..`, no `/` can
  be a valid entity ID.
- `entity_dir()` / `storage_area_dir()` / `hot_file_path()` — every constructed
  path is resolved via `Path.resolve()` (which also follows symlinks) and
  checked that the result is `is_relative_to()` the respective storage area.
  This also catches a malicious symlink already present on disk, not just `../`
  sequences in the input string.

## Resource limits

`app/limits.py` defines hard upper bounds for write, query, export and import
operations, each checked **before** the actual processing:

| Limit | Value |
| --- | ---: |
| Events per write batch | 1,000 |
| Entities per multi-query | 25 |
| Points per raw value query | 100,000 |
| Rows per CSV export | 5,000,000 |
| Import rows per entity | 10,000,000 |
| ZIP upload | 2 GiB |
| CSV upload | 256 MiB |
| `settings.json` (Symcon) | 16 MiB |
| Unpacked ZIP total size | 5 GiB |
| ZIP members | 500,000,000 |
| Compression ratio | 200:1 |

## Import hardening (ZIP/CSV)

The ZIP/CSV/`settings.json` limits above are checked **before** full
unpacking/reading. The compression ratio limit is the actual zip-bomb defense:
an archive that claims to contain far more than 200 times its compressed size
is rejected before it is unpacked.

## Security headers and response behavior

Dynamic responses deliver restrictive headers (see the `main.py` middleware);
import paths are normalized. Backup file names are generated server-side, never
taken from user input (see [operations.md](operations.md)).

## Known limits (deliberate decisions, not gaps)

- **One token for everything.** No tenant separation, no token per integration
  setup. Sufficient for the target case (one Home Assistant system, one
  Zeitarchiv).
- **No rate limiting** on `/api/write` beyond the batch size limit — trusts the
  integration as the only realistic client behind the token.
- **No CSRF protection** on the Ingress forms — Ingress sessions are
  same-origin and run inside the already authenticated Home Assistant session;
  a classic CSRF scenario (a foreign origin sends a form) presupposes a valid
  Ingress session of its own, which the attacker does not have.
