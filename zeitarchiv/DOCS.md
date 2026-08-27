# Zeitarchiv

Zeitarchiv archiviert ausgewählte Home-Assistant-Zustände langfristig und
platzsparend als Parquet-Dateien. Die separat zu installierende
Zeitarchiv-Integration sendet die Zustandsänderungen an die App; Ingress
stellt Archiv, Charts, Tabellen, Import/Export, Bereinigung, Aufbewahrung und
Backup / Restore in der Home-Assistant-Oberfläche bereit.

## Einrichtung

1. App installieren und starten.
2. Zeitarchiv über die Home-Assistant-Seitenleiste öffnen.
3. Unter **Einstellungen → Verbindung** den beim ersten Start automatisch
   erzeugten API-Token kopieren.
4. Die Zeitarchiv-Integration installieren (über HACS oder manuell von
   [github.com/bertel2020/HA-Zeitarchiv](https://github.com/bertel2020/HA-Zeitarchiv))
   und mit Host `localhost`, Port `8127` und diesem Token einrichten.
5. In den Optionen der Integration die zu archivierenden Entitäten auswählen.

Die vollständige Oberfläche ist nur über den authentifizierten
Home-Assistant-Ingress erreichbar. Der veröffentlichte Port `8127` dient der
Integration und nimmt ausschließlich tokenpflichtige Health- und
Schreibzugriffe an.

## Konfiguration

Die App-Option `timezone` legt die IANA-Zeitzone für Navigation, Zeitpläne und
Darstellung fest; Standard ist `Europe/Berlin`. Weitere Einstellungen werden
direkt in der App verwaltet und im App-Datenverzeichnis dauerhaft gespeichert.

Vor Updates oder umfangreichen Importen empfiehlt sich ein Backup unter
**System → Backup / Restore**.

Ausgeführte Symcon- und CSV-Importe werden unter **Import → Reports** dauerhaft
protokolliert. Dort lassen sich Ergebnisse filtern, im Detail prüfen und als
JSON herunterladen. Importvorschauen erzeugen keinen Report.

Ausführliche Funktions- und Grenzwerthinweise stehen in der mitgelieferten
`README.md`.
