# Zeitarchiv — Design-System (`app.css`)

Gemeinsames Stylesheet für alle Ingress-Seiten der App. Entstanden aus einem
Redesign-Durchgang (August 2026), dessen Kernproblem war: jede Seite trug ihren
eigenen, leicht abweichenden `<style>`-Block mit sich — gleiche Farben, aber
unterschiedliche Seitenbreiten (720–1080px), unterschiedliche Tabellen-Paddings
und keine Garantie, dass z. B. die Spalte "Datensätze" auf der Startseite und auf
der Statistik-Seite an derselben Stelle landet. `app.css` ist die einzige Quelle
der Wahrheit für Farben, Typografie, Seitenbreite und die wiederkehrenden
Bausteine (Kacheln, Tabellen, Chips, Buttons) — jede Seite verlinkt es und fügt
in ihrem eigenen `<style>`-Block nur noch das hinzu, was wirklich seitenspezifisch
ist (z. B. der Chart-Container auf der Verlaufsseite, die Dropzone beim Import).

## Einbinden

```html
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500;600;700&display=swap">
<link rel="stylesheet" href="{{ base }}/static/css/app.css?v={{ css_v }}">
```

`{{ css_v }}` ist ein Jinja-Global (`templates.env.globals["css_v"]` in `main.py`,
an die mtime von `app.css` beim Start gekoppelt) — reines Cache-Busting, weil
`StaticFiles` keinen `Cache-Control`-Header setzt und Browser die Datei sonst
über einen Neustart/Deploy hinweg aus dem Cache weiterverwenden können. Beim
Hinzufügen eines neuen `<link>` auf `app.css` immer `?v={{ css_v }}` mitführen.

`{{ base }}` ist der bestehende relative Rückpfad zur App-Wurzel (Ingress hat
einen dynamischen Pfad-Präfix, ein absoluter Pfad würde daran vorbeizeigen):

| Route-Tiefe | Beispiel | `base` |
|---|---|---|
| Wurzel | `/`, `/statistik`, `/import`, `/settings` | kein `base` nötig — einfach `static/css/app.css` |
| 1 Ebene | `/entities/{id}` | `".."` |
| 2 Ebenen | `/entities/{id}/cleanup`, `/entities/{id}/config` | `"../.."` |

Fragmente, die per htmx in eine bereits geladene Seite eingehängt werden
(`_entities_table.html`, `_rows_table.html`, `_duplicates_preview.html`,
`_entity_config_form.html`, `_import_*.html`) binden **nichts eigenes ein** —
sie erben das Stylesheet der Seite, in die sie geswapped werden.

## Design-Tokens

Alle Farben sind CSS-Variablen auf `:root`, mit einem `@media (prefers-color-scheme: dark)`-Block für Dark Mode. Variablennamen sind bewusst unverändert aus der Vorversion übernommen (nur die Werte wurden verfeinert) — jeder bestehende `var(--accent-line)`-Verweis funktioniert unverändert weiter.

| Variable | Hell | Dunkel | Verwendung |
|---|---|---|---|
| `--bg` | `#F5F6F1` | `#0E1512` | Seitenhintergrund |
| `--surface` | `#FFFFFF` | `#171F1B` | Karten, Tabellen, Inputs |
| `--surface-alt` | `#EEF1E9` | `#1E2822` | Zebra/Hover-Hintergrund, deaktivierte Felder |
| `--border` | `#E1E6DB` | `#2A362F` | Standard-Trennlinie |
| `--border-strong` | `#CDD5C4` | `#3A483E` | Kopfzeilen-Trennlinie, Hover-Rahmen |
| `--ink` | `#131C17` | `#E8ECE4` | Haupttext, Werte |
| `--ink-muted` | `#4B584E` | `#9FAC9B` | Fließtext, Tabellenzellen |
| `--ink-faint` | `#8A9484` | `#66756A` | Labels, Zeitstempel, Platzhalter |
| `--accent-line` | `#0C6B5D` | `#4FC3AE` | Primärakzent (Standard-Entitäten, Links, aktive Auswahl) |
| `--accent-line-soft` | `#E1F1EC` | `#173430` | Sanfter Hintergrund für `--accent-line` |
| `--accent-bar` | `#B15E1B` | `#E2A15E` | Sekundärakzent (Zähler-Entitäten) |
| `--accent-bar-soft` | `#F7E8D6` | `#3A2A18` | Sanfter Hintergrund für `--accent-bar` |
| `--danger` | `#A23B36` | `#E28A85` | Ausreißer/Duplikate/destruktive Aktionen |
| `--danger-soft` | `#F4DEDB` | `#3A201E` | Sanfter Hintergrund für `--danger` |
| `--font-display` | `'IBM Plex Sans'` | — | Fließtext, Überschriften, UI-Beschriftungen |
| `--font-mono` | `'IBM Plex Mono'` | — | **Nur echte Daten** — siehe Typografie unten |
| `--shadow` | dezenter Elevation-Schatten für Karten/Kacheln |

Farben nie hart verdrahten — immer über `var(--…)`, sonst bricht Dark Mode
lautlos für genau diese eine Stelle.

## Typografie

**Kernregel des Redesigns:** Mono-Schrift ist für echte Daten reserviert —
Zeitstempel, Entity-IDs, Byte-/Zeilenzahlen, Token-Werte. Abschnitts-Labels,
Tabellenköpfe und UI-Beschriftungen sind Sentence-Case in `--font-display`,
nicht mehr GROSSBUCHSTABEN in `--font-mono`. Vorher fühlten sich Seiten mit viel
Mono-Text schnell wie eine Rohdaten-Tabelle an statt wie ein Produkt — diese
Trennung ist der größte einzelne Lesbarkeits-Hebel aus dem Redesign.

- `h1` — 1.5rem/700, Seitentitel
- `h2` — 1.05rem/700, Abschnittsüberschrift
- `.crumb` — 12.5px, `--ink-faint`, Breadcrumb über dem Titel
- `.sub` — 13.5px, `--ink-muted`, Unterzeile mit Kontextlinks
- Tabellenkopf (`table.dt th`) — 12px/600, `--ink-faint`, **kein** Uppercase/Letter-Spacing mehr
- Tabellenzelle (`table.dt td`) — 13.5px, `--ink-muted`, `tabular-nums`

## Seiten-Grundgerüst

```css
.page{max-width:1120px;margin:0 auto;min-width:0;}
```

Eine einzige Breite für alle Seiten — vorher zwischen 720px (Konfiguration) und
1080px (Import) uneinheitlich. `min-width:0` verhindert, dass breite Inhalte
(Tabellen, Diagramme) das Flex-/Grid-Elternelement aufblähen; `.tbl-wrap` und
`.card` scrollen bei Bedarf selbst horizontal statt die ganze Seite zu strecken.

Jede Seite folgt demselben Kopfbereich:

```html
<div class="page">
  <p class="crumb"><a href="{{ base }}/">Zeitarchiv</a> / … </p>
  <h1>…</h1>
  <p class="sub">… &middot; <a href="…">Kontextlink →</a></p>
  …
</div>
```

## Bausteine

### Kacheln (`.stat-row` / `.stat`)

```html
<div class="stat-row">
  <a class="stat" href="statistik">
    <div class="label">Entitäten</div>
    <div class="value">9</div>
    <div class="sub-value">3 Standard · 4 Zähler · 2 Schalter</div>
  </a>
  …
</div>
```

`.stat` ist sowohl als `<div>` (rein informativ, z. B. auf der Konfigurationsseite)
als auch als `<a class="stat">` (klickbar, mit Hover-Anhebung) einsetzbar — beide
teilen sich dieselbe Kachel-Optik. Sparklines (`<svg class="sparkline">` mit
einer `<polyline>`, erzeugt über `_sparkline_points()` in `main.py`) sind optional
und werden nur bei genug Verlaufsdaten gerendert.

### Tabellen (`table.dt`)

```html
<table class="dt">          <!-- normale Zeilenhöhe: Entitäten, Statistik -->
<table class="dt compact">  <!-- engere Zeilenhöhe: Werte-Listen mit vielen Zeilen -->
```

`.dt.compact` ist bewusst **nicht** überall der Standard — nur dort, wo eine
Tabelle typischerweise hunderte Zeilen auf einmal zeigt (Bereinigung, Duplikate-
Vorschau, Konfigurations-Vorschau), zählt Dichte mehr als Luft zwischen den
Zeilen. Die Entitäten- und Statistik-Tabellen bleiben bei der normalen Höhe,
weil sie seltener über wenige Dutzend Zeilen hinauswachsen.

Beide Varianten teilen sich Kopfzeile, Zebra-Hover (`tbody tr:hover`) und die
`tabular-nums`-Ausrichtung für Zahlenspalten — genau das sorgt dafür, dass eine
Spalte wie "Größe" auf zwei verschiedenen Seiten optisch identisch aussieht.

### Badges — zwei verschiedene, absichtlich getrennte Klassen

- **`.badge`** — Entitätstyp (Standard/Zähler/Schalter), sanft eingefärbter Rahmen-Pill, erscheint in der Entitäten-Tabelle.
- **`.flag-badge`** — Zeilen-Warnung (Ausreißer/Lücke/Duplikat) in der Bereinigungs-Tabelle, kräftig rot gefüllt.

Beide heißen bewusst nicht gleich `.badge` — die frühere Vorversion tat das,
was dazu führte, dass eine CSS-Änderung an einer Bedeutung ungewollt die andere
mit veränderte. Beim Hinzufügen eines neuen Badge-Typs: erst prüfen, ob er
semantisch näher an "Typ-Kennzeichnung" oder "Zeilen-Warnung" liegt, statt eine
dritte Variante zu erfinden.

### Chips (`.chip` / `.filter-chip`)

Zwei Interaktionsmuster, eine Optik:

- **`.filter-chip`** — `<label><input type="checkbox|radio"><span>…</span></label>`, rein CSS-getrieben (kein JS nötig für den visuellen Zustand). Verwendet auf der Entitäten- und Bereinigungs-Seite für Filter, die über `hx-include`/`hx-trigger="change"` laufen.
- **`.chip`** — `<button class="chip" :class="{active: …}">`, Zustand kommt aus Alpine.js. Verwendet auf der Verlaufsseite (Chart-Toolbar), wo der Zustand ohnehin schon clientseitig in Alpine lebt.

Die Auswahlfarbe ist standardmäßig `--accent-line` (teal, neutrale Auswahl).
Wo die Auswahl tatsächlich einen Alarm-Filter markiert (Bereinigung: Ausreißer/
Lücken/Duplikate), überschreibt eine **lokale** Regel in `cleanup.html` die
Auswahlfarbe auf `--danger` — das ist eine bewusste, dokumentierte Abweichung,
keine Inkonsistenz.

### Buttons (`.btn`)

`.btn` ist die Basis (grauer Rahmen, `--surface-alt`-Hintergrund). Varianten:
`.btn.primary`/`.btn-primary` (gefüllt, `--accent-line`), `.btn-danger`/`.btn.danger`
(roter Rahmen), `.btn-danger-outline` (wie `.btn-danger`, aber mit eigenem
`:disabled`-Zustand für "erst Filter wählen"-Fälle), `.navbtn` (quadratischer
Icon-Button für ‹/›-Navigation, meist als `class="btn navbtn"` kombiniert).

### Einstellungen (`.settings-layout`)

Zweispaltiges Layout für `/settings`: `.settings-nav` (Kategorie-Liste, `<a href="#anchor">`
zu `<section id="anchor">`-Blöcken im `.settings-panel`) + `.settings-panel`
(Inhalt aller Kategorien, untereinander gerendert — kein JS-Tab-Umschalten,
bewusst einfach gehalten, da die meisten Kategorien noch Platzhalter sind).
Ein `.nav-divider` trennt echte Einstellungs-Kategorien von Querverweisen zu
anderen Seiten (aktuell: Statistik-Übersicht).

## Was bewusst lokal bleibt

Nicht jede Seiten-CSS ist eine Inkonsistenz, die zentralisiert gehört:

- **`import.html`** behält seine eigenen `.dropzone`/`.upload-progress`/`.map-input`/`.callout`-Regeln — hochspezifisch für den Upload-Assistenten, kein zweiter Verwendungsort in Sicht.
- **`entity_detail.html`** behält `.toolbars`/`.seg`/`.nav`/`.period-label`/`#chart` — das Zeitraum-Umschalter-Muster der Chart-Seite, ebenfalls ohne zweiten Verwendungsort.
- **`cleanup.html`** behält die eine Zeile für die rote Auswahlfarbe der Alarm-Filter (siehe oben).

Faustregel: Etwas wandert nach `app.css`, sobald es auf **zwei oder mehr**
Seiten (fast) identisch vorkommt. Einmalige, seitenspezifische Interaktionsmuster
bleiben lokal — sonst wird `app.css` selbst zu der Art von unübersichtlicher
Groß-Datei, die dieses Redesign eigentlich vermeiden sollte.

## Neue Seite hinzufügen — Checkliste

1. `<link>` auf Google Fonts + `{{ base }}/static/css/app.css` (Tiefe siehe Tabelle oben).
2. `<div class="page">` mit `.crumb` → `h1` → `.sub`.
3. Tabellen bekommen `class="dt"` (oder `class="dt compact"` bei vielen Zeilen) statt eigener `table`/`th`/`td`-Regeln.
4. Kacheln nutzen `.stat-row`/`.stat` (als `<a>`, falls klickbar).
5. Buttons/Chips nutzen `.btn`/`.chip`/`.filter-chip` statt neu erfundener Klassen.
6. Nur wirklich seitenspezifische Regeln in den lokalen `<style>`-Block — bei allem anderen erst prüfen, ob `app.css` es schon anbietet.
