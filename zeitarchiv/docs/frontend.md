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

## Seitenrahmen (`base.html`) und URL-Präfix

Alle 23 Vollseiten-Templates erben ihren Rahmen von `base.html` — Doctype,
`<head>`, Stylesheet-Verweis, Topnav. Vorher baute sich jede Seite
ihren Kopf selbst zusammen, und zwar weitgehend gleich: Doctype, `<html>`,
`<meta charset>`, Viewport, Font-Link und `<body>` waren über alle 23
byte-identisch. (Der Font-Link ist inzwischen ganz entfallen — seit ZG-14
liegen die Schriften lokal und werden in `app.css` gebunden, siehe
„Statische Assets".) Ein Fix am Kopf musste damit 23-mal gepflegt werden — und die
eine Zeile, die *nicht* identisch war, lief in vier Schreibweisen
auseinander (siehe unten).

Der Rahmen bietet fünf Blöcke, alle aus dem Bestand abgelesen statt auf Vorrat
angelegt:

| Block | Wofür |
| --- | --- |
| `title` | Der **ganze** Titel, nicht nur sein variabler Teil: 21 Seiten folgen „Zeitarchiv — X", zwei nicht |
| `root_vars` | Zusätzliche `:root`-Variablen; nur `entities.html` und `dashboard_detail.html` setzen neben `--font-scale` noch `--dashboard-row-height` |
| `page_css` | Der `<link>` auf das seitenlokale Stylesheet (`static/css/pages/<seite>.css`) |
| `topnav` | Nur `_energiedashboard_report.html` überschreibt ihn (leer) |
| `content` | Der gesamte Seitenkörper **einschließlich der `<script>`-Tags am Ende** |

Einen `page_js`-Block gibt es bewusst nicht: kein Template hat ein `<script>`
im `<head>`. Die Tags stehen am Körperende und reisen im `content`-Block mit —
auch der Verweis auf das seitenlokale Skript (`static/js/pages/<seite>.js`,
siehe unten). Die Ladereihenfolge bleibt dadurch exakt die bisherige.

**Jeder Pfad im HTML beginnt mit `{{ app_root }}`.** Die Variable kommt aus
einem Kontext-Prozessor (`_app_root_context` in `main.py`), der sie für *jede*
`TemplateResponse` mitliefert — unter Home Assistant aus dem
`X-Ingress-Path`-Header, lokal aus `root_path`, sonst leer. Sie ist absolut und
damit unabhängig davon, wie tief eine Seite in der URL hängt.

Genau daran ist die App früher wiederholt gescheitert: Seiten schrieben ihr
Präfix selbst. Allein der Stylesheet-Verweis stand in vier Fassungen im Baum —
`static/css/app.css`, `../static/…`, `{{ base }}/static/…` und
`{{ app_root }}/static/…` —, und eine neue Seite auf einer neuen
Schachtelungstiefe erwischte die falsche. Die frühere `base`-Variable gibt es
nicht mehr; sie kommt in keinem Template und in keiner Route mehr vor, und ein
Test hält das fest. **Für eine neue Seite heißt das: `{{ app_root }}` vor jeden
Pfad, nichts anderes.**

Abgesichert ist beides durch `tests/test_base_template.py` (der Rahmen trägt
seine Zusagen und keine Seite wiederholt sie) und `tests/test_ingress_prefix.py`
— Letzteres prüft die *Auflösung* jeder Asset-Angabe, indem es sie wie ein
Browser per `urljoin` gegen den Header-Pfad auflöst, nicht ihre Schreibweise.
Der Test überlebt damit jede weitere Umstellung der Notation.

## Statische Assets

`app.mount("/static", ...)` liefert `app/static/` aus, mit
`Cache-Control: public, max-age=31536000, immutable`. Sicher ist dieser lange
Cache nur, weil jede Referenz einen Parameter trägt, der sich mit der Datei
ändert.

Adressiert wird über **`{{ asset('js/pages/statistik.js') }}`** — ein
Jinja-Global aus `main.py`, das den Ingress-Präfix davorsetzt und den
Cache-Buster anhängt. Eine Zeile nennt nur noch den Pfad unterhalb von
`static/`; den Rest kann sie damit nicht vergessen.

Der Buster ist seit ZG-05 der **Inhalts-Hash der einzelnen Datei** (blake2b, 8
Hexzeichen). Vorher waren es drei Zahlen — `css_v`, `js_v`, `vendor_v` —, je die
jüngste mtime über einen ganzen Ordner. Das ging zweimal schief: Git speichert
keine mtimes, ein CI-Checkout setzt alle Dateien auf die Checkout-Zeit und
`COPY` übernimmt sie, also war nach **jedem** Release alles neu — auf dem
Energiedashboard 1.317 KiB je Nutzer, davon 1,0 MB ECharts, das sich seit
Monaten nicht geändert hatte. Und eine Änderung an einer der 30 JS-Dateien
entwertete den Cache aller dreißig. Beides ist in
`tests/test_asset_versions.py` festgehalten.

Was nur eine Seite braucht, liegt als `static/css/pages/<seite>.css` neben
`app.css` und wird im `page_css`-Block verlinkt; dasselbe gilt für das
seitenlokale JavaScript als `static/js/pages/<seite>.js`, verlinkt am Körperende
zwischen den geteilten Skripten. Als `<style>`- bzw. `<script>`-Block im
Template reisten diese rund 477 KB bei jedem Seitenaufruf erneut mit, statt
einmal im Browser-Cache zu liegen. Inline bleibt im Template nur, was Jinja
braucht — Startwerte und Serverdaten, nichts, was etwas *tut*; `tests/
test_page_scripts.py` hält das fest. Konventionen dazu
(Dateiname folgt dem Template, wann etwas nach `app.css` gehört) stehen in
[`app/static/css/README.md`](../app/static/css/README.md), dem Dokument des
Design-Systems.

`static/fonts/` enthält IBM Plex Sans und IBM Plex Mono als WOFF2 (10 Dateien,
164 KB, Zeichensätze latin und latin-ext). Bis ZG-14 kamen beide von
`fonts.googleapis.com` — in Netzen ohne Internetzugang oder mit DNS-Filter
kostete das bei jedem Seitenaufbau einen Timeout, bevor die Ersatzschrift
griff. Gebunden werden sie per `@font-face` am Kopf von `app.css`, mit Pfaden
**relativ zum Stylesheet** (`url(../fonts/…)`): Ein Browser wertet `url()`
gegen die URL der CSS-Datei aus, die den Ingress-Präfix bereits trägt — ein
absolutes `/static/fonts/…` wäre unter Ingress ein 404, und `{{ app_root }}`
hilft nicht, weil CSS nicht durch Jinja läuft. Die Dateinamen sind
unveränderlich zu behandeln: `/static/*` trägt `Cache-Control: immutable` über
ein Jahr, und `url()` kann keinen `?v=`-Cache-Buster mitführen. Ein
Schriften-Update bekommt deshalb einen **neuen Dateinamen**.

## Hinweistexte: drei Rollen, ein Info-Knopf

Erklärende Hilfetexte belegten auf dem Telefon einen erheblichen Teil der Seite,
obwohl sie einmal gelesen und danach nicht mehr gebraucht werden. Sie stehen
deshalb hinter einem Info-Knopf und klappen bei Bedarf auf — inline, nicht als
Popover: keine Überlagerung, keine Positionsrechnung, und mit `dd-picker` gibt
es bereits eine Popover-Mechanik.

**Der Standardzustand hängt an der Breite:** am Schreibtisch aufgeklappt, unter
700 px zu. Bis 0.88.0 war er überall gleich (immer zu) — der Platzdruck, der
das Wegklappen nötig macht, besteht aber nur auf schmalen Geräten; auf der
Entität-Konfiguration waren es 557 von 2.806 px, am Schreibtisch fällt derselbe
Text nicht ins Gewicht. Damit verhält sich der Info-Knopf wie die beiden
anderen einklappbaren Blöcke der App (Protokollierungs-Karte auf der Log-Seite,
„So funktioniert …"-Anleitungen auf Import), bis hin zum selben Breakpoint. Der
Knopf bleibt auf beiden Breiten bedienbar; ein Breitenwechsel setzt auf den
Standard der neuen Breite zurück.

Voraussetzung war eine Unterscheidung, die es vorher nicht gab: alle Hinweise
trugen dieselbe Klasse `.hint`, obwohl drei verschiedene Dinge darin steckten.
Die Rolle steht **zusätzlich** zur Kontextklasse (`hint`, `tbl-hint`,
`settings-compact-hint`, `settings-section-description`), weil beide Achsen
unabhängig sind — der Kontext bestimmt Größe und Abstände, die Rolle, ob der
Text wegklappen darf:

| Rolle | Bedeutung |
| --- | --- |
| *(ohne)* | Erklärung — darf hinter den Info-Knopf |
| `hint-warn` | Warnung — bleibt sichtbar; `hint-warn-strong` gibt den drei Sätzen mit nicht umkehrbarem Verlust eine Kante in `--warning` |
| `hint-status` | Daten-, Leer- und Ladezustand — ist Inhalt, keine Erklärung |

`settings-section-description` — der Satz zwischen Abschnittsüberschrift und
Inhalt — kam zuletzt dazu: dieselbe Sorte Text, nur eine Ebene höher. Er
behält seine eigene Klasse, weil er anders aussieht (`--ink-muted`, 13 px statt
`--ink-faint`, 12,5 px), und steht deshalb sowohl im Selektor von
`hint-toggle.js` als auch in den Kontextklassen von `test_hint_roles.py`.

Gebaut wird das mit den Makros aus `_hints.html`
(`{% from "_hints.html" import hint_button, hint_body %}`), geklappt von
`static/js/hint-toggle.js`, das die Seite einbinden muss. Der Knopf findet
seinen Hinweis über die **Stellung** im Dokument, nicht über `aria-controls`:
ein Teil der Felder steht in Alpine-Vorlagen, die je Zeile neu ausgerollt
werden, feste ids wären dort mehrfach im Dokument. Geklappt wird über
Delegation an `document`, weil htmx die Formulare komplett austauscht.

Die Trefferfläche ist 44 × 44 px bei 16 px Symbol, über ein Pseudoelement und
6 px nach oben versetzt — mittig zentriert verliert der Knopf genau die unteren
6 px an das Bedienelement darunter. Abgesichert durch
`tests/test_hint_roles.py` (die Rollen sind Auszeichnung, keine Gestaltung) und
`tests/test_hint_toggle.py`.

**Faustregel, welcher Text aufklappen darf:** er erklärt das *Bedienen* genau
eines Elements. Wer das *Ablesen* der Anzeige erklärt, bleibt stehen.

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

`--font-mono` steht in dieser App für **maschinenlesbar**: Entity-IDs,
Zeitstempel, Rohwerte, Codeausschnitte — alles, was man kopiert oder Zeichen
für Zeichen vergleicht. Beschriftungen, Erklärtexte und selbst getippte
Anzeigenamen bekommen dagegen `--font-display`, auch wenn Zahlen darin
vorkommen; sollen die Zahlen beim Blättern nicht springen, leistet
`font-variant-numeric: tabular-nums` das ohne den Terminal-Eindruck einer
Monospace-Schrift. Die Unterscheidung trägt nur, solange sie konsequent
bleibt: liegt Mono auch auf Fließtext, sagt sie nichts mehr aus.

Zwei Fallstricke: Formularfelder erben `font-family` nicht — ohne explizite
Angabe fallen sie auf die Browser-Standardschrift zurück, nicht auf die der
App. Und ein Tooltip an einem Mono-Host (`td.mono`, Entity-ID-Zellen) erbt
dessen Schrift, wenn er selbst keine setzt.

## Charts (ECharts)

Kein eigener Chart-Renderer — ECharts-Instanzen werden direkt aus den
`/api/query[-multi]`-Antworten befüllt. Mehrere Entitäten mit
unterschiedlichen Einheiten bekommen automatisch getrennte Y-Achsen.

### Zoom: eine Lupe im Zeitraum, kein zweiter Zeitraum

Nur die Entitäts-Detailseite (`entity_detail.js`) hat einen `dataZoom`.
Möglich wird das dadurch, dass die x-Achse dort ohnehin hart auf das
Abfragefenster genagelt ist (`min: windowStart`, `max: periodEnd`), damit der
Chart immer die ganze gewählte Periode zeigt — auch dort, wo keine Daten
liegen. Der Zoom wählt einen Ausschnitt *innerhalb* dieser Grenzen; sein
Vollzustand ist per Definition die ohnehin sichtbare Periode. Daraus folgt der
Rest: keine Serveranfrage (die Punkte sind geladen), kein URL-Zustand
(`range`/`offset` behalten ihre Bedeutung), kein Eintrag in den Chart-Optionen
der Entität. Ein Neuzeichnen verwirft den Ausschnitt von selbst, weil
`setOption(option, true)` die ganze Komponente ersetzt.

Vier Einstellungen tragen das Verhalten, und die Voreinstellung von ECharts ist
an jeder einzelnen die falsche:

| Einstellung | Wert | Warum nicht die Voreinstellung |
| --- | --- | --- |
| `zoomOnMouseWheel` | `'ctrl'` | `true` ließe den Chart das Scrollrad kapern — wer vorbeiscrollt, zoomt. Ein Trackpad-Pinch erzeugt ctrl+wheel nativ, die Geste kommt dadurch gratis. |
| `moveOnMouseWheel` | `false` | Dasselbe Argument: das Rad allein gehört der Seite. |
| `moveOnMouseMove` | `'ctrl'` | zrender macht aus einer Ein-Finger-Berührung Mausereignisse; mit `true` gälte auf dem Telefon jeder senkrechte Wisch über dem Chart als Schwenk, und `preventDefaultMouseMove` (Voreinstellung `true`) hielte die Seite dabei an. Strg gibt es dort nicht — der Wisch bleibt der Seite, gezoomt wird per Pinch (eigener Handler, von diesen Schaltern unberührt). |
| `filterMode` | an `dynamicYAxis` gekoppelt | `'filter'` skaliert die y-Achse mit, `'none'` nicht. Die Seite hat mit „y-Achse fest/dynamisch" schon einen Schalter dafür — fest verdrahtet würde der Zoom gegen ihn arbeiten statt ihn zu bedienen. |

Angeboten wird der Zoom nur, wo mehr Punkte als Pixel da sind:
`points.length > ZOOM_MIN_POINTS` (200). Bewusst eine Schwelle über die
tatsächlich geladenen Punkte statt einer Liste erlaubter Zeiträume — dieselbe
Zeitraum-Stufe braucht je nach Melderhythmus der Entität unterschiedliche
Antworten. Der **Zeitstrahl** ist davon ausgenommen und bekommt den Zoom
immer: dort geht es nicht um Komfort, sondern um Sichtbarkeit. Ein Segment
wird von seinem Anfang bis zu seinem Ende gezeichnet, und bei „Monat" auf rund
900 px entspricht ein Pixel etwa 48 Minuten — jedes kürzere Schaltereignis ist
schmaler als ein Pixel. Die Zahl der Segmente sagt darüber nichts.

### Bereich aufziehen (Umschalt + Ziehen)

Ein Ausschnitt lässt sich auch direkt aufziehen: Umschalttaste halten, mit der
Maus einen Bereich über der Zeitachse markieren, loslassen. Die Geste war
frei — schlichtes Ziehen war unbelegt, Schwenken liegt auf Strg+Ziehen, Zoomen
auf Strg+Rad — und fügt sich in dieselbe Regel ein: Taste halten heißt „ich
meine den Chart". Auf einem Touchscreen gibt es keine Umschalttaste, dort
bleibt der Wisch also der Seite, ohne dass es dafür eine Sonderregel bräuchte.

Umgesetzt über die **`brush`-Komponente**, nicht über
`toolbox.feature.dataZoom`. Der naheliegende Weg wäre eine `toolbox` mit
`show: false` gewesen, damit die fremden ECharts-Icons draußen bleiben — der
ist aber am laufenden Chart gemessen **wirkungslos**: ohne sichtbare Toolbox
legt ECharts deren View nicht an, `takeGlobalCursor` mit `dataZoomSelect`
läuft ins Leere (`getModel().getComponent('brush')` bleibt `null`). Über
`brush` direkt ist der Modus nachweislich scharf (`brushOption.brushType`
wird `'lineX'`), und die Auswahl gehört uns: aus `brushEnd` wird selbst ein
`dataZoom` mit `startValue`/`endValue`, danach wird die Fläche sofort wieder
aufgehoben — sonst bliebe das Rechteck grau über dem Chart liegen.

Die Icons müssen an **zwei** Stellen abbestellt werden, und die zweite ist
nicht naheliegend: `toolbox: []` in der brush-Konfiguration sagt nur, welche
brush-Knöpfe die Werkzeugleiste zeigt. Die Werkzeugleiste selbst legt ECharts
trotzdem an — sie erschien mit vier fremden Icons genau dort, wo der
Ausschnitts-Chip sitzt. Erst ein zusätzliches `toolbox: {show: false}` in der
Option hält sie draußen.

Der Tastaturzustand braucht drei Ereignisse, nicht eines: `keydown` schaltet
scharf, `keyup` wieder aus — aber **nicht mitten im Ziehen**, sonst
hinterließe ein zu früh losgelassenes Umschalt einen halb gezogenen Rahmen;
der Zustand wird gemerkt und beim `mouseup` nachgezogen. Dazu `blur` auf dem
Fenster: wer bei gedrückter Taste das Fenster wechselt, bekommt nie ein
`keyup`, und der Chart bliebe dauerhaft im Auswahlmodus.

**Unter der Karte** steht dauerhaft eine Zeile, die beide Fälle benennt — wie
viele Punkte gezeichnet sind und ob sich daran etwas vergrößern lässt
(`get zoomHint()`). Sie ist `hint-status` und darf deshalb nicht hinter den
Info-Knopf: die Punktzahl ist ein Datenzustand, keine Erklärung. Ohne sie
bliebe der graue Ausschnitts-Chip unerklärt. Sie steht bewusst *außerhalb* der
Karte — in ihr beschreibt die Legende die Werte selbst, der Satz beschreibt
dagegen das Bedienen der Ansicht. Der Zeitstrahl bekommt einen eigenen
Wortlaut ohne Punktzahl: dort sind es oft eine Handvoll Segmente, und
„3 Datenpunkte — zoomen möglich" widerspräche der Regel, die der Satz daneben
aufstellt.

Zurückgesetzt wird über den Ausschnitts-Chip, der **im Chart** sitzt: oben
rechts, absolut im `.chart-wrap` positioniert, sichtbar nur bei aktivem Zoom.

Er stand zunächst in der Werkzeugleiste und war dort gemessen die Ursache für
deren Umbruch — bei 1000 px Fensterbreite brauchte sie mit ihm 1003 von 952
verfügbaren Pixeln, ohne ihn 843. Dabei war er das Element mit dem seltensten
Anlass: sichtbar immer, gemeint nur während eines Zooms. (Das ursprüngliche
Argument für den festen Platz — „jeder Control behält seine Stelle" — hat
sich mit dem Wegfall des „Jetzt"-Knopfes erledigt: ein meist deaktivierter
Knopf verdient keine Zeilenbreite.)

Im Chart überdeckt er keine Daten: ECharts beginnt erst bei `grid.top` (36 px)
zu zeichnen, und dieser Streifen ist rechts leer — links steht dort die
Einheiten-Beschriftung der y-Achse. Der Behälter `.chart-wrap` existiert nur,
damit sich „oben rechts" auf den Chart bezieht und nicht auf die Karte samt
ihrer je nach Breite unterschiedlichen Polsterung. Ein Schatten hebt ihn ab,
falls die Kurve doch einmal bis dorthin reicht. Die frühere Mindestbreite ist
entfallen — sie gab es nur, damit die wechselnde Beschriftung die Nachbarn in
der Leiste nicht verschiebt, und Nachbarn hat er dort keine mehr.

Dass es den Zoom überhaupt gibt, sagt deshalb allein die Hinweiszeile unter
der Karte: der Chip erscheint erst, wenn schon gezoomt ist.

Alle übrigen Charts der App bleiben ohne Zoom, und das ist eine Entscheidung
und kein offener Rest: eine Dashboard-Kachel ist ein Blickfang, kein Werkzeug;
Sankey und Donut haben keine Zeitachse; die Tag-mal-Stunde-Heatmap ist
kategorial; der Monatsverlauf im Bericht hat zwölf Balken.
`tests/test_chart_zoom.py` hält das fest.

### Markierte Bereiche (`markArea`)

„Löschen" auf der Bereinigungsseite ist ein Soft-Delete; endgültig entfernt
wird erst der Purge. Jeder Lesepfad filtert markierte Zeilen aber sofort heraus
(`filter_deleted_occurrences`) — sie waren damit aus jeder Ansicht
verschwunden, obwohl sie noch existierten und noch zu retten waren.

`/api/query?marked=true` liefert `marked_ranges` (Blöcke aus `start`, `end`,
`count`) und `marked_total` — die Zahl der markierten WERTE im Fenster, auch
wenn die Blockliste gekappt wurde (`MAX_MARKED_RANGES`). Gelesen wird
**ausschließlich der Index** (`get_deleted_counts`); Hot Buffer und Archiv
werden nicht angefasst. Genau das macht die Auskunft billig genug, um sie bei
jeder Abfrage mitzuliefern — und es ist der Grund, warum die Bänder-Variante
der Punkt-Variante vorzuziehen war: ein Band braucht die Werte nicht.

Benachbarte Markierungen werden zu einem Block zusammengefasst; die Schwelle
ist ein Hundertstel des Fensters (rund neun Pixel auf einem 900 px breiten
Chart). **Der Wert ist gemessen, nicht geraten:** ein Dreihundertstel (drei
Pixel) ergibt für eine Tagesansicht 4,8 Minuten, ein Sensor im 5-Minuten-Takt
lag also um zwölf Sekunden darüber — aus einem zusammenhängenden Block wurden
147 getrennte Bänder. Eine aus den Daten abgeleitete Schwelle (Median der
Abstände) scheitert an genau zwei Markierungen: dort ist der Median ihr
eigener Abstand, sie verschmelzen dann immer.

Gezeichnet wird als `markArea` an der Hauptserie, nicht als zweite Serie: eine
Serie bestimmte die Achsenskalierung mit, verfälschte die Punkt-Schwelle des
Zooms und stünde in der Legende. `silent: true` ist Pflicht — sonst fängt das
Band die Mauszeiger-Ereignisse ab und das Achsen-Tooltip der Kurve bleibt
ausgerechnet an den interessanten Stellen aus. Ein Block aus einer einzigen
Markierung bekommt eine Mindestbreite, sonst wäre er null Pixel breit.

Weil ein Band nur die Zeitachse braucht und keine Aussage über den Wert
trifft, gilt es für **jeden** Entitätstyp. (Ein Marker auf dem entfernten Wert
hätte das nicht gekonnt: bei einem Zähler sind Bucket-Werte Zuwächse und
Rohwerte absolute Stände.)

Der Zustand wird pro Entität gespeichert (`show_marked`, Standard aus). Der
Link aus der Purge-Vorschau (Housekeeping → Speicherplatz → Endgültige
Bereinigung) bringt ihn über `?marked=1` für den Besuch mit, **ohne** ihn zu
speichern: eine Seite über einen Link zu öffnen ist keine Einstellung.

Der Chip in der Werkzeugleiste erscheint nur, wenn es im Zeitraum tatsächlich
Markierungen gibt, und ist beides — Anzeige und Menü mit den zwei Wegen, die
man von dort aus gehen will: zur Bereinigungsseite (einzelne Markierungen
zurücknehmen) oder zur Rückgängig-Vorschau der letzten Charge
(`…/cleanup?undo=1`).

**Beides sind Links, und das ist der Punkt.** Die letzte Charge kann
sechsstellig sein — in einer echten Installation gemessen 196.263 Werte —,
während der Chip darüber nur die paar Markierungen des gezeigten Zeitraums
nennt. Aus diesem Menü heraus eine Aktion dieser Größenordnung auszulösen,
mit einer Rückfrage als einziger Zwischenstufe, wäre eine Falle: die Zahl im
Knopf legt eine ganz andere Größenordnung nahe als das, was tatsächlich
passiert. Die Vorschau auf der Bereinigungsseite zeigt stattdessen die
betroffenen Zeilen selbst, bevor irgendetwas geschieht.

Das Aufklappen der Vorschau übernimmt `cleanup.js` anhand von `?undo=1` — mit
einem **Warteschritt**, und der ist gemessen nötig: die Zeilentabelle wird beim
Seitenaufbau zweimal geholt (`hx-trigger="load"` auf `#controls`, und gleich
darauf ein `change`, weil das eingesetzte Fragment das Seitengrößen-Feld
schreibt; nur die zweite Anfrage trägt `page_size`). Wer nach dem ersten
Austausch öffnet, sieht die Vorschau vom zweiten sofort wieder überschrieben —
es sieht aus, als hätte der Klick nie stattgefunden.

### Werkzeugleiste: zwei Gruppen

Links steht, **welcher** Ausschnitt gezeigt wird (Zeitraum, Blättern,
Markierungen), rechts, **wie** er gezeigt wird (Vergleichen, Optionen —
`.toolbar-right` mit `margin-left:auto`, erst ab 641 px, darunter bricht die
Leiste ohnehin um). Entitäts-Chart und Chart-Editor tragen dieselbe Gruppe,
deshalb steht die Regel in `app.css`, nicht in einer der beiden Seiten-Dateien.

Die Gruppe muss die Knöpfe **umschließen**. Ein `margin-left:auto` auf dem
ersten von zweien schob nur diesen nach rechts und trennte den zweiten in die
nächste Zeile ab. Ihre Menüs klappen nach links auf
(`.toolbar-right .menu-popover{left:auto;right:0;}`), sonst ragt das 290 px
breite Optionen-Menü am rechten Rand aus dem Fenster — an dieselbe Breite
gebunden wie die Rechtsbündigkeit, weil es unterhalb davon genau andersherum
falsch wäre.

Unterhalb von 641 px reicht das nicht: dort hängt das Menü wieder links am
Knopf, und der steht am rechten Ende seiner Zeile — gemessen lief es auf einem
375-px-Telefon von 257 bis 547 px, ragte also 172 px aus dem Fenster, und da
`<html>` `overflow-x:hidden` trägt, waren die rechtsbündigen Schalter jeder
Menüzeile **unerreichbar**. In CSS ist das nicht zu lösen: Rechtsöffnung
verschiebt das Problem nur an den anderen Rand (im Chart-Editor läge die linke
Kante bei −97 px), und ein fest breites Popover an einem beweglichen Anker
lässt sich ohne Kenntnis der Ankerposition nicht klemmen.
`static/js/menu-popover-clamp.js` misst deshalb beim Öffnen und schiebt
waagerecht zurück — gegen `documentElement.clientWidth`, denn das ist die Box,
an der `overflow-x:hidden` abschneidet. Verschoben wird über `left`, nicht
`transform`: `transform` gehört der Öffnen-Animation. Dasselbe Vorgehen wie
`reposition()` im Kalender-Popover. Dazu `max-height:70vh` mit Innenscroll
unter 640 px — das Menü ist 621 px hoch und reichte sonst unter die Falz.

Einen „Jetzt"-Knopf gibt es nicht mehr: er belegte dauerhaft Platz und war die
meiste Zeit deaktiviert. Seine Funktion liegt als Zweitfunktion auf der schon
aktiven Zeitraum-Stufe — ein erneuter Klick auf „Tag" springt zurück auf
heute, auf „Monat" in den laufenden Monat. Dieselbe Geste kennt das
Energiedashboard seit jeher (`setRange()` in `energiedashboard.js`); ein
`title` weist darauf hin, solange es etwas zu tun gibt.
`tests/test_entity_chart_toolbar.py` hält beides fest, einschließlich der
Vorlage im Energiedashboard.

### Durchschnittslinie

Eine `markLine` je Serie beim Durchschnitt der **gezeichneten** Werte, eigene
Zeile im Optionen-Menü (nicht an „Statistik in Legende" gekoppelt: die Legende
nennt die Zahl, die Linie zeigt ihre Lage — das eine will man oft ohne das
andere, und eine Zeile, die nur zusammen mit einer anderen Option erscheint,
wäre ohne sie unauffindbar).

Zwei Entscheidungen, die man beim Nachbauen sonst anders träfe:

- **Der Wert wird selbst gerechnet, nicht per `markLine: {type: 'average'}`.**
  ECharts rechnet dort über die Daten, die die Serie gerade führt — bei
  `dataZoom` mit `filterMode: 'filter'` also über den sichtbaren Ausschnitt.
  Die Linie änderte damit ihre Bedeutung beim Zoomen, und zwar abhängig von
  „Dynamische Y-Achse", die den filterMode bestimmt. Ein fester `yAxis`-Wert
  kann das nicht.
- **Die Quelle der Werte ist je Seite eine andere, und das ist Absicht.** Die
  Entitätsseite zeichnet ihre Punkte unverändert — dort ist der Durchschnitt
  derselbe wie in der Legende. Der Chart-Editor zeichnet `resamplePoints()`,
  und die fassen Zähler und Schalter per SUMME zusammen; ein Durchschnitt über
  die Rohpunkte läge dort um den Faktor der Bucketbreite unter den
  gezeichneten Balken und klebte sichtbar an der Nulllinie. Legende und Linie
  können dort deshalb verschiedene Zahlen nennen — sie beantworten dann auch
  verschiedene Fragen.

Die Linie sitzt nur auf der Hauptserie: bei aktivem Vergleich wäre eine zweite
Linie für die Vorperiode eine Aussage, die in der Legende nirgends steht. Im
Zeitstrahl gibt es sie gar nicht — dort ist die y-Achse keine Werteachse.

Gespeichert wird sie wie die übrigen Optionen: pro Entität in
`entities.chart_options`, pro Chart in `saved_charts.average_line`. Die
Dashboard-Kachel zeichnet sie mit — sie liest den Wert über
`data-average-line` aus dem angehefteten Chart, damit dasselbe Chart nicht
zweimal verschieden aussieht. Dort ohne Einheit im Text, wie auch die eigenen
Wert-Beschriftungen der Kachel.

Die Kachel mittelt dabei nicht über `lineData`: der Linien-Zweig hängt bis
`window_end` einen Halte-Punkt an, der den letzten Wert wiederholt und ihn
damit doppelt zählen würde. Deshalb sammelt jeder der beiden Zweige seine
Werte in `averageValues` selbst.

### Markierungsquote der Ausreißer-Erkennung

Unter dem Schwellwert-Feld (Entität konfigurieren) steht, welchen Anteil der
Werte die eingestellte Schwelle markiert — die Zahl hinschreiben, während sie
gewählt wird, statt hinterher davor zu warnen.

Zwei Eigenheiten, die man beim Nachbauen anders träfe:

- **Die Quote erscheint nur, wenn sie zu GENAU dieser Schwelle gehört.** Der
  Cache der Bereinigungsseite (`cleanup_alltime_stats`) hält je Entität eine
  Zahl; wer die Schwelle ändert, hätte sonst die Quote der alten daneben
  stehen. Deshalb speichert der Eintrag jetzt mit, mit welcher Schwelle
  gezählt wurde; passt sie nicht (oder fehlt sie, bei Einträgen von vor dieser
  Änderung), steht dort „Jetzt prüfen" statt einer Zahl.
- **Der Vollscan läuft auf Klick, nie beim Seitenaufbau.** Gemessen 7,1
  Sekunden für eine Entität mit 3,8 Mio. Rohwerten — das gehört in keinen
  Request-Pfad. Kein Hintergrundlauf über alle Entitäten: gebraucht wird die
  Zahl genau dann, wenn jemand die Schwelle einstellt.

Balken und Farben kommen von `.usage-bar-track`/`.usage-bar-fill`, demselben
Baustein wie der Host-Speicherplatz in `housekeeping.html`.

## Mobile Listenansicht

Unter 640 px arbeiten zwei Module zusammen, die eine neue Seite nicht
einbinden, sondern nur bedienen muss.

**`table-cards.js` — aus jeder Tabellenzeile wird eine Karte.** Jeder Wert
trägt seine Spaltenüberschrift als Etikett bei sich (`data-label` je `td`),
die Kopfzeile verschwindet, und `app.css` macht daraus unter 640 px die
Kartenform. Das gilt automatisch für jedes `table.dt` — eine neue Tabelle muss
dafür nichts tun. Ausgenommen erkennt das Modul selbst: `colspan`/`rowspan`
(in dieser App heißt das Vergleichstabelle, deren Raster die Aussage ist),
mehrstufige Köpfe, weniger als drei Spalten, Chart-Legenden. Wer eine Tabelle
bewusst herausnehmen will, setzt `data-cards="off"`.

Die Karte startet **eingeklappt**: sichtbar bleiben Überschrift, ein Leitwert
und die Bedienspalten davor (Favoriten-Stern, Auswahlkästchen). Leitwert ist
die erste beschriftete Spalte nach dem Namen — die Listen dieser App stellen
die wichtigste Angabe ohnehin nach vorn. Trifft das für eine Tabelle nicht zu,
nennt sie ihre Spalte selbst: `data-card-lead="Letzter Wert"` am `<table>`,
mit dem Spaltennamen aus der Kopfzeile. Greift der Name ins Leere, gilt wieder
die erste Spalte. Eingeklappt wird ab zwei versteckten Werten.

Ebenfalls von hier: der Scroll-Hinweis an den Rändern von `.tbl-wrap` hängt an
der Klasse `.is-scrollable`, die das Modul aus `scrollWidth` gegen
`clientWidth` setzt — ob ein Container überläuft, weiß nur das Layout.

Wohin das Sortiermenü gehört, entscheidet `sortHost()` in drei Stufen: ein
`[data-sort-host]` im selben `<section>` gewinnt, sonst das Ansicht-Menü (nur
bei genau einer Listentabelle auf der Seite), sonst die `.tbl-wrap` über der
Tabelle. Der erste Fall ist für Seiten, die über ihrer Liste schon eine
Bedienzeile haben — auf Housekeeping steht in „Inaktive Entitäten" eine
Zeitraum-Auswahl, und Sortieren gehört in dieselbe Zeile statt in eine zweite
darunter. **Das Ziel muss außerhalb des per htmx getauschten Bereichs liegen:**
dort überlebt es den Austausch, während das Menü selbst neu gebaut wird (siehe
`verwaisteSortmenues()`). Läge es darin, löschte jeder Wechsel des
Schwellwerts genau das Formular, das ihn auslöst.

Der Pager steht unter 640 px linksbündig — an derselben Kante wie
Überschriften, Zählstand und die Zeilenkarten selbst. `justify-content` steht
in acht Templates als Inline-Stil (`space-between`, für den Schreibtisch
richtig), deshalb setzt `app.css` es mit `!important` statt in acht Vorlagen.

**`list-settings-menu.js` — die Werkzeugleiste wird ein Menü.** Filter,
Spaltenauswahl und Sortierung stehen auf schmalen Bildschirmen zusammen in
einem Menü „Ansicht" neben der Suche statt in mehreren Reihen darüber. Eine
Seite bekommt das, wenn ihre Werkzeugleiste `#controls` oder `.card-browser`
heißt **und** ein eigenes Suchfeld als direktes Kind trägt. Das Suchfeld ist
die Bedingung, nicht Zierde: auf der Bereinigungs-Seite heißt `#controls` ein
Container, in dem die Zeitraum-Leiste steckt — die Hauptbedienung der Seite,
die nicht in ein Menü gehört.

Zwei Eigenschaften sind beim Weiterbauen wichtig:

- **Verschoben, nicht nachgebaut.** Die Bedienelemente wandern als dieselben
  DOM-Knoten in ein Popover *innerhalb* der Leiste. Feldnamen, `hx-include`
  und die Auswertung im Server bleiben dadurch unberührt; es gibt keine zweite
  Fassung des Formulars, die auseinanderlaufen könnte. Auf breiten
  Bildschirmen wandern sie zurück, ein Kommentarknoten je Element merkt sich
  den Platz.
- **Kein `MutationObserver`.** Ein Beobachter auf dem Dokument schaukelt sich
  mit dem in `table-cards.js` auf — dieses Modul verschiebt Elemente, das
  weckt den anderen, dessen Arbeit wiederum dieses. Die Werkzeugleiste steht
  beim Laden da und wird von htmx nie ersetzt; `DOMContentLoaded` und
  `htmx:afterSwap` genügen.

Die Dropdowns, die die Bedienelemente mitbringen, klappen im Menü **an Ort und
Stelle** auf statt als Popover darüber — ein Popover im Popover ist auf 375 px
nicht unterzubringen. Dafür war kein Eingriff in `dd-picker.js` nötig: das
Aufklappen hängt dort an der Klasse `.open`, nicht an der Positionierung.

## Rückmeldung für lange Aktionen

Drei Anzeigen, jede eine Antwort auf eine andere Frage: **der Knopf**, **der
Balken**, **die Glocke**. Benannt nach dem, was man sieht, und ausdrücklich
nicht durchnummeriert — zwischen Knopf und Balken stand zeitweise eine
vierte Anzeige, und eine Nummer, die sich bei jedem Zu- oder Abgang
verschiebt, taugt nicht als Name für etwas, über das man später noch reden
will. Der serverseitige Unterbau (`progress.py`, wer sich anmeldet und warum)
steht in [architecture.md](architecture.md); hier steht, was im Browser
passiert.

**Der Knopf — sagt, dass er arbeitet.** `hx-disabled-elt="this"` plus
die `.btn`-Regeln in `app.css`: gedimmt, `cursor:progress`, ein kleiner
rotierender Ring hinter der Beschriftung. Beantwortet „ist mein Klick
angekommen?" und verhindert zugleich den zweiten Klick.

Dauert die Anfrage länger als drei Sekunden, hängt `js/btn-elapsed.js` eine
mitlaufende Uhr in den Knopf (`.btn-elapsed`, Tabellenziffern, damit er beim
Weiterzählen nicht die Breite wechselt). Erst ab dieser Schwelle, weil eine
Sekundenanzeige darunter keine Information ist, sondern Unruhe — dass etwas
läuft, sagen Ring und Dimmung bereits; die Uhr sagt „noch, seit einer
Minute". Sie hängt an keiner Vorlage, sondern nur daran, ob der Auslöser ein
`.btn` ist: Damit gibt es keine Liste von Knöpfen, die beim Anlegen eines
neuen jemand vergessen könnte. Das Skript lädt `_topnav.html` zentral, wie
`topnav-activity.js`. `aria-hidden` liegt auf der Uhr — eine im Sekundentakt
vorgelesene Zahl wäre eine Dauerunterbrechung, und dass der Knopf beschäftigt
ist, sagt Screenreadern sein `disabled`.

Drei Fallen:

- **Der Aufhänger ist nicht nur `.htmx-request`.** htmx setzt diese Klasse auf
  das auslösende Element — aber nur, solange kein `hx-indicator` gesetzt ist;
  sonst wandert sie an das dort benannte Element, und der Knopf bleibt ohne
  jeden Zustand. Gemessen sah „Index prüfen" während seiner Anfrage deshalb
  aus wie ein dauerhaft gesperrter Knopf. Deshalb zeichnen die Regeln
  zusätzlich `[data-disabled-by-htmx]` — das Attribut, das `hx-disabled-elt`
  unabhängig davon setzt und das „gesperrt, **weil** etwas läuft" bedeutet.
- Die Regel `.btn:disabled{opacity:.4}` muss **vor** den laufenden Varianten
  mit `opacity:.7` stehen — sonst ist ein laufender Knopf blasser als ein
  gesperrter.
- Das Deaktivieren passiert in htmx *nach* dem Einsammeln der
  Formularwerte (`htmx:beforeRequest` läuft davor), ein `disabled`-Feld
  verschluckt seinen Wert also nicht. Das ist im minifizierten htmx
  nachgeprüft, nicht angenommen.

> Kurzzeitig stand diese Uhr in einem eigenen Chip **neben** fünf dieser
> Knöpfe (`_busy.html`, `busy-chip.js`). Der Chip ist wieder entfernt: Er
> sagte im Kern dasselbe wie der Knopf, hielt daneben dauerhaft Platz frei —
> und nahm dem Knopf über seinen `hx-indicator` genau den Laufzustand weg, den
> er ergänzen sollte. Geblieben ist der eine Teil, den der Knopf allein nicht
> konnte: die Sekunden. Wer eine Anzeige neben einem Knopf erwägt, hat damit
> den Präzedenzfall — erst prüfen, ob sie nicht in den Knopf gehört.

**Der Balken — sagt, wie weit.** `_job_progress.html`, gefüllt aus
`JobProgress.snapshot()`. Der Container pollt sich alle 500 ms per
`hx-swap="outerHTML"` selbst; der Endpunkt liefert entweder wieder die
Anzeige oder — sobald der Auftrag durch ist — das Ergebnis **ohne**
`hx-trigger`, wodurch das Polling von selbst endet, ohne mitzuzählen. Drei
Darstellungen, je nach Ehrlichkeit der Zahlen: Gesamtzahl bekannt → „X von Y
Einheiten"; nur ein Zähler → „X Einheiten bisher" mit leerem Balken; keins von
beidem → „läuft…". `aria-live="polite"` sitzt am Textabsatz, nicht am
Container: Letzterer wird zweimal je Sekunde ersetzt, eine Live-Region darauf
würde je nach Screenreader gar nicht oder unablässig vorgelesen.

**Die Glocke — sagt es überall.** Seit die Aufträge im Hintergrund
laufen, überleben sie den Seitenwechsel; die Kopfleiste ist das einzige
Bauteil, das auf jeder Seite steht. `js/topnav-activity.js` hängt an
`_topnav.html` selbst (wie Alpine), nicht an einer Liste von Seiten, und holt
`/notices/activity` alle 2 Sekunden, solange etwas läuft, sonst alle 8
(`TAKT_AKTIV_MS`/`TAKT_RUHE_MS`). Der Abschnitt „Läuft gerade"
(`_activity_block.html`) steht über dem Meldungskopf und in der Akzentfarbe,
nicht in den Schweregrad-Punkten darunter: Ein laufender Vorgang ist kein
Problem und soll auch nicht wie eines aussehen.

Das zweite Abzeichen links an der Glocke ist bewusst getrennt vom roten
rechts — das rote zählt Probleme. Es ist immer eine Pille; die Ziffer
erscheint erst ab zwei gleichzeitigen Vorgängen, weil sich die meisten dieser
Aufträge über `exclusive()` ohnehin serialisieren und eine dauerhafte „1"
eine Ziffer ohne Information wäre. `.notice-badge.is-activity` setzt
deshalb nur Seite und Farbe (`right:auto; left:2px;`), keine eigene Geometrie
— ein Test hält das fest, damit die beiden Abzeichen nicht auseinanderlaufen.

Geblinkt wird nur bei **Änderung**: Das Skript vergleicht die `data-job-id`
der Einträge und blitzt dreimal (`animation:activity-blink .34s steps(1,end) 3`),
wenn eine dazukommt oder verschwindet — beim ersten Abruf nach dem
Seitenaufbau absichtlich nicht. Dauerblinken verbietet sich doppelt: WCAG
lässt höchstens drei Blitze je Sekunde zu (Anfallsrisiko), und alles, was
länger als fünf Sekunden blinkt, bräuchte eine eigene Stopp-Möglichkeit — ein
Symcon-Import läuft Minuten. `prefers-reduced-motion` schaltet die Animation
ganz ab — das Abzeichen selbst bleibt ruhig stehen, solange etwas läuft, und
die Liste im Panel sagt ohnehin, was.

> **Falle beim Ein-/Ausblenden.** Eine Autor-Regel `display:flex` schlägt das
> `hidden`-Attribut immer, unabhängig von der Reihenfolge (die UA-Regel
> `[hidden]{display:none}` hat die niedrigste Herkunft). Wer ein Element mit
> `hidden` umschaltet und ihm zugleich ein `display` gibt, braucht einen
> ausdrücklichen `[hidden]{display:none}`-Riegel daneben. `app.css`
> dokumentiert das an Ort und Stelle bei `.notice-snooze-options[hidden]` —
> die Falle ist in dieser App schon zweimal zugeschnappt.

## Wiederkehrende Muster, die neue Seiten übernehmen sollten

- **`base.html` erben und `{{ app_root }}` vor jeden Pfad** — beides oben
  ausführlich; es sind die zwei Punkte, an denen eine neue Seite unter Ingress
  am ehesten stillschweigend kaputtgeht.
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
- **`_entity_tabs.html`**: die Reiterzeile der drei Entitätsseiten (Verlauf,
  Werte bearbeiten, Konfiguration). Sie borgt bewusst das Idiom der Topnav —
  Grundlinie, aktiver Unterstrich, `aria-current="page"` —, und alle drei
  Seiten tragen darüber denselben Kopf (`.h1-row` mit Favoriten-Stern,
  darunter Entity-ID, Typ, Weg zur Liste), sodass ein Reiterwechsel nur den
  Inhalt austauscht. Vorher steckten zwei der drei als Navigationseinträge im
  Optionen-Menü der Verlaufsseite; das Menü enthält jetzt nur noch Aktionen.
  Treffen wie auf „Werte bearbeiten“ zwei Reiterzeilen aufeinander, ist die
  innere zu weichen Pillen abgestuft (`--accent-line-soft`, kleinere Schrift,
  keine Grundlinie), damit die Rangfolge auch ohne Farbwissen lesbar bleibt.
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
- **`hx-disabled-elt="this"` und `_job_progress.html`**: die beiden Bausteine
  für Aktionen, die spürbar dauern — der Knopf allein, wo es keine ehrliche
  Gesamtzahl gibt, der Balken, wo es eine gibt. Neue lange Aktionen sollen
  diese benutzen statt eine eigene Anzeige daneben zu stellen; was
  serverseitig dazugehört, steht oben unter „Rückmeldung für lange Aktionen".
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
