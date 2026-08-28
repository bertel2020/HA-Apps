<p align="center">
  <img src="https://raw.githubusercontent.com/bertel2020/HA-Apps/main/zeitarchiv/logo.png" alt="Zeitarchiv" width="160">
</p>

<h1 align="center">Zeitarchiv App</h1>

<p align="center">
  Langfristige, kompakte Zeitreihen für Home Assistant.<br>
  <sub>PARQUET + ZSTD · INGRESS · CHARTS · TABELLEN · IMPORT · BACKUP</sub>
</p>

Zeitarchiv bewahrt ausgewählte Zustandsänderungen unabhängig von der
Aufbewahrungsdauer des Home-Assistant-Recorders auf. Die
[Zeitarchiv-Integration](https://github.com/bertel2020/HA-Zeitarchiv) sammelt
die gewünschten Werte; diese App speichert, verdichtet, durchsucht und
visualisiert sie.

Ausführliches, aufgabenorientiertes Benutzerhandbuch:
[docs/user-guide.md](docs/user-guide.md). Technische Dokumentation für
Entwickler: [docs/](docs/README.md).

## Was Zeitarchiv ist

Der Home-Assistant-Recorder ist auf kurze Aufbewahrung und den laufenden
Betrieb ausgelegt — für Auswertungen über Monate oder Jahre reicht das nicht.
Zeitarchiv schließt diese Lücke: Es übernimmt ausgewählte Zustandsänderungen
von Home Assistant, verdichtet abgeschlossene Monate spaltenorientiert und
verlustfrei, und stellt daraus schnelle Langzeitauswertungen bereit — als
eigene, in die Home-Assistant-Oberfläche eingebettete Anwendung, nicht als
weitere Lovelace-Karte.

Zeitarchiv besteht aus zwei getrennten, unabhängig versionierten Teilen:

- Die **[Zeitarchiv-Integration](https://github.com/bertel2020/HA-Zeitarchiv)**
  läuft in Home Assistant selbst und entscheidet, welche Entitäten
  archiviert werden.
- Diese **App** läuft als eigenständiges Add-on, empfängt die Werte über
  eine token-gesicherte API, speichert sie dauerhaft und stellt Archiv,
  Auswertung und Datenpflege über den Ingress bereit.

## Auf einen Blick

| | |
| --- | --- |
| **Kompaktes Archiv** | Abgeschlossene Monate als Parquet mit zstd-Kompression, unabhängig von der Recorder-Aufbewahrung |
| **Schnelle Langzeitansichten** | Vorbereitete Rollups von Stunde bis Jahr statt teurer Live-Aggregation |
| **Eigene Dashboards** | Frei kombinierbare Charts und Vergleichstabellen auf beliebig vielen Dashboards |
| **Datenpflege** | Ausreißer, Lücken und Duplikate erkennen, korrigieren oder bereinigen |
| **Datenübernahme** | Bestehende Historie aus Symcon, CSV-Dateien oder direkt aus Home Assistant importieren |
| **Sicherung** | Prüfbare, portable ZIP-Backups mit Wiederherstellung und Zeitplan |
| **Sicherheit** | Ingress-Authentifizierung, tokenpflichtige API, strikt getrennter Schreibzugang |

## Zusammenspiel

```text
Home-Assistant-Entitäten
          │ state_changed
          ▼
Zeitarchiv-Integration
  Filter · Queue · Batch · Retry
          │ POST /api/write + Bearer-Token
          ▼
Zeitarchiv App
  Hot Buffer ──► Monatsarchiv ──► Rollups
          │
          └────► Ingress-Oberfläche
                 Charts · Tabellen · Pflege · Export
```

Die Verantwortlichkeiten bleiben bewusst getrennt:

- Die **Integration** entscheidet, welche Home-Assistant-Entitäten gesendet
  werden, und hält Übertragungsfehler von Home Assistant fern.
- Die **App** entscheidet über Auflösung, Aufbewahrung, Speicherung,
  Aggregation und Darstellung. Die Entitäts-Auflösung dient der Darstellung;
  eingehende Werte werden nicht mehr über einen zeitlichen Mindestabstand
  verworfen.

## Installation

### 1. App installieren

**Über den Add-on-Store:** In Home Assistant **Einstellungen → Add-ons →
Add-on-Store → ⋮ → Repositories** öffnen, `https://github.com/bertel2020/HA-Apps`
eintragen und hinzufügen. **Zeitarchiv** erscheint danach im Store; installieren
und starten. Unterstützt werden `amd64` und `aarch64`.

**Manuell:** Alternativ den Inhalt dieses Verzeichnisses als
`/addons/zeitarchiv` auf den Home-Assistant-Host kopieren und den Store neu
laden.

Nach dem Start erscheint Zeitarchiv in der Home-Assistant-Seitenleiste. Die
vollständige Oberfläche läuft über den authentifizierten Supervisor-Ingress;
ein separates Benutzerkonto ist nicht erforderlich.

### 2. API-Token kopieren

Zeitarchiv öffnen und unter **Einstellungen → Verbindung** den automatisch
erzeugten API-Token kopieren. Der Token kann dort später neu generiert werden.

### 3. Integration verbinden

Die [Zeitarchiv-Integration](https://github.com/bertel2020/HA-Zeitarchiv)
installieren (über HACS oder manuell, siehe deren README) und in Home
Assistant über **Einstellungen → Geräte & Dienste → Integration hinzufügen →
Zeitarchiv** einrichten. Benötigt werden Host, Port `8127` und der API-Token.

### 4. Archivfilter festlegen

Auf der Integrationskachel **Konfigurieren → Archivfilter bearbeiten** öffnen
und Domains, Entitäten, Bereiche oder Geräte auswählen. Ohne passende Filter
wartet die App auf Daten, archiviert aber nichts.

## Kernfunktionen

Details zur Bedienung jeder Seite stehen im
[Benutzerhandbuch](docs/user-guide.md); hier nur ein funktionaler Überblick.

**Dashboards.** Charts und Vergleichstabellen lassen sich als Kacheln auf
beliebig vielen, frei benannten Dashboards anordnen — nicht nur auf einer
einzigen Startseite.

**Entitäten und Verläufe.** Jede archivierte Entität besitzt eine eigene
Verlaufsansicht mit Zeitraum-Navigation von Stunde bis Dekade, Vergleich mit
Vorperiode oder Vorjahr sowie individuell einstellbarer Auflösung,
Aufbewahrung und Rundung.

**Charts und Tabellen.** Eigene Charts können mehrere Entitäten mit
unterschiedlichen Einheiten überlagern. Vergleichstabellen kombinieren
einzelne Entitäten, Summengruppen und Formeln über frei gewählte
Zeitspalten (z. B. Monat für Monat über mehrere Jahre).

**Datenpflege.** Ausreißer, Lücken, doppelte Zeitstempel und gerundet
gleiche Wiederholungen werden erkannt und lassen sich einzeln korrigieren
oder als zunächst rückgängig machbare Soft-Delete-Markierung entfernen.
Physisch entfernt werden markierte Werte erst durch einen separaten,
endgültigen Schritt.

**Statistik.** Entitätenzahl, Datensätze, Speicherbedarf und Wachstum über
die Zeit, aufgeschlüsselt nach Typ, Auflösung und Aufbewahrung.

**Import und Export.** Bestehende Historie lässt sich aus Symcon-Exporten,
frei zuordenbaren CSV-Dateien oder direkt aus der laufenden
Home-Assistant-Instanz (Rohhistorie oder Langzeitstatistik) übernehmen.
Jeder Import bleibt als Report nachvollziehbar; bereits vorhandene
Zeitstempel werden dabei nie dupliziert. Die vollständige Rohdatenhistorie
einer Entität lässt sich als CSV exportieren.

## Speicherung und Aufbewahrung

```text
laufender Monat      abgeschlossene Monate        Langzeitabfragen
Hot Buffer (CSV)  ─► Parquet + zstd            ─► vorberechnete Rollups
```

Der laufende Monat liegt als anhängbare CSV-Datei vor. Abgeschlossene Monate
werden spaltenorientiert und verlustfrei komprimiert; daraus vorberechnete
Rollups (Stunde bis Jahr) machen Langzeitauswertungen schnell, ohne bei jeder
Abfrage über Rohdaten aggregieren zu müssen. Die automatische, standardmäßig
deaktivierte Aufbewahrungs-Durchsetzung entfernt Werte jenseits der je
Entität konfigurierten Frist; als **Unbegrenzt** markierte Entitäten bleiben
unangetastet.

## Backup und Wiederherstellung

Vollständige, portable ZIP-Backups (Index, Hot Buffer, Monatsarchive,
Rollups) lassen sich erstellen, planen, prüfen und wiederherstellen —
zusätzlich zu, nicht statt der automatischen Home-Assistant-Snapshots. Eine
Wiederherstellung wird vor der Anwendung geprüft (Prüfsummen, ZIP-Struktur,
Index-Integrität) und ist rollback-fähig: Der bisherige Datenbestand wird vor
dem Überschreiben verschoben, nicht gelöscht.

## Sicherheit und Netzwerk

| Zugang | Erreichbarer Umfang |
| --- | --- |
| Supervisor-Ingress, intern Port `8099` | Vollständige Oberfläche und Verwaltung |
| Veröffentlichter Port `8127` | Nur `GET /api/health` und `POST /api/write` |

Beide API-Endpunkte auf Port `8127` benötigen einen Bearer-Token. Oberfläche,
Abfragen, Exporte, Backups, Importe und Verwaltungsrouten antworten dort mit
HTTP 404. Importpfade werden normalisiert, Archive auf Zip-Bomb-Muster geprüft
und dynamische Inhalte mit restriktiven Sicherheitsheadern ausgeliefert.

### Ressourcenlimits

| Operation | Grenze |
| --- | ---: |
| Events je Schreibbatch | 1.000 |
| Entitäten je Multi-Abfrage | 25 |
| Punkte je Rohwertabfrage | 100.000 |
| Zeilen je CSV-Export | 5.000.000 |
| Importzeilen je Entität | 10.000.000 |
| ZIP-Upload | 2 GiB |
| CSV-Upload | 256 MiB |
| `settings.json` | 16 MiB |
| Entpackte ZIP-Daten | 5 GiB |

## Einstellungen und Konfiguration

Darstellung, Archivierungs-Standards, Aufbewahrung, Verbindung, Diagnose und
mehr werden vollständig in der App verwaltet und im Zeitarchiv-Index
gespeichert — siehe [Benutzerhandbuch → Einstellungen im
Detail](docs/user-guide.md#einstellungen-im-detail). Die einzige
Supervisor-Option ist `timezone` (IANA-Zeitzone, Standard `Europe/Berlin`).

Beim Start sowie nach Datenimporten gleicht Zeitarchiv die abgeleiteten
Indexkennzahlen automatisch mit Parquet-Archiv und Hot Buffer ab, um
Inkonsistenzen früh sichtbar zu machen.

## Bekannte Grenzen

- Die App ist für ein einzelnes lokales Home-Assistant-System und einen
  gemeinsamen API-Token ausgelegt; Mehrbenutzer- oder Mandantentrennung gibt
  es derzeit nicht.
- Die Ingress-Seiten sind nicht als eigenständige Lovelace-Karten oder
  öffentliche Chart-URLs gedacht.
- Manuelle Lösch- und Retention-Aktionen können endgültig sein. Vor größeren
  Eingriffen sollte ein geprüftes Backup erstellt werden.

## Lizenz

Dieses Projekt steht unter der [Apache License 2.0](LICENSE).
Copyright 2026 bertel2020.

Die verwendeten Drittanbieter-Komponenten und deren Lizenzen sind in
[THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md) aufgeführt.

---

<p align="center">
  <a href="https://github.com/bertel2020/HA-Zeitarchiv">Integration einrichten</a>
  ·
  <a href="https://github.com/bertel2020/HA-Apps/blob/main/zeitarchiv/CHANGELOG.md">Changelog</a>
  ·
  <a href="https://github.com/bertel2020/HA-Apps/blob/main/zeitarchiv/DOCS.md">App-Store-Dokumentation</a>
</p>
