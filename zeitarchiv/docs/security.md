# Sicherheit

## Netzwerktrennung (nginx-Gateway)

Siehe [architecture.md](architecture.md) für das Diagramm. Kernpunkt: die
Trennung zwischen voller Oberfläche (Ingress, `:8099`) und minimaler
öffentlicher API (`:8127`) ist **ausschließlich** `nginx.conf` — kein Code in
`app/main.py` prüft, über welchen Port eine Anfrage hereinkam. Auf `:8127`
antwortet `location / { return 404; }` am Gateway, bevor die Anfrage die App
überhaupt erreicht; nur `/api/health` und `/api/write` sind dort explizit
durchgereicht. `:8099` erlaubt zusätzlich nur die Supervisor-Ingress-IP
(`172.30.32.2`) und `127.0.0.1`.

**Konsequenz für Änderungen:** ein neuer Endpunkt in `main.py`, der aus
Versehen sensible Daten liefert, ist auf `:8127` automatisch weiterhin durch
die nginx-Allowlist blockiert — ABER ein Refactoring, das `/api/health` oder
`/api/write` verschiebt/umbenennt, muss `nginx.conf` synchron mitändern.

## Authentifizierung

- Ein einziger, prozessweiter Bearer-Token (`security.generate_api_token()`,
  256 Bit Entropie via `secrets.token_urlsafe`). Persistiert in
  `settings.api_token`, einmalig beim ersten Start erzeugt
  (`ensure_api_token()`), über **Einstellungen → Verbindung** einsehbar und
  neu generierbar.
- Vergleich über `secrets.compare_digest` (Timing-Angriff-resistent), nicht
  `==`.
- Gilt für `/api/write` und `/api/health` (die einzigen Routen, die nginx auf
  `:8127` überhaupt durchlässt). Die Ingress-Oberfläche selbst hat **kein**
  eigenes Login — Authentifizierung übernimmt dort vollständig Home
  Assistants Supervisor-Ingress.
- Fehlversuche werden gezählt (`connection_stats.auth_failures`,
  `last_auth_failure_ts`) und unter **Einstellungen → Verbindung**
  angezeigt — kein Lockout, nur Sichtbarkeit.

## Pfad-/Symlink-Schutz

`storage/paths.py` ist die einzige Stelle, die Entity-IDs in Dateipfade
übersetzt:

- `validate_entity_id()` — Format-Whitelist
  (`^[a-z][a-z0-9_]*\.[a-z0-9_]+$`), max. 240 Zeichen. Kein `..`, kein `/`
  kann eine gültige Entity-ID sein.
- `entity_dir()` / `storage_area_dir()` / `hot_file_path()` — jeder
  konstruierte Pfad wird per `Path.resolve()` aufgelöst (folgt auch
  Symlinks) und geprüft, dass das Ergebnis `is_relative_to()` des
  jeweiligen Storage-Bereichs ist. Das fängt auch einen bereits auf
  Platte vorhandenen bösartigen Symlink ab, nicht nur `../`-Sequenzen im
  Eingabestring.

## Import-Härtung (ZIP/CSV)

`app/limits.py` definiert harte Obergrenzen, geprüft **vor** vollständigem
Entpacken/Einlesen:

| Grenze | Wert |
| --- | --- |
| ZIP-Upload | 2 GiB |
| CSV-Upload | 256 MiB |
| `settings.json` (Symcon) | 16 MiB |
| Entpackte ZIP-Gesamtgröße | 5 GiB |
| ZIP-Mitglieder | 500.000.000 |
| Kompressionsverhältnis | 200:1 |

Das Kompressionsverhältnis-Limit ist die eigentliche Zip-Bomb-Verteidigung:
ein Archiv, das behauptet, weit mehr als das 200-fache seiner komprimierten
Größe zu enthalten, wird abgelehnt, bevor es entpackt wird.

## Sicherheitsheader und Antwortverhalten

Dynamische Antworten liefern restriktive Header (siehe `main.py`-Middleware);
Importpfade werden normalisiert. Backup-Dateinamen werden serverseitig
generiert, nie aus Nutzereingaben übernommen (siehe [operations.md](operations.md)).

## Bekannte Grenzen (bewusste Entscheidungen, keine Lücken)

- **Ein Token für alles.** Keine Mandantentrennung, kein Token pro
  Integrationseinrichtung. Für den Zielfall (ein Home-Assistant-System, ein
  Zeitarchiv) ausreichend.
- **Kein Rate-Limiting** auf `/api/write` über die Batch-Größengrenze hinaus
  — vertraut der Integration als einzigem realistischen Client hinter dem
  Token.
- **Kein CSRF-Schutz** auf den Ingress-Formularen — Ingress-Sessions sind
  same-origin und laufen innerhalb der bereits authentifizierten
  Home-Assistant-Session; ein klassisches CSRF-Szenario (fremde Origin sendet
  Formular) setzt eine eigene, gültige Ingress-Session voraus, die der
  Angreifer nicht hat.
