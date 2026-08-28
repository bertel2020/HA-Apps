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

- Ruft `/api/query-multi` **clientseitig** auf, ein Request je Spalte
  (Zeitraum), alle in dieser Spalte gebrauchten Entitäten gebündelt.
- Formel-Zeilen: ein kleiner handgeschriebener Ausdrucks-Parser
  (`evalFormula()`, unterstützt `+ - * / ()` und Zeilen-Buchstaben) statt
  `eval()`/`Function()` — bewusst, obwohl Formeln nur aus der eigenen
  Datenbank stammen (kein externer Angriffsvektor), weil ein handgebauter
  Parser für so einfache Ausdrücke die sauberere Wahl bleibt.
- Aggregation je Zeile (`auto`/`avg`/`min`/`max`/`sum`) und Nachkommastellen
  je Spalte sind rein clientseitige Darstellungslogik — der Server speichert
  nur die Struktur (siehe [data-model.md](data-model.md)), berechnet nie
  selbst eine Tabellenzelle.
- **Layout-Lektion (siehe `table_editor.html`-Kommentare):** Zeilen-Buchstaben
  (A/B/C) als *separate* Tabelle neben statt als Spalte innerhalb der
  Haupttabelle zu rendern, klingt sauberer, führt aber zu Zeilenhöhen-Drift
  zwischen zwei unabhängigen `<table>`-Elementen (Border-Rundung, Badge- vs.
  Textzeilen-Höhe). Die robuste Lösung: Buchstaben-Spalte bleibt echte erste
  Tabellenspalte (der Browser garantiert dadurch pixelgenaue Zeilenhöhen von
  selbst); visuell "abgesetzt" wirkt sie stattdessen über gezielte
  `:not(...)`-Selektor-Ausnahmen bei Kopfzeilen-Hervorhebung, nicht über
  physische Trennung vom DOM.

## Theming

CSS-Variablen (`--bg`, `--surface`, `--ink`, `--accent-line`, …) in
`static/css/app.css`, umgeschaltet über `data-color-scheme`/`data-color-mode`
auf `<html>`. Drei Farbschemata (`zeitarchiv`, `home_assistant`, `modern`),
je mit eigenem Hell-/Dunkel-Variablensatz. Neue UI-Elemente müssen
ausschließlich diese Variablen verwenden, nie feste Hex-Farben — Ausnahme:
das Zeitarchiv-Logo (SVG) trägt bewusst feste Markenfarben, unabhängig vom
gewählten Schema, wie eine Wortmarke.

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
- **`confirm-dialog.js`**: App-eigener Bestätigungsdialog statt
  `window.confirm()` — konsistentes Aussehen, in den drei Farbschemata
  korrekt eingefärbt.
- **`number-format.js`**: einzige Stelle, die ein Zahlenformat kennt
  (aktuell deutsch, Komma als Dezimaltrennzeichen); eine künftige
  Sprachumschaltung ändert nur diese eine Datei, nicht jede einzelne
  Tabellen-/Chart-Seite.
- **`sortable-table.js`**: einheitliches Sortierverhalten für längere
  Listen-/Verwaltungstabellen (Bereinigungs-Vorschau, Indexkonsistenz,
  Ausführungsverläufe, Duplikate je Entität, Symcon-Zuordnungsbericht),
  inklusive automatischer Seitenumbrüche bei vielen Zeilen — neue Tabellen
  dieser Art sollten dieses Modul statt einer eigenen Sortierlogik nutzen.
