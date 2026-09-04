# Home Assistant Apps by bertel2020

Dieses Repository stellt verschiedene Apps für Home Assistant bereit. Jede App
liegt in einem eigenen Ordner und kann unabhängig installiert und aktualisiert
werden.

## Verfügbare Apps

| App | Version | Beschreibung | Dokumentation |
| --- | --- | --- | --- |
| Zeitarchiv | 0.81.1 | Kompaktes Zeitreihen-Archiv mit Parquet, Ingress, Energie-Dashboard, Charts, Importen sowie sicherer Logging- und Ingest-Diagnose. | [Anleitung](zeitarchiv/README.md) · [Dokumentation](zeitarchiv/docs/README.md) |

Weitere Apps können später als zusätzlicher Ordner im Repository-Stamm ergänzt
und in dieser Tabelle eingetragen werden.

## Repository in Home Assistant hinzufügen

[![Add-on-Repository zu My Home Assistant hinzufügen](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fbertel2020%2FHA-Apps)

Alternativ von Hand:

1. In Home Assistant **Einstellungen → Apps → App-Store** öffnen.
2. Rechts oben das Menü öffnen und **Repositories** auswählen.
3. Folgende Adresse hinzufügen:

   ```text
   https://github.com/bertel2020/HA-Apps
   ```

4. Den App-Store neu laden.
5. Die gewünschte App auswählen und installieren.

Die Apps werden als vorgebaute Images für `amd64` und `aarch64` auf
`ghcr.io/bertel2020` veröffentlicht; der Home-Assistant-Host muss beim
Installieren oder Aktualisieren nichts selbst bauen.

## Repository-Struktur

```text
HA-Apps/
├── repository.yaml
├── README.md
├── zeitarchiv/
│   ├── config.yaml
│   ├── Dockerfile
│   ├── README.md
│   ├── docs/
│   └── ...
└── weitere-app/
    ├── config.yaml
    ├── Dockerfile
    └── ...
```

## Fehler melden

Fehler und Verbesserungsvorschläge bitte über die
[GitHub Issues](https://github.com/bertel2020/HA-Apps/issues) melden.

## Lizenz

[MIT](LICENSE)
