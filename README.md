# Home Assistant Apps by bertel2020

Dieses Repository stellt verschiedene Apps für Home Assistant bereit. Jede App
liegt in einem eigenen Ordner und kann unabhängig installiert und aktualisiert
werden.

## Verfügbare Apps

| App | Version | Beschreibung | Dokumentation |
| --- | --- | --- | --- |
| Zeitarchiv | 0.70.0 | Kompaktes Zeitreihen-Archiv mit Parquet, Ingress, Charts, direktem Home-Assistant-Import, detaillierter Speicheranalyse und kontrollierter Index-Optimierung. | [Anleitung](zeitarchiv/README.md) · [Dokumentation](zeitarchiv/docs/README.md) |

Weitere Apps können später als zusätzlicher Ordner im Repository-Stamm ergänzt
und in dieser Tabelle eingetragen werden.

## Repository in Home Assistant hinzufügen

1. In Home Assistant **Einstellungen → Apps → App-Store** öffnen.
2. Rechts oben das Menü öffnen und **Repositories** auswählen.
3. Folgende Adresse hinzufügen:

   ```text
   https://github.com/bertel2020/HA-Apps
   ```

4. Den App-Store neu laden.
5. Die gewünschte App auswählen und installieren.

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
