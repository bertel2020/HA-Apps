# Frontend architecture

*[Deutsche Version](../frontend.md)*

No build step, no bundler, no npm. `static/vendor/` contains unchanged copies
of Alpine.js, htmx and ECharts; all app-owned scripts live uncompiled under
`static/js/`.

## Rendering model

Three layers work together, depending on the interaction needs of the
respective page:

1. **Jinja2 (server-side rendering).** `Jinja2Templates` (`app/main.py`),
   templates under `app/templates/`. Central custom Jinja filters:
   `format_int`, `format_value` (`templates.env.filters[...]`, see
   `formatting.py`) — number formatting once in Python instead of duplicated at
   every template location.
2. **htmx** for partial reloads without JS of its own: forms post directly
   (`hx-post`), the server answers with an HTML fragment
   (`_settings_*_form.html` pattern), `hx-target`/`hx-swap` replaces exactly the
   affected DOM section. Polling (e.g. diagnostic tools while active) via
   `hx-trigger="every Ns"`.
3. **Alpine.js** for purely client-side, non-persistent state: dropdown pickers,
   form visibility, and the two most complex editors of the app (chart and
   table editor) — an HTML form is not enough there, because users are meant to
   add, rearrange and freely add rows/columns and see a live preview before
   saving.

Rule of thumb in the code: **one form, one value, immediate save** → htmx.
**Several related, changeable elements with a live preview before saving** →
Alpine.js component with its own `x-data` state, sent to the server only on the
explicit Save click.

## Page frame (`base.html`) and URL prefix

All 23 full-page templates inherit their frame from `base.html` — doctype,
`<head>`, stylesheet reference, topnav. Previously each page assembled its own
head, and largely the same: doctype, `<html>`, `<meta charset>`, viewport, font
link and `<body>` were byte-identical across all 23. (The font link has since
been dropped entirely — since ZG-14 the fonts are local and bound in
`app.css`, see "Static assets".) A fix to the head thus had to be maintained 23
times — and the one line that was *not* identical diverged into four spellings
(see below).

The frame offers five blocks, all read off the existing code instead of created
in advance:

| Block | Purpose |
| --- | --- |
| `title` | The **whole** title, not just its variable part: 21 pages follow "Zeitarchiv — X", two do not |
| `root_vars` | Additional `:root` variables; only `entities.html` and `dashboard_detail.html` set `--dashboard-row-height` besides `--font-scale` |
| `page_css` | The `<link>` to the page-local stylesheet (`static/css/pages/<page>.css`) |
| `topnav` | Only `_energiedashboard_report.html` overrides it (empty) |
| `content` | The entire page body **including the `<script>` tags at the end** |

There is deliberately no `page_js` block: no template has a `<script>` in the
`<head>`. The tags sit at the end of the body and travel along in the `content`
block — including the reference to the page-local script
(`static/js/pages/<page>.js`, see below). The load order thus remains exactly
the previous one.

**Every path in the HTML begins with `{{ app_root }}`.** The variable comes from
a context processor (`_app_root_context` in `main.py`) that supplies it for
*every* `TemplateResponse` — under Home Assistant from the `X-Ingress-Path`
header, locally from `root_path`, otherwise empty. It is absolute and thus
independent of how deep a page hangs in the URL.

This is exactly where the app repeatedly failed before: pages wrote their
prefix themselves. The stylesheet reference alone stood in four versions in the
tree — `static/css/app.css`, `../static/…`, `{{ base }}/static/…` and
`{{ app_root }}/static/…` — and a new page at a new nesting depth got the wrong
one. The former `base` variable no longer exists; it appears in no template and
no route anymore, and a test enforces that. **For a new page this means:
`{{ app_root }}` before every path, nothing else.**

Both are secured by `tests/test_base_template.py` (the frame keeps its promises
and no page repeats them) and `tests/test_ingress_prefix.py` — the latter checks
the *resolution* of every asset reference by resolving it like a browser would,
via `urljoin` against the header path, not its spelling. The test thereby
survives any further change of notation.

## Static assets

`app.mount("/static", ...)` serves `app/static/`, with
`Cache-Control: public, max-age=31536000, immutable`. This long cache is only
safe because every reference carries a parameter that changes with the file.

Addressing is via **`{{ asset('js/pages/statistik.js') }}`** — a Jinja global
from `main.py` that prepends the Ingress prefix and appends the cache buster. A
line only names the path below `static/`; it can thus not forget the rest.

Since ZG-05 the buster is the **content hash of the individual file** (blake2b,
8 hex characters). Before, there were three numbers — `css_v`, `js_v`,
`vendor_v` —, each the youngest mtime over a whole folder. That went wrong
twice: Git does not store mtimes, a CI checkout sets all files to the checkout
time and `COPY` takes them over, so after **every** release everything was new
— on the energy dashboard 1,317 KiB per user, of which 1.0 MB ECharts that had
not changed for months. And a change to one of the 30 JS files invalidated the
cache of all thirty. Both are recorded in `tests/test_asset_versions.py`.

What only one page needs lives as `static/css/pages/<page>.css` next to
`app.css` and is linked in the `page_css` block; the same applies to
page-local JavaScript as `static/js/pages/<page>.js`, linked at the end of the
body between the shared scripts. As a `<style>` or `<script>` block in the
template, these roughly 477 KB traveled along again on every page visit instead
of lying once in the browser cache. Only what Jinja needs stays inline in the
template — start values and server data, nothing that *does* something;
`tests/test_page_scripts.py` enforces that. Conventions on this (file name
follows the template, when something belongs in `app.css`) are in
[`app/static/css/README.md`](../../app/static/css/README.md), the design system
document.

`static/fonts/` contains IBM Plex Sans and IBM Plex Mono as WOFF2 (10 files,
164 KB, latin and latin-ext character sets). Until ZG-14 both came from
`fonts.googleapis.com` — in networks without internet access or with DNS
filtering, that cost a timeout on every page load before the fallback font
kicked in. They are bound via `@font-face` at the top of `app.css`, with paths
**relative to the stylesheet** (`url(../fonts/…)`): a browser evaluates `url()`
against the URL of the CSS file, which already carries the Ingress prefix — an
absolute `/static/fonts/…` would be a 404 under Ingress, and `{{ app_root }}`
does not help because CSS does not pass through Jinja. The file names are to be
treated as immutable: `/static/*` carries `Cache-Control: immutable` for over a
year, and `url()` cannot carry a `?v=` cache buster. A font update therefore
gets a **new file name**.

## Hint texts: three roles, one info button

Explanatory help texts took up a considerable part of the page on the phone,
although they are read once and then no longer needed. They therefore sit
behind an info button and unfold on demand — inline, not as a popover: no
overlay, no position calculation, and with `dd-picker` there is already a
popover mechanism.

**The default state depends on the width:** unfolded at the desk, closed below
700 px. Until 0.88.0 it was the same everywhere (always closed) — but the space
pressure that makes folding away necessary exists only on narrow devices; on
the entity configuration it was 557 of 2,806 px, at the desk the same text is
negligible. The info button thus behaves like the two other collapsible blocks
of the app (logging card on the log page, "How … works" guides on import),
right down to the same breakpoint. The button remains usable at both widths; a
width change resets to the default of the new width.

A prerequisite was a distinction that did not exist before: all hints carried
the same class `.hint`, although three different things were in it. The role
stands **in addition** to the context class (`hint`, `tbl-hint`,
`settings-compact-hint`, `settings-section-description`), because both axes are
independent — the context determines size and spacing, the role whether the
text may fold away:

| Role | Meaning |
| --- | --- |
| *(none)* | Explanation — may go behind the info button |
| `hint-warn` | Warning — stays visible; `hint-warn-strong` gives the three sentences with irreversible loss an edge in `--warning` |
| `hint-status` | Data, empty and loading state — is content, not explanation |

`settings-section-description` — the sentence between section heading and
content — was the latest addition: the same kind of text, just one level
higher. It keeps its own class because it looks different (`--ink-muted`, 13 px
instead of `--ink-faint`, 12.5 px), and therefore appears both in the selector
of `hint-toggle.js` and in the context classes of `test_hint_roles.py`.

It is built with the macros from `_hints.html`
(`{% from "_hints.html" import hint_button, hint_body %}`), folded by
`static/js/hint-toggle.js`, which the page must include. The button finds its
hint via its **position** in the document, not via `aria-controls`: some of the
fields sit in Alpine templates that are rolled out anew per row, fixed ids
would appear multiple times in the document there. Folding happens via
delegation on `document`, because htmx replaces the forms completely.

The hit area is 44 × 44 px with a 16 px icon, via a pseudo-element and offset
6 px upward — centered, the button would lose exactly the lower 6 px to the
control below it. Secured by `tests/test_hint_roles.py` (the roles are markup,
not styling) and `tests/test_hint_toggle.py`.

**Rule of thumb for which text may unfold:** it explains how to *operate*
exactly one element. Whoever explains how to *read* the display stays put.

## Comparison tables (`table-compute.js`)

Shared calculation logic between the full table editor (`table_editor.html`)
and the compact dashboard tile view (`dashboard-tiles.js`) — **one** module, so
that a fix in one place is not forgotten in the other. Works on plain index
arrays (columns, rows), not on Alpine's reactive UID state.

- Calls `/api/query-table` **once** for all visible columns and required
  entities. The endpoint shares a request-local read cache across all periods
  and returns only scalar aggregates instead of complete point series.
  Dashboard tiles pass only the actually visible table area.
- Formula rows: a small hand-written expression parser (`evalFormula()`,
  supports `+ - * / ()` and row letters) instead of `eval()`/`Function()` —
  deliberately, although formulas only come from the own database (no external
  attack vector), because a hand-built parser remains the cleaner choice for
  such simple expressions.
- The server computes, per entity and period, the aggregates `auto`/`avg`/
  `min`/`max`/`sum`. Groups, formulas and decimal places per column remain
  client-side presentation logic; only the structure is still stored (see
  [data-model.md](data-model.md)).
- Presentation options live exclusively in the table's `style_json` and apply
  identically to the full view and the dashboard tile: section names on divider
  rows, highlighting of formula rows, frozen label column/header row, manual
  column widths (`_TableColumnBody.width`/`style.label_col_width`), header/value
  alignment, equal-width value columns, set-apart comparison columns,
  percentage deviation below the current value, spelled-out missing values as
  well as show/hide-able, optionally smaller or aligned units and aligned
  decimal places. Old tables keep their previous presentation through
  conservative defaults.
- **Layout lesson (see the `table_editor.html` comments):** rendering the row
  letters (A/B/C) as a *separate* table next to the main table instead of as a
  column within it sounds cleaner but leads to row-height drift between two
  independent `<table>` elements (border rounding, badge vs. text line height).
  The robust solution: the letter column stays a real first table column (the
  browser thereby guarantees pixel-exact row heights by itself); it looks
  visually "set apart" instead through targeted `:not(...)` selector exceptions
  for header highlighting, not through physical separation in the DOM.
- **Sticky header lesson:** `position:sticky` on `<thead>`/`<tr>` is not
  reliably supported by Safari/WebKit — in particular the corner cell (header
  row × frozen first column) remains ineffective there. Only `position:sticky`
  on each `<th>` individually is robust; with a two-level header the second row
  additionally needs a `top` equal to the height of the first row, otherwise
  both overlap when scrolling. This height varies with density/font size and
  is therefore measured via JS and set as the custom property
  `--tbl-group-header-h` (`syncLetterPositions()` in the editor,
  `renderTableTile()` in `dashboard-tiles.js`).
- **The tile's label column does not break within a word:** value cells are
  `nowrap` (minimum width = content). If the label had `word-break:
  break-word` (minimum width one character), the auto layout would squeeze
  this column alone into a letter strip when the tile is too narrow.
  `th:first-child, td:first-child` therefore set `word-break:normal`; the table
  scrolls horizontally instead (`.dtile-table-preview`).
- **Equal-width value columns (`style.equal_value_cols`):** a pure `width:1%`
  CSS trick does NOT reliably distribute space evenly with `table-layout:auto`
  as soon as number lengths differ strongly between columns (daily vs. annual
  sum) — the narrowest column stays bound to its minimum content. Robust is
  `table-layout:fixed` together with a `<colgroup>`: only the label column gets
  an explicit `<col>` width, all other `<col>` elements without their own width
  share the rest equally according to the specification — engine-independent,
  unlike widths via cells of the "first row" with fixed layout.

## Multilingual support (German ↔ English)

German is the source text, every further language a catalog
(`app/i18n/en.json`, sections `app` and `js`). **The German text itself is the
key** (as with gettext): whatever is missing in the catalog appears unchanged in
German, the app stays usable in every intermediate state. The language is in
the setting `language` (`auto`/`de`/`en`, default `de`; Settings → Appearance);
`auto` follows `Accept-Language`.

| Where | Call | Note |
|---|---|---|
| Template | `{{ _("Text {n}", n=value) }}` | The key is HTML source text (`&hellip;`, `<strong>`), the result is `Markup`; inserted values are escaped. |
| Script / Alpine expression | `t('Text {n}', {n: value})` | `static/js/i18n.js`; the JS catalog is delivered inline as `window.ZA_CATALOG` only for non-German. |
| Python, at request time | `tr("Text {n}", n=value)` | reads the language of the running request (`i18n.current_language`, set by an app-wide dependency before every route); without a request the background language (see below). |
| Python, constant | `N_("Text")` | returns a `Lazy`: a `str` with the German wording that is translated only on display (`{{ label }}`). For label lists created at import. |

Rules that have proven themselves: whole sentences with placeholders instead of
glued-together parts; plural forms as two complete keys (`… Zeile` / `…
Zeilen`) instead of `Zeile{{ 'n' if … }}`; log lines stay German.
`tests/test_i18n.py` checks that every marked text has a catalog entry, that no
entry is orphaned and that the placeholders of the translation match those of
the German text. Source text tests that read templates go via
`template_text()`/`german()` (`tests/_paths.py`), which rebuilds the `_()` calls
back into plain German text.

Not yet translated: log lines and the ECharts' own labels. The guide and the
READMEs are available in English under `docs/en/`.

### Background tasks and stored texts

**Without a request** (the schedulers for backup, retention, automatic
cleanup) `current_language` is not set; `tr()` and `_()` then ask
`i18n.active_language()`. The background language is the fixed language
setting, with "Automatic" the language a browser asked for last (setting
`language_last_seen`, written only on a change and only for a matching
`Accept-Language` — a script without a header does not overwrite it), and
German if there has been no request yet. `i18n.set_background_language()`
registers this at startup.

**Stored free texts** (errors in activity and job tables, the `errors` of the
import reports, title/detail/meta of a mute) exist as finished text, in the
language that applied when they were written. The stored format stays
unchanged; on display `i18n.retranslate()` (Jinja filter `retranslate`) brings
them into the language of the request: a text that matches a catalog entry —
German or translated, also with placeholder values — is rebuilt via the German
key. This also covers older entries. Raw exception texts (`{exc}`) and own
names match no entry and stay as they are. With several matching patterns the
one with the most fixed wording wins; pure placeholder patterns (`{a}: {b}`)
do not count. A new display place for stored text: apply the filter.

### Number, date and currency format

Format and currency do **not** depend on the language — a German user with an
English interface still pays in euros, and `en-GB` and `en-US` write dates and
clocks differently. Two settings of their own (Settings → Appearance), both with
the value `auto`:

| Setting | Values | `auto` |
|---|---|---|
| `regional_format` (`app/formats.py`) | `de-DE`, `en-GB`, `en-US` | region from `Accept-Language` (`en-GB`/`en-AU` → British, `en-US`/`en-CA` → American, `de-*` → German); without a match by the interface language (`en` → `en-GB`) |
| `currency` (`app/currency.py`) | `EUR`, `GBP`, `USD`, `CHF` | `currency` from `http://supervisor/core/api/config`, 3 s timeout, a hit cached for 1 h and a failure for 5 min; without a supervisor, without an answer or for any other currency `EUR` |

The server resolves both **once per request** and passes them on to both sides,
so that a tile and a chart next to each other never write differently:

- **Python:** ContextVars `formats.current_format` and `currency.current_currency`,
  set by the same app-wide dependency as the language (`i18n.dependencies`).
  `format_int`/`format_value`/`format_size` (`formatting.py`),
  `formats.format_date`/`format_datetime`/`format_clock`/`format_day_month` and
  the Jinja filter `format_money` read them. `strftime("%d.%m.%Y")` and a fixed
  " €" do not belong in display code.
- **Browser:** `<html data-format="en-GB" data-currency='{…}'>` (JSON with symbol,
  position, subunit) → `window.ZA_FORMAT` and `window.ZA_CURRENCY`
  (`static/js/i18n.js`). All `Intl`/`toLocale…` calls and `NumberFormat` take
  `ZA_FORMAT`; amounts are written by `fmtCurrency()` from `ZA_CURRENCY`.
  Deliberately no `Intl` currency format: `en-GB` would write USD as "US$", and
  server and browser would differ.
- **Notation:** German has the symbol after the amount (`2,52 €`), English before
  it (`€2.52`, `CHF 2.52`); `en-US` uses a 12-hour clock, the others 24 hours.
  Negative amounts: the minus in front of everything (`-£1,234.50`).
- **Untouched:** machine formats (ISO dates, CSV export, file names, the `strptime`
  pattern in the CSV import).
- **The currency converts nothing:** prices exist without a currency as "unit per
  kWh"; the setting only changes symbol, position and the subunit in the input
  field and hints (Ct/p/¢/Rp, in text cents/pence/rappen).

Both settings have a route of their own (`POST /settings/regional-format`,
`POST /settings/currency`, answer `204` with `HX-Refresh`, an invalid value
`400`), because the whole page changes on a switch. A new format or currency: an
entry in `FORMATS` or `CURRENCIES` including its label in
`FORMAT_CHOICES`/`CURRENCY_CHOICES` and in the catalog. `tests/test_formats.py`
and `tests/test_currency.py` cover resolution, notation and pages; one test
catches a fixed `'de-DE'` in scripts and a fixed "€" in scripts and the report.

## Theming

CSS variables (`--bg`, `--surface`, `--ink`, `--accent-line`, `--warning`,
`--danger`, …) in `static/css/app.css`, switched via
`data-color-scheme`/`data-color-mode` on `<html>`. Three color schemes
(`zeitarchiv`, `home_assistant`, `modern`), each with its own light/dark
variable set. New UI elements must use exclusively these variables, never fixed
hex colors — exception: the Zeitarchiv logo (SVG) deliberately carries fixed
brand colors, independent of the chosen scheme, like a wordmark.

The `modern` scheme separates the roles deliberately: cool slate tones form
background, surfaces and borders; cobalt is the primary UI color for
navigation, focus and selection; teal remains the data and chart accent. New
components must not blur these roles through component-specific fixed colors.
Warnings and errors use the global `--warning*`/`--danger*` tokens.

`--font-scale` (CSS variable, from **Settings → Appearance**) scales practically
every `font-size` in `app.css` via `calc(Npx * var(--font-scale, 1))` — new
components must adopt this pattern, otherwise they ignore the font size
setting.

`--font-mono` stands in this app for **machine-readable**: entity IDs,
timestamps, raw values, code snippets — everything that one copies or compares
character by character. Labels, explanatory texts and even self-typed display
names get `--font-display` instead, even if numbers appear in them; if the
numbers are not meant to jump when scrolling, `font-variant-numeric:
tabular-nums` achieves that without the terminal impression of a monospace
font. The distinction only holds as long as it stays consistent: if mono is
also on running text, it no longer says anything.

Two pitfalls: form fields do not inherit `font-family` — without an explicit
specification they fall back to the browser default font, not to that of the
app. And a tooltip on a mono host (`td.mono`, entity ID cells) inherits its
font if it sets none itself.

## Charts (ECharts)

No chart renderer of our own — ECharts instances are filled directly from the
`/api/query[-multi]` responses. Several entities with different units
automatically get separate Y axes.

### Zoom: a magnifier within the period, not a second period

Only the entity detail page (`entity_detail.js`) has a `dataZoom`. This is
possible because the x axis there is pinned hard to the query window anyway
(`min: windowStart`, `max: periodEnd`), so that the chart always shows the
whole chosen period — even where there is no data. The zoom selects a section
*within* these bounds; its full state is by definition the period that is
visible anyway. The rest follows from that: no server request (the points are
loaded), no URL state (`range`/`offset` keep their meaning), no entry in the
entity's chart options. A redraw discards the section by itself, because
`setOption(option, true)` replaces the whole component.

Four settings carry the behavior, and the ECharts default is wrong on every
single one:

| Setting | Value | Why not the default |
| --- | --- | --- |
| `zoomOnMouseWheel` | `'ctrl'` | `true` would let the chart hijack the scroll wheel — whoever scrolls past, zooms. A trackpad pinch natively produces ctrl+wheel, so the gesture comes for free. |
| `moveOnMouseWheel` | `false` | Same argument: the wheel alone belongs to the page. |
| `moveOnMouseMove` | `'ctrl'` | zrender turns a one-finger touch into mouse events; with `true`, on the phone every vertical swipe over the chart would count as a pan, and `preventDefaultMouseMove` (default `true`) would stop the page in the process. There is no Ctrl there — the swipe stays with the page, zooming is done by pinch (own handler, untouched by these switches). |
| `filterMode` | coupled to `dynamicYAxis` | `'filter'` scales the y axis along, `'none'` does not. The page already has a switch for that with "y axis fixed/dynamic" — hard-wired, the zoom would work against it instead of serving it. |

The zoom is offered only where there are more points than pixels:
`points.length > ZOOM_MIN_POINTS` (200). Deliberately a threshold over the
points actually loaded instead of a list of allowed periods — the same period
step needs different answers depending on the entity's reporting rhythm. The
**timeline** is exempt from this and always gets the zoom: there it is not
about convenience but about visibility. A segment is drawn from its start to
its end, and with "Month" at about 900 px, one pixel corresponds to about 48
minutes — every shorter switching event is narrower than a pixel. The number of
segments says nothing about that.

### Dragging out an area (Shift + drag)

A section can also be dragged out directly: hold the Shift key, mark an area
over the time axis with the mouse, release. The gesture was free — plain
dragging was unassigned, panning is on Ctrl+drag, zooming on Ctrl+wheel — and
fits the same rule: holding a key means "I mean the chart". On a touch screen
there is no Shift key, so the swipe stays with the page there without needing a
special rule for it.

Implemented via the **`brush` component**, not via
`toolbox.feature.dataZoom`. The obvious way would have been a `toolbox` with
`show: false` to keep the foreign ECharts icons out — but measured on the
running chart, that is **ineffective**: without a visible toolbox, ECharts does
not create its view, `takeGlobalCursor` with `dataZoomSelect` goes nowhere
(`getModel().getComponent('brush')` stays `null`). Via `brush` directly the
mode is demonstrably armed (`brushOption.brushType` becomes `'lineX'`), and the
selection is ours: from `brushEnd` we build a `dataZoom` with
`startValue`/`endValue` ourselves, after which the area is immediately
cleared — otherwise the rectangle would stay gray on top of the chart.

The icons have to be unsubscribed in **two** places, and the second is not
obvious: `toolbox: []` in the brush configuration only says which brush buttons
the toolbar shows. ECharts creates the toolbar itself anyway — it appeared with
four foreign icons exactly where the section chip sits. Only an additional
`toolbox: {show: false}` in the option keeps it out.

The keyboard state needs three events, not one: `keydown` arms, `keyup`
disarms again — but **not in the middle of dragging**, otherwise a Shift
released too early would leave a half-dragged frame; the state is remembered
and caught up at `mouseup`. Plus `blur` on the window: whoever switches windows
with the key held never gets a `keyup`, and the chart would stay in selection
mode permanently.

**Below the card** there is a permanent line that names both cases — how many
points are drawn and whether anything can be magnified from them
(`get zoomHint()`). It is `hint-status` and therefore must not go behind the
info button: the point count is a data state, not an explanation. Without it,
the gray section chip would remain unexplained. It deliberately sits *outside*
the card — in the card the legend describes the values themselves, whereas the
sentence describes operating the view. The timeline gets its own wording without
a point count: there it is often a handful of segments, and "3 data points —
zoom possible" would contradict the rule that the sentence next to it
establishes.

Reset is via the section chip, which sits **in the chart**: top right,
positioned absolutely in the `.chart-wrap`, visible only when the zoom is
active.

It initially sat in the toolbar and was measured to be the cause of its wrap
there — at 1000 px window width, it needed 1003 of 952 available pixels with it,
843 without it. And it was the element with the rarest occasion: always
visible, only meant during a zoom. (The original argument for the fixed place —
"every control keeps its spot" — became moot with the removal of the "Now"
button: a mostly disabled button does not deserve row width.)

In the chart it covers no data: ECharts only starts drawing at `grid.top` (36
px), and this strip is empty on the right — on the left sits the y axis unit
label. The container `.chart-wrap` exists only so that "top right" refers to
the chart and not to the card including its width-dependent padding. A shadow
sets it apart in case the curve does reach that far. The former minimum width
has been dropped — it existed only so that the changing label would not shift
the neighbors in the bar, and it has no neighbors there anymore.

That the zoom exists at all is therefore told solely by the hint line below the
card: the chip appears only once already zoomed.

All other charts of the app stay without zoom, and that is a decision and not an
open remainder: a dashboard tile is an eye-catcher, not a tool; Sankey and donut
have no time axis; the day-by-hour heatmap is categorical; the monthly course
in the report has twelve bars. `tests/test_chart_zoom.py` records that.

### Marked areas (`markArea`)

"Delete" on the cleanup page is a soft delete; only the purge removes for good.
Every read path, however, filters marked rows out immediately
(`filter_deleted_occurrences`) — they had thus vanished from every view although
they still existed and could still be rescued.

`/api/query?marked=true` returns `marked_ranges` (blocks of `start`, `end`,
`count`) and `marked_total` — the number of marked VALUES in the window, even
if the block list was capped (`MAX_MARKED_RANGES`). **Only the index** is read
(`get_deleted_counts`); hot buffer and archive are not touched. That is exactly
what makes the information cheap enough to deliver with every query — and it is
the reason why the band variant was preferable to the point variant: a band does
not need the values.

Adjacent marks are merged into one block; the threshold is one hundredth of the
window (about nine pixels on a 900 px wide chart). **The value is measured, not
guessed:** one three-hundredth (three pixels) gives 4.8 minutes for a day view,
so a sensor on a 5-minute cadence was twelve seconds above it — from one
contiguous block became 147 separate bands. A threshold derived from the data
(median of the distances) fails with exactly two marks: there the median is
their own distance, so they then always merge.

It is drawn as `markArea` on the main series, not as a second series: a series
would co-determine the axis scaling, distort the zoom's point threshold and
appear in the legend. `silent: true` is mandatory — otherwise the band catches
the mouse pointer events and the curve's axis tooltip stays off at exactly the
interesting spots. A block made of a single mark gets a minimum width,
otherwise it would be zero pixels wide.

Because a band needs only the time axis and makes no statement about the value,
it applies to **every** entity type. (A marker on the removed value could not
have done that: for a counter, bucket values are increases and raw values are
absolute readings.)

The state is saved per entity (`show_marked`, default off). The link from the
purge preview (Housekeeping → Storage → Final cleanup) brings it along for the
visit via `?marked=1`, **without** saving it: opening a page via a link is not a
setting.

The chip in the toolbar appears only if there actually are marks in the period,
and is both — display and menu with the two ways one wants to go from there: to
the cleanup page (take back individual marks) or to the undo preview of the
last batch (`…/cleanup?undo=1`).

**Both are links, and that is the point.** The last batch can be six-digit —
measured in a real installation 196,263 values —, while the chip above it
names only the few marks of the period shown. Triggering an action of this
magnitude from this menu, with a confirmation prompt as the only intermediate
step, would be a trap: the number in the button suggests an entirely different
magnitude from what actually happens. The preview on the cleanup page instead
shows the affected rows themselves before anything happens.

Unfolding the preview is handled by `cleanup.js` based on `?undo=1` — with a
**wait step**, and it is measured to be necessary: the row table is fetched
twice when the page is built (`hx-trigger="load"` on `#controls`, and right
after a `change`, because the inserted fragment writes the page size field;
only the second request carries `page_size`). Whoever opens after the first
swap sees the preview immediately overwritten by the second — it looks as if the
click had never happened.

### Toolbar: two groups

On the left stands **which** section is shown (period, paging, marks), on the
right **how** it is shown (Compare, Options — `.toolbar-right` with
`margin-left:auto`, only from 641 px, below that the bar wraps anyway). Entity
chart and chart editor carry the same group, which is why the rule is in
`app.css`, not in one of the two page files.

The group must **enclose** the buttons. A `margin-left:auto` on the first of two
pushed only that one to the right and separated the second into the next line.
Its menus open to the left (`.toolbar-right .menu-popover{left:auto;right:0;}`),
otherwise the 290 px wide options menu sticks out of the window at the right
edge — tied to the same width as the right alignment, because below it would be
wrong in exactly the opposite way.

Below 641 px that is not enough: there the menu hangs on the left of the button
again, and the latter is at the right end of its row — measured, on a 375 px
phone it ran from 257 to 547 px, thus sticking out 172 px from the window, and
since `<html>` carries `overflow-x:hidden`, the right-aligned switches of every
menu row were **unreachable**. This cannot be solved in CSS: opening to the
right merely shifts the problem to the other edge (in the chart editor the left
edge would lie at −97 px), and a fixed-width popover on a movable anchor cannot
be clamped without knowing the anchor position.
`static/js/menu-popover-clamp.js` therefore measures on opening and pushes back
horizontally — against `documentElement.clientWidth`, because that is the box
at which `overflow-x:hidden` clips. Shifting is done via `left`, not
`transform`: `transform` belongs to the opening animation. The same approach as
`reposition()` in the calendar popover. Plus `max-height:70vh` with inner scroll
below 640 px — the menu is 621 px tall and would otherwise reach below the fold.

There is no "Now" button anymore: it permanently occupied space and was disabled
most of the time. Its function lies as a secondary function on the already
active period step — a renewed click on "Day" jumps back to today, on "Month"
into the current month. The energy dashboard has always known the same gesture
(`setRange()` in `energiedashboard.js`); a `title` points to it as long as there
is something to do. `tests/test_entity_chart_toolbar.py` records both, including
the template in the energy dashboard.

### Average line

One `markLine` per series at the average of the **drawn** values, its own row in
the options menu (not coupled to "Statistics in legend": the legend names the
number, the line shows its position — one often wants the one without the other,
and a row that appears only together with another option would be
undiscoverable without it).

Two decisions that one would otherwise make differently when rebuilding:

- **The value is computed by hand, not via `markLine: {type: 'average'}`.**
  ECharts computes there over the data the series currently carries — with
  `dataZoom` with `filterMode: 'filter'`, i.e. over the visible section. The
  line would thus change its meaning when zooming, and depending on "Dynamic Y
  axis", which determines the filterMode. A fixed `yAxis` value cannot do that.
- **The source of the values differs per page, and that is intended.** The
  entity page draws its points unchanged — there the average is the same as in
  the legend. The chart editor draws `resamplePoints()`, and these combine
  counters and switches by SUM; an average over the raw points would lie there
  by the factor of the bucket width below the drawn bars and stick visibly to the
  zero line. Legend and line can therefore name different numbers there — they
  then also answer different questions.

The line sits only on the main series: with active comparison, a second line for
the previous period would be a statement that appears nowhere in the legend. In
the timeline there is none at all — there the y axis is not a value axis.

It is saved like the other options: per entity in `entities.chart_options`, per
chart in `saved_charts.average_line`. The dashboard tile draws it too — it reads
the value via `data-average-line` from the pinned chart so that the same chart
does not look different twice. There without unit in the text, like the tile's
own value labels.

The tile does not average over `lineData` in the process: the line branch
appends a hold point up to `window_end` that repeats the last value and would
thus count it twice. Therefore each of the two branches collects its values in
`averageValues` itself.

### Area under line charts

A subtle `areaStyle` (opacity 0.08) under every line series, its own row "Area"
in the options menu, only for charts (not for the entity's own history page —
the option does not exist there).

Until 0.92.0, dashboard tile and chart editor knew two completely separate
ECharts option builds: `dashboard-tiles.js` set `areaStyle` unconditionally for
every line series, `chart_editor.js` knew the key nowhere at all — the same
chart looked different pinned than on its own page, without that ever having
been decided consciously.

It is saved like `show_values`/`average_line`: per chart in
`saved_charts.area_fill`, default **on** (`ALTER TABLE ... DEFAULT 1`) — unlike
those two (which start at 0/off), because on corresponds to the previous,
unchanged tile behavior. No global switch needed: the dashboard tile reads its
entire chart configuration from the same `saved_charts` row anyway
(`data-area-fill`, analogous to `data-average-line`), so a pinned chart takes
over the setting automatically.

The comparison secondary series (with active "Compare") gets a lower opacity
(0.05 instead of 0.08) than the main series — it is already dashed/paler
anyway, two equally strong overlaid areas would otherwise have smeared
visually.

### Duration display (switch, display mode "Time")

A switch with `display_mode: "time"` gets, in `chart_editor.js` and
`dashboard-tiles.js` alike, its own synthetic axis key (`axisKey(s) =
isDurationSeries(s) ? ' duration' : s.unit`) instead of its real, mostly empty
`unit` — otherwise it would wrongly share an axis with unit-less standard
entities and appear there in raw seconds instead of as "1h 30m"
(`NumberFormat.fmtDuration`).

Until 0.92.0 this applied only in the chart editor. The dashboard tile knew the
display mode exclusively in its self-built HTML legend (always correct there via
`NumberFormat.fmtDuration`) — the actual ECharts axis, the native tooltip and
the value label still calculated only with `fmtCompactNumber`. The duration flag
therefore now travels along as the fifth element in the `lineData` tuple
(`[ts, value, unit, decimals, isDuration]`) — including the hold point that the
line branch appends up to `window_end`, otherwise exactly the last, often most
visible point would lose its formatting.

The average line (see above) continues to calculate without a duration special
case in both files — the same, pre-existing behavior as before this fix, no new
inconsistency.

### Mark rate of the outlier detection

Below the threshold field (configuring an entity) it states what share of the
values the set threshold marks — writing down the number while it is being
chosen instead of warning about it afterwards.

Two peculiarities that one would otherwise handle differently when rebuilding:

- **The rate appears only if it belongs to EXACTLY this threshold.** The cleanup
  page's cache (`cleanup_alltime_stats`) holds one number per entity; whoever
  changes the threshold would otherwise have the rate of the old one next to
  it. The entry therefore now also stores which threshold was used for counting;
  if it does not match (or is missing, for entries from before this change),
  "Check now" appears there instead of a number.
- **The full scan runs on click, never when building the page.** Measured 7.1
  seconds for an entity with 3.8 million raw values — that belongs in no request
  path. No background run over all entities: the number is needed exactly when
  someone sets the threshold.

Bars and colors come from `.usage-bar-track`/`.usage-bar-fill`, the same
building block as the host storage space in `housekeeping.html`.

## Mobile list view

Below 640 px, two modules work together that a new page does not have to
include but only to operate.

**`table-cards.js` — every table row becomes a card.** Each value carries its
column heading with it as a label (`data-label` per `td`), the header row
disappears, and `app.css` turns this into the card form below 640 px. This
applies automatically to every `table.dt` — a new table has to do nothing for
it. The module itself recognizes exceptions: `colspan`/`rowspan` (in this app
that is called a comparison table, whose grid is the statement), multi-level
headers, fewer than three columns, chart legends. Whoever wants to take a table
out deliberately sets `data-cards="off"`.

The card starts **collapsed**: heading, a lead value and the operating columns
in front of it (favorite star, checkbox) remain visible. The lead value is the
first labeled column after the name — the lists of this app put the most
important information first anyway. If that does not apply to a table, it names
its column itself: `data-card-lead="Last value"` on the `<table>`, with the
column name from the header row. If the name goes nowhere, the first column
applies again. Collapsing happens from two hidden values on.

Also from here: the scroll hint at the edges of `.tbl-wrap` hangs on the class
`.is-scrollable`, which the module sets from `scrollWidth` against
`clientWidth` — whether a container overflows is known only to the layout.

Where the sort menu belongs is decided by `sortHost()` in three steps: a
`[data-sort-host]` in the same `<section>` wins, otherwise the View menu (only
with exactly one list table on the page), otherwise the `.tbl-wrap` above the
table. The first case is for pages that already have an operating row above
their list — on Housekeeping, "Inactive entities" has a period selection, and
sorting belongs in the same row instead of a second one below. **The target
must lie outside the area swapped via htmx:** there it survives the swap while
the menu itself is rebuilt (see `verwaisteSortmenues()`). If it lay inside, every
change of the threshold would delete exactly the form that triggers it.

Below 640 px the pager is left-aligned — at the same edge as headings, count
and the row cards themselves. `justify-content` appears in eight templates as an
inline style (`space-between`, right for the desk), which is why `app.css` sets
it with `!important` instead of in eight templates.

**`list-settings-menu.js` — the toolbar becomes a menu.** Filters, column
selection and sorting stand together on narrow screens in a "View" menu next to
the search instead of in several rows above. A page gets this if its toolbar is
called `#controls` or `.card-browser` **and** carries a search field of its own
as a direct child. The search field is the condition, not decoration: on the
cleanup page, `#controls` is a container containing the period bar — the page's
main control, which does not belong in a menu.

Two properties matter when building further:

- **Moved, not rebuilt.** The controls move as the same DOM nodes into a
  popover *inside* the bar. Field names, `hx-include` and the evaluation on the
  server thus remain untouched; there is no second version of the form that could
  diverge. On wide screens they move back, a comment node per element
  remembers the place.
- **No `MutationObserver`.** An observer on the document feeds off the one in
  `table-cards.js` — this module moves elements, which wakes the other, whose
  work in turn wakes this one. The toolbar is there at load and is never
  replaced by htmx; `DOMContentLoaded` and `htmx:afterSwap` suffice.

The dropdowns that the controls bring along unfold in the menu **in place**
instead of as a popover above it — a popover within a popover cannot be
accommodated at 375 px. No intervention in `dd-picker.js` was needed for that:
unfolding there hangs on the class `.open`, not on positioning.

## Feedback for long actions

Three displays, each an answer to a different question: **the button**, **the
bar**, **the bell**. Named after what one sees, and explicitly not numbered —
between button and bar there was at one point a fourth display, and a number
that shifts with every addition or removal is no good as a name for something
one wants to talk about later. The server-side foundation (`progress.py`, who
registers and why) is in [architecture.md](architecture.md); here it says what
happens in the browser.

**The button — says that it is working.** `hx-disabled-elt="this"` plus the
`.btn` rules in `app.css`: dimmed, `cursor:progress`, a small rotating ring
behind the label. Answers "did my click arrive?" and at the same time prevents
the second click.

If the request takes longer than three seconds, `js/btn-elapsed.js` hangs a
running clock into the button (`.btn-elapsed`, tabular figures so that it does
not change width while counting on). Only from this threshold, because a seconds
display below that is not information but unrest — that something is running is
already said by ring and dimming; the clock says "still, for a minute now". It
hangs on no template but only on whether the trigger is a `.btn`: thus there is
no list of buttons that someone could forget when creating a new one. The script
loads `_topnav.html` centrally, like `topnav-activity.js`. `aria-hidden` lies on
the clock — a number read out every second would be a permanent interruption,
and that the button is busy is told to screen readers by its `disabled`.

Three traps:

- **The hook is not only `.htmx-request`.** htmx sets this class on the
  triggering element — but only as long as no `hx-indicator` is set; otherwise
  it moves to the element named there, and the button stays without any state.
  Measured, "Check index" therefore looked like a permanently locked button
  during its request. The rules therefore additionally draw
  `[data-disabled-by-htmx]` — the attribute that `hx-disabled-elt` sets
  independently and that means "locked **because** something is running".
- The rule `.btn:disabled{opacity:.4}` must come **before** the running variants
  with `opacity:.7` — otherwise a running button is paler than a locked one.
- Disabling happens in htmx *after* collecting the form values
  (`htmx:beforeRequest` runs before), so a `disabled` field does not swallow its
  value. This was verified in the minified htmx, not assumed.

> This clock briefly stood in a chip of its own **next to** five of these
> buttons (`_busy.html`, `busy-chip.js`). The chip has been removed again: it
> said at its core the same as the button, kept space free next to it
> permanently — and, via its `hx-indicator`, took away from the button exactly
> the running state it was meant to supplement. What remained is the one part
> the button alone could not do: the seconds. Whoever considers a display next to
> a button thus has the precedent — first check whether it does not belong in the
> button.

**The bar — says how far.** `_job_progress.html`, filled from
`JobProgress.snapshot()`. The container polls itself every 500 ms via
`hx-swap="outerHTML"`; the endpoint returns either the display again or — as
soon as the job is done — the result **without** `hx-trigger`, whereby polling
ends by itself without counting along. Three presentations, depending on the
honesty of the numbers: total known → "X of Y units"; only a counter → "X units
so far" with an empty bar; neither → "running…". `aria-live="polite"` sits on
the text paragraph, not on the container: the latter is replaced twice a second,
a live region on it would, depending on the screen reader, not be read out at
all or incessantly.

**The bell — says it everywhere.** Since the jobs run in the background, they
survive the page change; the header bar is the only component that appears on
every page. `js/topnav-activity.js` hangs on `_topnav.html` itself (like
Alpine), not on a list of pages, and fetches `/notices/activity` every 2
seconds as long as something is running, otherwise every 8
(`TAKT_AKTIV_MS`/`TAKT_RUHE_MS`). The "Running now" section
(`_activity_block.html`) stands above the notice header and in the accent
color, not in the severity dots below: a running operation is not a problem and
should not look like one either.

The second badge on the left of the bell is deliberately separate from the red
one on the right — the red one counts problems. It is always a pill; the digit
appears only from two simultaneous operations, because most of these jobs
serialize via `exclusive()` anyway and a permanent "1" would be a digit without
information. `.notice-badge.is-activity` therefore sets only side and color
(`right:auto; left:2px;`), no geometry of its own — a test records that so that
the two badges do not diverge.

It blinks only on **change**: the script compares the `data-job-id` of the
entries and flashes three times (`animation:activity-blink .34s steps(1,end) 3`)
when one is added or disappears — deliberately not on the first fetch after page
build. Permanent blinking is ruled out twice: WCAG allows at most three flashes
per second (seizure risk), and anything that blinks longer than five seconds
would need a stop option of its own — a Symcon import runs for minutes.
`prefers-reduced-motion` switches the animation off entirely — the badge itself
stays calm as long as something is running, and the list in the panel says what
anyway.

> **Trap with showing/hiding.** An author rule `display:flex` always beats the
> `hidden` attribute, regardless of order (the UA rule `[hidden]{display:none}`
> has the lowest origin). Whoever toggles an element with `hidden` and at the
> same time gives it a `display` needs an explicit `[hidden]{display:none}` bolt
> next to it. `app.css` documents this on the spot at
> `.notice-snooze-options[hidden]` — the trap has already snapped shut twice in
> this app.

## Recurring patterns that new pages should adopt

- **Inherit `base.html` and `{{ app_root }}` before every path** — both
  described in detail above; they are the two points where a new page under
  Ingress is most likely to break silently.
- **`dd-picker`**: uniform dropdown picker markup/behavior
  (`static/js/dd-picker.js` for simple cases, Alpine `x-data` directly for
  pickers that occur several times per list row — see the comments in
  `table_editor.html` on exactly this trade-off).
- **`confirm-dialog.js`**: app-owned dialogs instead of the browser variants —
  consistent look, correctly colored in the three color schemes.
  `appConfirm(text, {danger})` replaces `window.confirm()` (and via the
  `htmx:confirm` event also covers `hx-confirm`), `appAlert(text)` replaces
  `window.alert()`. In the app, neither of the two browser functions is called
  directly anymore: the native dialog prefixes the message with the server
  address ("192.168.x.x:8123 says") and cannot be styled.
- **`card-browser.js`**: search and sorting of the tile overviews (dashboards,
  charts, tables). Deliberately purely client-side — unlike the entity
  overview, which filters on the server via htmx: these lists typically
  comprise a few dozen entries and stand completely in the DOM anyway. Sorting
  is done via keys of its own per tile (`data-name`, `data-created`,
  `data-favorite`) instead of the order delivered by the server; this leaves
  the `ORDER BY` clauses of the `list_*` methods untouched, which also feed e.g.
  the dashboard dropdown of the topnav. "Favorites first" is a separate switch
  that can be combined with any sorting, not a sort mode. Tiles with
  `data-sort-first="true"` stay at the very front regardless; the dashboard
  overview uses this for the default dashboard.
- **`_entity_tabs.html`**: the tab row of the three entity pages (History, Edit
  values, Configuration). It deliberately borrows the topnav's idiom — baseline,
  active underline, `aria-current="page"` —, and all three pages carry the same
  header above it (`.h1-row` with favorite star, below it entity ID, type, way
  back to the list), so that a tab change exchanges only the content. Before,
  two of the three sat as navigation entries in the history page's options menu;
  the menu now contains only actions. If two tab rows meet, as on "Edit values",
  the inner one is graded down to soft pills (`--accent-line-soft`, smaller
  font, no baseline) so that the ranking is readable even without color
  knowledge.
- **`_dashboard_usage.html`**: shared usage display in opened chart and table
  views. It uses the existing chip, menu and popover building blocks; with
  several assignments the default dashboard comes first, then the names follow
  alphabetically.
- **`number-format.js`**: the only place that knows a number format; the format
  comes from `window.ZA_FORMAT` (setting "Number and date format", see
  [Number, date and currency format](#number-date-and-currency-format)), no
  longer fixed to `de-DE`.
- **`server-time.js`**: calendar values in the time zone of the SERVER instead
  of the browser. The server computes periods in its time zone (option
  `timezone`) and delivers timestamps; whoever makes a date from them with
  `getFullYear()`/`getMonth()` or `toLocaleDateString()` without `timeZone` gets
  the previous day with a browser behind the server zone (e.g. Portugal, server
  in Germany) — on 01.10. "September", in January the previous year. The zone
  sits as `data-tz` on `<html>` (`base.html`); `ServerTime.withZone(options)`
  adds `timeZone` for `toLocale*String()`, `ServerTime.parts(epochSeconds)`
  returns year/month/day/hour/minute/weekday. New code that displays server
  timestamps uses this helper. Not yet converted (roadmap): entity/chart editor
  and the ECharts time axes.
- **`sortable-table.js`**: uniform sorting behavior for longer list/admin tables
  (cleanup preview, index consistency, data integrity, run histories,
  duplicates per entity, Symcon mapping report), including automatic page breaks
  with many rows — new tables of this kind should use this module instead of
  sorting logic of their own.
- **Badge + popup** (energy dashboard): a compact, colored key figure in the
  card header (`@click="$refs.xDialog.showModal()"`) opens a native
  `<dialog class="detail-dialog">` with details — saves space compared to a
  permanently visible card. Closes via the standard button as well as by click
  outside (`@click="if ($event.target === $el) $el.close()"` on the `<dialog>`
  itself). Multi-line `[data-tooltip]` content needs the opt-in class
  `.tooltip-lines` (`white-space:pre-line`) plus real `\n` in the attribute
  value — the app-wide base rule otherwise renders `white-space:normal` and line
  breaks collapse into spaces.
- **`group-picker.js`** (energy dashboard, consumer groups): searchable dropdown
  without a fixed option list — select an existing entry or create a new one via
  free text (like tags/labels in Home Assistant), analogous to
  `entity-picker.js`, but the option list itself is an Alpine list held in the
  root `x-data` and passed to each instance by reference (not copied) — a group
  newly created here thus appears immediately in every other group field,
  without a server round trip.
- **`hx-disabled-elt="this"` and `_job_progress.html`**: the two building blocks
  for actions that take noticeable time — the button alone where there is no
  honest total, the bar where there is one. New long actions should use these
  instead of placing a display of their own next to them; what belongs to it
  server-side is above under "Feedback for long actions".
- **`.usage-bar-track`/`.usage-bar-fill`**: slim utilization bar (model:
  `_settings_backup_progress.html`'s progress bar, here though for a persistent
  state instead of a running operation). Fill color via an additional class
  `positive`/`warning`/`danger` on `.usage-bar-fill`, with a subtle
  `color-mix()` gloss gradient instead of a colorful scale across the full
  width. Currently used for the host storage space in `housekeeping.html`.
- **`.status-card-accent`/`.status-card-accent-strong`**: two-level,
  non-alarming highlighting of a `.status-card` tile (e.g. "Update available")
  — deliberately separate from `.status-card-danger` so that red stays reserved
  for real problems. Level 1 only border/background, level 2 additionally
  colored text.
- **Content-width cards instead of equal-width grid columns** (energy dashboard,
  several storage units): `display:flex;flex-wrap:wrap` instead of
  `display:grid` with `1fr` columns, when cards may need different amounts of
  space — flex items are content-sized by default instead of stretching to a
  forced equal column width (which would have left visible empty space beside
  narrower card content), but wrap into the next line when space is short just
  like a grid.
