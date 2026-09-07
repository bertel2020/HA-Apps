# Zeitarchiv — Design-System (`app.css`)

Gemeinsames Stylesheet für alle Ingress-Seiten der App. Entstanden aus einem
Redesign-Durchgang (August 2026), dessen Kernproblem war: jede Seite trug ihren
eigenen, leicht abweichenden `<style>`-Block mit sich — gleiche Farben, aber
unterschiedliche Seitenbreiten (720–1080px), unterschiedliche Tabellen-Paddings
und keine Garantie, dass z. B. die Spalte "Datensätze" auf der Startseite und auf
der Statistik-Seite an derselben Stelle landet. `app.css` ist die einzige Quelle
der Wahrheit für Farben, Typografie, Seitenbreite und die wiederkehrenden
Bausteine (Kacheln, Tabellen, Chips, Buttons) — jede Seite verlinkt es und fügt
nur noch das hinzu, was wirklich seitenspezifisch ist (z. B. der Chart-Container
auf der Verlaufsseite, die Dropzone beim Import).

Dieses Seitenspezifische stand bis September 2026 als `<style>`-Block im
jeweiligen Template. Seit ZG-04 Schritt 3 liegt es als eigene Datei in
`pages/` — siehe unten „Seitenlokales CSS".

## Einbinden

```html
<link rel="stylesheet" href="{{ app_root }}/static/css/app.css?v={{ css_v }}">
```

Das steht seit ZG-04 Schritt 1 nur noch **einmal**, in `base.html`. Eine neue
Seite erbt es über `{% extends "base.html" %}` und schreibt es nicht selbst.
Daneben stand bis ZG-14 ein zweiter `<link>` auf `fonts.googleapis.com` — die
Schriften liegen jetzt als WOFF2 unter `static/fonts/` und werden am Kopf
dieser Datei per `@font-face` gebunden. Der Kommentar dort erklärt, warum die
Pfade relativ sein müssen und warum ein Schriften-Update einen neuen
Dateinamen bekommt.

`{{ app_root }}` ist der Präfix aus dem `X-Ingress-Path`-Header (siehe
`_app_root_context()` in `main.py`) und damit unabhängig davon, wie tief die
Seite in der URL liegt. Vorher trugen die Seiten dafür eine `base`-Variable mit
je nach Tiefe `""`, `".."` oder `"../.."` — dieser Mechanismus ist mit ZG-03
ersatzlos entfallen. `tests/test_ingress_prefix.py` prüft die Auflösung, nicht
die Schreibweise.

`{{ css_v }}` ist ein Jinja-Global (`templates.env.globals["css_v"]` in
`main.py`, die jüngste mtime über **alle** Dateien unter `static/css/`) — es
macht das lange `Cache-Control: public, max-age=31536000, immutable` sicher, das
`_CachedStaticFiles` auf `/static/*` setzt. Ohne Cache-Buster bliebe eine
geänderte Datei ein Jahr lang unsichtbar. Bei jedem neuen `<link>` auf ein
eigenes Stylesheet deshalb immer `?v={{ css_v }}` mitführen.

## Seitenlokales CSS

Was nur eine Seite braucht, liegt als `pages/<seite>.css` neben dieser Datei und
wird im `page_css`-Block des Templates verlinkt:

```html
{% block page_css %}
<link rel="stylesheet" href="{{ app_root }}/static/css/pages/statistik.css?v={{ css_v }}">
{% endblock %}
```

Der Dateiname folgt dem Template (`statistik.html` → `pages/statistik.css`);
`tests/test_page_css.py` hält das fest. Der Grund für die eigene Datei ist nicht
Ordnung, sondern Übertragung: als `<style>`-Block reisten diese Regeln bei
**jedem** Seitenaufruf erneut mit (ZG-22: 62 KB allein auf dem
Energiedashboard), als Datei genau einmal.

Eine Regel, die zwei Seiten brauchen, gehört nicht in zwei `pages/`-Dateien,
sondern nach `app.css`.

Das seitenlokale **JavaScript** folgt derselben Regel und liegt spiegelbildlich
als `static/js/pages/<seite>.js`, verlinkt am Körperende zwischen den geteilten
Skripten (`tests/test_page_scripts.py`). Inline bleibt im Template nur, was
Jinja braucht — Startwerte und Serverdaten, nichts, was etwas *tut*.

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
  <p class="crumb"><a href="{{ app_root }}/">Zeitarchiv</a> / … </p>
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

### Hinweistexte (`.hint` + Rolle)

Drei Sorten Text teilen sich Schriftgröße, Zeilenhöhe und `--ink-faint`, sind
aber nicht dasselbe. Die **Rolle** steht zusätzlich zur Kontextklasse (`hint`,
`tbl-hint`, `settings-compact-hint`): der Kontext bestimmt Größe und Abstände,
die Rolle, ob der Text hinter dem Info-Knopf wegklappen darf.

| Klasse | Bedeutung |
| --- | --- |
| `hint` allein | Erklärung — darf hinter den Info-Knopf (`_hints.html`, `hint-toggle.js`) |
| `+ hint-warn` | Warnung — bleibt immer sichtbar |
| `+ hint-warn hint-warn-strong` | Warnung vor nicht umkehrbarem Verlust: Kante in `--warning`, ein Ton mehr Kontrast. Nicht `--danger` — das ist die Farbe für Fehler und stumpft sonst ab |
| `+ hint-status` | Daten-, Leer- und Ladezustand — ist Inhalt, keine Erklärung |

Die Rollen sind **Auszeichnung, keine Gestaltung**: außer `hint-warn-strong`
gestaltet keine von ihnen etwas, damit eine künftige Auszeichnung nicht
nebenbei das Aussehen ändert (`tests/test_hint_roles.py`). Die Beschriftung
über einer Kartenzahl war nie ein Hinweis und heißt `status-card-label`.

`.hint-toggle` ist der 16-px-Knopf im Label; seine Trefferfläche wächst über
ein Pseudoelement auf 44 × 44 px, ohne im Layout Platz zu belegen.

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

1. `{% extends "base.html" %}` — Schriften, `app.css` und Topnav kommen von dort.
2. `<div class="page">` mit `.crumb` → `h1` → `.sub`.
3. Tabellen bekommen `class="dt"` (oder `class="dt compact"` bei vielen Zeilen) statt eigener `table`/`th`/`td`-Regeln.
4. Kacheln nutzen `.stat-row`/`.stat` (als `<a>`, falls klickbar).
5. Buttons/Chips nutzen `.btn`/`.chip`/`.filter-chip` statt neu erfundener Klassen.
6. Nur wirklich seitenspezifische Regeln nach `pages/<seite>.css` — bei allem anderen erst prüfen, ob `app.css` es schon anbietet.
7. Seitenlokales JavaScript nach `static/js/pages/<seite>.js` — im Template bleibt nur, was Jinja einsetzt.
