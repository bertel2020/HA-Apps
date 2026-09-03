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
- Gilt für `/api/write`, `/api/health` und `/api/notices` (die einzigen
  Routen, die nginx auf `:8127` überhaupt durchlässt). Die Ingress-Oberfläche
  selbst hat **kein**
  eigenes Login — Authentifizierung übernimmt dort vollständig Home
  Assistants Supervisor-Ingress.
- Fehlversuche werden gezählt (`connection_stats.auth_failures`,
  `last_auth_failure_ts`) und unter **Einstellungen → Verbindung**
  angezeigt. Wiederholte Auth-Warnungen werden gedrosselt; Token oder
  Authorization-Header erscheinen dabei nie im Log. Es gibt keinen Lockout,
  nur Sichtbarkeit.

## Logging und sensible Diagnosedaten

Eine zentrale Redaction entfernt Bearer-Token, `token`, `api_token`,
`password` und `secret` aus Text-, JSON-, Header- und Querystring-Darstellungen,
bevor App- oder Fremdlogger nach `stdout`/`stderr` schreiben. Beim Einlesen der
Supervisor-Historie wird als zweite Schutzschicht erneut redigiert.

Der Write-Capture enthält bewusst rohe Entity-IDs und Messwerte. Er erfasst
genau den nächsten Schreibbatch, niemals den Authorization-Header, läuft nach
spätestens 60 Minuten ab und wird auch ohne weiteren UI-Aufruf automatisch
gelöscht. Downloads liefern `Cache-Control: no-store`. Der 15-minütige
Entity-Trace kann dieselben Betriebsdaten im Log sichtbar machen und sollte
nur gezielt zur Diagnose verwendet werden. Siehe [logging.md](logging.md).

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

## Ressourcenlimits

`app/limits.py` definiert harte Obergrenzen für Schreib-, Abfrage-, Export-
und Import-Operationen, jeweils geprüft **vor** der eigentlichen
Verarbeitung:

| Grenze | Wert |
| --- | ---: |
| Events je Schreibbatch | 1.000 |
| Entitäten je Multi-Abfrage | 25 |
| Punkte je Rohwertabfrage | 100.000 |
| Zeilen je CSV-Export | 5.000.000 |
| Importzeilen je Entität | 10.000.000 |
| ZIP-Upload | 2 GiB |
| CSV-Upload | 256 MiB |
| `settings.json` (Symcon) | 16 MiB |
| Entpackte ZIP-Gesamtgröße | 5 GiB |
| ZIP-Mitglieder | 500.000.000 |
| Kompressionsverhältnis | 200:1 |

## Import-Härtung (ZIP/CSV)

Die ZIP-/CSV-/`settings.json`-Grenzen oben werden **vor** vollständigem
Entpacken/Einlesen geprüft. Das Kompressionsverhältnis-Limit ist die
eigentliche Zip-Bomb-Verteidigung: ein Archiv, das behauptet, weit mehr als
das 200-fache seiner komprimierten Größe zu enthalten, wird abgelehnt, bevor
es entpackt wird.

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
