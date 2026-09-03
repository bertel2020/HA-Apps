# Frontend-Architektur

Kein Build-Schritt, kein Bundler, kein npm. `static/vendor/` enthält
unveränderte Kopien von Alpine.js, htmx und ECharts; alle App-eigenen Skripte
liegen unkompiliert unter `static/js/`.

## Rendering-Modell

Drei Schichten arbeiten zusammen, je nach Interaktionsbedarf der jeweiligen
Seite:

1. **Jinja2 (Server-Side-Rendering).** `Jinja2Templates` (`app/main.py`),
   Templates unter `app/templates/`. Zentrale eigene Jinja-Filter:
   `format_int`, `format_value` (`templates.env.filters[...]`, siehe
   `formatting.py`) — Zahlenformatierung einmal in Python statt an jeder
   Template-Stelle dupliziert.
2. **htmx** für partielle Neuladungen ohne eigenes JS: Formulare posten
   direkt (`hx-post`), Server antwortet mit einem HTML-Fragment
   (`_settings_*_form.html`-Muster), `hx-target`/`hx-swap` ersetzt genau den
   betroffenen DOM-Ausschnitt. Polling (z. B. Diagnose-Werkzeuge, solange
   aktiv) über `hx-trigger="every Ns"`.
3. **Alpine.js** für rein clientseitigen, nicht persistenten Zustand:
   Dropdown-Picker, Formular-Sichtbarkeit, und die beiden komplexesten
   Editoren der App (Chart- und Tabellen-Editor) — dort reicht ein
   HTML-Formular nicht, weil Nutzer Zeilen/Spalten frei hinzufügen,
   neu anordnen und live eine Vorschau sehen sollen, bevor gespeichert wird.

Faustregel im Code: **ein Formular, ein Wert, sofortiges Speichern** → htmx.
**Mehrere zusammengehörige, änderbare Elemente mit Live-Vorschau vor dem
Speichern** → Alpine.js-Komponente mit eigenem `x-data`-Zustand, erst beim
expliziten Speichern-Klick an den Server gesendet.

## Statische Assets

`app.mount("/static", ...)` liefert `app/static/` aus. Antworten werden mit
Cache-Headern versehen; Template-Links hängen `?v={{ css_v }}` /
`?v={{ js_v }}` an (Build-/Zeitstempel-basiert), damit ein Deploy nicht am
Browser-Cache alter Assets scheitert.

## Vergleichstabellen (`table-compute.js`)

Geteilte Berechnungslogik zwischen dem vollen Tabellen-Editor
(`table_editor.html`) und der kompakten Dashboard-Kachel-Ansicht
(`dashboard-tiles.js`) — **ein** Modul, damit ein Fix an einer Stelle nicht
an der anderen vergessen wird. Arbeitet auf reinen Index-Arrays (Spalten,
Zeilen), nicht auf Alpines reaktivem UID-Zustand.

- Ruft `/api/query-table` **einmal** für alle sichtbaren Spalten und benötigten
  Entitäten auf. Der Endpunkt teilt einen request-lokalen Lese-Cache über alle
  Zeiträume und liefert nur skalare Aggregate statt vollständiger Punktreihen.
  Dashboard-Kacheln übergeben nur den tatsächlich sichtbaren Tabellenbereich.
- Formel-Zeilen: ein kleiner handgeschriebener Ausdrucks-Parser
  (`evalFormula()`, unterstützt `+ - * / ()` und Zeilen-Buchstaben) statt
  `eval()`/`Function()` — bewusst, obwohl Formeln nur aus der eigenen
  Datenbank stammen (kein externer Angriffsvektor), weil ein handgebauter
  Parser für so einfache Ausdrücke die sauberere Wahl bleibt.
- Der Server berechnet je Entität und Zeitraum die Aggregate `auto`/`avg`/
  `min`/`max`/`sum`. Gruppen, Formeln und Nachkommastellen je Spalte bleiben
  clientseitige Darstellungslogik; gespeichert wird weiterhin nur die Struktur
  (siehe [data-model.md](data-model.md)).
- Darstellungsoptionen liegen ausschließlich im `style_json` der Tabelle und
  gelten identisch für Vollansicht und Dashboard-Kachel: Abschnittsnamen an
  Trennzeilen, Hervorhebung von Formelzeilen, fixierte Beschriftungsspalte/
  Kopfzeile, manuelle Spaltenbreiten (`_TableColumnBody.width`/
  `style.label_col_width`), Header-/Werte-Ausrichtung, gleich breite
  Werte-Spalten, abgesetzte Vergleichsspalten, prozentuale Abweichung unter
  dem aktuellen Wert, ausgeschriebene Fehlwerte sowie ein-/ausblendbare,
  optional kleinere oder ausgerichtete Einheiten und ausgerichtete
  Dezimalstellen. Alte Tabellen behalten durch konservative Defaults ihre
  bisherige Darstellung.
- **Layout-Lektion (siehe `table_editor.html`-Kommentare):** Zeilen-Buchstaben
  (A/B/C) als *separate* Tabelle neben statt als Spalte innerhalb der
  Haupttabelle zu rendern, klingt sauberer, führt aber zu Zeilenhöhen-Drift
  zwischen zwei unabhängigen `<table>`-Elementen (Border-Rundung, Badge- vs.
  Textzeilen-Höhe). Die robuste Lösung: Buchstaben-Spalte bleibt echte erste
  Tabellenspalte (der Browser garantiert dadurch pixelgenaue Zeilenhöhen von
  selbst); visuell "abgesetzt" wirkt sie stattdessen über gezielte
  `:not(...)`-Selektor-Ausnahmen bei Kopfzeilen-Hervorhebung, nicht über
  physische Trennung vom DOM.
- **Sticky-Header-Lektion:** `position:sticky` auf `<thead>`/`<tr>` wird von
  Safari/WebKit nicht zuverlässig unterstützt — dort bleibt insbesondere die
  Eck-Zelle (Kopfzeile × fixierte erste Spalte) unwirksam. Robust ist nur
  `position:sticky` auf jeder `<th>` einzeln; bei zweistufiger Kopfzeile
  braucht die zweite Zeile zusätzlich ein `top` in Höhe der ersten Zeile,
  sonst überlappen sich beide beim Scrollen. Diese Höhe variiert mit Dichte/
  Schriftgröße und wird deshalb per JS gemessen und als Custom Property
  `--tbl-group-header-h` gesetzt (`syncLetterPositions()` im Editor,
  `renderTableTile()` in `dashboard-tiles.js`).
- **Gleich breite Werte-Spalten (`style.equal_value_cols`):** ein reiner
  `width:1%`-CSS-Trick verteilt bei `table-layout:auto` den Platz NICHT
  zuverlässig gleichmäßig, sobald sich Zahlenlängen zwischen Spalten stark
  unterscheiden (Tages- vs. Jahressumme) — die schmalste Spalte bleibt an
  ihren Mindest-Inhalt gebunden. Robust ist `table-layout:fixed` zusammen mit
  einer `<colgroup>`: nur die Beschriftungsspalte bekommt eine explizite
  `<col>`-Breite, alle übrigen `<col>`-Elemente ohne eigene Breite teilen
  sich den Rest laut Spezifikation zu gleichen Teilen — motorunabhängig,
  anders als Breiten über Zellen der "ersten Zeile" bei fixed layout.

## Theming

CSS-Variablen (`--bg`, `--surface`, `--ink`, `--accent-line`, `--warning`,
`--danger`, …) in `static/css/app.css`, umgeschaltet über
`data-color-scheme`/`data-color-mode` auf `<html>`. Drei Farbschemata
(`zeitarchiv`, `home_assistant`, `modern`),
je mit eigenem Hell-/Dunkel-Variablensatz. Neue UI-Elemente müssen
ausschließlich diese Variablen verwenden, nie feste Hex-Farben — Ausnahme:
das Zeitarchiv-Logo (SVG) trägt bewusst feste Markenfarben, unabhängig vom
gewählten Schema, wie eine Wortmarke.

Das Schema `modern` trennt die Rollen bewusst: kühle Slate-Töne bilden
Hintergrund, Flächen und Rahmen; Cobalt ist die primäre UI-Farbe für
Navigation, Fokus und Auswahl; Teal bleibt Daten- und Chart-Akzent. Neue
Komponenten dürfen diese Rollen nicht durch komponentenspezifische
Festfarben vermischen. Warnungen und Fehler verwenden die globalen
`--warning*`-/`--danger*`-Token.

`--font-scale` (CSS-Variable, aus **Einstellungen → Darstellung**) skaliert
praktisch jede `font-size` in `app.css` über `calc(Npx * var(--font-scale,
1))` — neue Komponenten müssen dieses Muster übernehmen, sonst ignorieren
sie die Schriftgrößen-Einstellung.

## Charts (ECharts)

Kein eigener Chart-Renderer — ECharts-Instanzen werden direkt aus den
`/api/query[-multi]`-Antworten befüllt. Mehrere Entitäten mit
unterschiedlichen Einheiten bekommen automatisch getrennte Y-Achsen.

## Wiederkehrende Muster, die neue Seiten übernehmen sollten

- **`dd-picker`**: einheitliches Dropdown-Picker-Markup/-Verhalten
  (`static/js/dd-picker.js` für einfache Fälle, Alpine-`x-data` direkt für
  Picker, die pro Listenzeile mehrfach vorkommen — siehe Kommentare in
  `table_editor.html` zu genau dieser Abwägung).
- **`confirm-dialog.js`**: App-eigene Dialoge statt der Browser-Varianten —
  konsistentes Aussehen, in den drei Farbschemata korrekt eingefärbt.
  `appConfirm(text, {danger})` ersetzt `window.confirm()` (und greift über
  das `htmx:confirm`-Event auch für `hx-confirm`), `appAlert(text)` ersetzt
  `window.alert()`. In der App wird keine der beiden Browser-Funktionen mehr
  direkt aufgerufen: der native Dialog stellt der Meldung die Serveradresse
  voran („Auf 192.168.x.x:8123 wird Folgendes angezeigt“) und lässt sich
  nicht gestalten.
- **`card-browser.js`**: Suche und Sortierung der Kachel-Übersichten
  (Dashboards, Charts, Tabellen). Bewusst rein clientseitig — anders als die
  Entitäten-Übersicht, die per htmx auf dem Server filtert: diese Listen
  umfassen typischerweise ein paar Dutzend Einträge und stehen ohnehin
  vollständig im DOM. Sortiert wird über eigene Schlüssel je Kachel
  (`data-name`, `data-created`, `data-favorite`) statt über die vom Server
  gelieferte Reihenfolge; dadurch bleiben die `ORDER BY`-Klauseln der
  `list_*`-Methoden unangetastet, die z. B. auch das Dashboard-Dropdown der
  Topnav versorgen. „Favoriten zuerst“ ist ein eigener, mit jeder Sortierung
  kombinierbarer Schalter, kein Sortiermodus. Kacheln mit
  `data-sort-first="true"` bleiben unabhängig davon ganz vorn; die
  Dashboard-Übersicht nutzt dies für das Standard-Dashboard.
- **`_dashboard_usage.html`**: gemeinsame Verwendungsanzeige in geöffneten
  Chart- und Tabellenansichten. Sie nutzt die bestehenden Chip-, Menü- und
  Popover-Bausteine; bei mehreren Zuordnungen steht das Standard-Dashboard
  zuerst, danach folgen die Namen alphabetisch.
- **`number-format.js`**: einzige Stelle, die ein Zahlenformat kennt
  (aktuell deutsch, Komma als Dezimaltrennzeichen); eine künftige
  Sprachumschaltung ändert nur diese eine Datei, nicht jede einzelne
  Tabellen-/Chart-Seite.
- **`sortable-table.js`**: einheitliches Sortierverhalten für längere
  Listen-/Verwaltungstabellen (Bereinigungs-Vorschau, Indexkonsistenz,
  Ausführungsverläufe, Duplikate je Entität, Symcon-Zuordnungsbericht),
  inklusive automatischer Seitenumbrüche bei vielen Zeilen — neue Tabellen
  dieser Art sollten dieses Modul statt einer eigenen Sortierlogik nutzen.
- **Badge + Popup** (Energiedashboard): eine kompakte, farbige Kennzahl im
  Kartenkopf (`@click="$refs.xDialog.showModal()"`) öffnet ein natives
  `<dialog class="detail-dialog">` mit Details — spart Platz gegenüber einer
  dauerhaft sichtbaren Karte. Schließt über den Standard-Button sowie per
  Klick außerhalb (`@click="if ($event.target === $el) $el.close()"` auf dem
  `<dialog>` selbst). Mehrzeilige `[data-tooltip]`-Inhalte brauchen die
  Opt-in-Klasse `.tooltip-lines` (`white-space:pre-line`) plus echte `\n` im
  Attributwert — die App-weite Basisregel rendert sonst `white-space:normal`
  und Zeilenumbrüche fallen zu Leerzeichen zusammen.
- **`group-picker.js`** (Energiedashboard, Verbraucher-Gruppen): durchsuchbares
  Dropdown ohne feste Optionsliste — bestehenden Eintrag auswählen oder per
  Freitext einen neuen erzeugen (wie Tags/Labels in Home Assistant), analog zu
  `entity-picker.js`, aber die Optionsliste selbst ist eine im Root-`x-data`
  gehaltene, per Referenz (nicht kopiert) an jede Instanz durchgereichte
  Alpine-Liste — eine hier neu angelegte Gruppe taucht dadurch sofort in jedem
  anderen Gruppen-Feld auf, ganz ohne Server-Rundtrip.
- **`.usage-bar-track`/`.usage-bar-fill`**: schlanker Auslastungsbalken
  (Vorbild: `_settings_backup_progress.html`s Fortschrittsbalken, hier aber
  für einen Dauerzustand statt eines laufenden Vorgangs). Füllfarbe über eine
  zusätzliche Klasse `positive`/`warning`/`danger` an `.usage-bar-fill`, mit
  dezentem `color-mix()`-Glanzverlauf statt einer bunten Skala über die volle
  Breite. Aktuell genutzt für den Host-Speicherplatz in `housekeeping.html`.
- **`.status-card-accent`/`.status-card-accent-strong`**: zweistufige, nicht
  alarmierende Hervorhebung einer `.status-card`-Kachel (z. B. "Update
  verfügbar") — bewusst getrennt von `.status-card-danger`, damit Rot
  echten Problemen vorbehalten bleibt. Stufe 1 nur Rahmen/Hintergrund, Stufe
  2 zusätzlich eingefärbter Text.
- **Content-breite Karten statt gleich breiter Grid-Spalten** (Energiedashboard,
  mehrere Speicher): `display:flex;flex-wrap:wrap` statt `display:grid` mit
  `1fr`-Spalten, wenn Karten unterschiedlich viel Platz brauchen können —
  Flex-Items sind standardmäßig content-groß statt sich auf eine erzwungene
  gleiche Spaltenbreite zu strecken (die bei schmalerem Karteninhalt sichtbaren
  Leerraum danaben hinterlassen hätte), wrappen aber bei Platzmangel genauso
  in die nächste Zeile wie ein Grid.
