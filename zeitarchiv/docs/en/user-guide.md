# User guide

*[Deutsche Version](../user-guide.md)*

This document is the detailed guide for users of the app — step by step,
task-oriented, every page in detail. For a short overview (what the app is,
core features at a glance) see the [app README](../../README.en.md); technical
internals for developers are in the other documents of this folder (see
[README.md](README.md)).

## Contents

**Setup**
- [First steps](#first-steps)

**Quick start**
- [Typical tasks](#typical-tasks) — "I want to do X" → straight to the solution

**The pages at a glance**
- [The overview page](#the-overview-page)
- [Searching and sorting overviews](#searching-and-sorting-overviews)
- [Explanations for a field](#explanations-for-a-field)
- [On the phone](#on-the-phone)
- [Dashboards](#dashboards)
- [Energy dashboard](#energy-dashboard)
  - [Setup](#setup)
  - [Required and useful entities](#required-and-useful-entities)
  - [Setting retention correctly](#setting-retention-correctly)
  - [Energy flow and consumer groups](#energy-flow-and-consumer-groups)
  - [Key figures, rings and badges](#key-figures-rings-and-badges)
  - [Status, data quality and anomalies](#status-data-quality-and-anomalies)
  - [Daily load profile](#daily-load-profile)
  - [Energy report](#energy-report)
- [Entities and histories](#entities-and-histories)
  - [Period navigation](#period-navigation)
  - [Presentation](#presentation)
  - [Zooming into a section](#zooming-into-a-section)
  - [Seeing marked values](#seeing-marked-values)
  - [Comparison](#comparison)
  - [Key figures and legend](#key-figures-and-legend)
  - [Saving the view](#saving-the-view)
  - [Saved options](#saved-options)
- [Charts](#charts)
  - [Entities and presentation](#entities-and-presentation)
  - [Donut instead of time series](#donut-instead-of-time-series)
  - [Period and comparison](#period-and-comparison)
  - [Differences from the history view of a single entity](#differences-from-the-history-view-of-a-single-entity)
  - [Order, names and visibility](#order-names-and-visibility)
  - [CSV and image export](#csv-and-image-export)
  - [What is saved — and what is not](#what-is-saved--and-what-is-not)
- [Tables](#tables)
  - [Rows](#rows)
  - [Columns](#columns)
  - [Aggregation and formatting](#aggregation-and-formatting)
  - [Formulas](#formulas)
  - [Presentation](#presentation-1)
- [Statistics](#statistics)

**Configuring &amp; maintaining entities**
- [Configuring an entity](#configuring-an-entity)
  - [Where do I start?](#where-do-i-start)
  - [App display name](#app-display-name)
  - [Resolution](#resolution)
  - [Retention](#retention)
  - [Decimal places](#decimal-places)
  - [Value change filter](#value-change-filter)
  - [Gap detection](#gap-detection)
  - [Outlier detection](#outlier-detection)
  - [Display mode](#display-mode)
  - [What applies retroactively — and what does not](#what-applies-retroactively--and-what-does-not)
  - [Data management](#data-management)
- [Cleanup](#cleanup)
- [Data handling](#data-handling)
  - [The journey of a value](#the-journey-of-a-value)
  - [Retention enforcement](#retention-enforcement)
  - [Deleting values: three levels](#deleting-values-three-levels)
  - [Changing and adding values](#changing-and-adding-values)
  - [Gaps, duplicates, repeats and counter decreases in detail](#gaps-duplicates-repeats-and-counter-decreases-in-detail)
- [Housekeeping](#housekeeping)

**Import, export &amp; backup**
- [Import and export](#import-and-export)
  - [Symcon](#symcon)
  - [CSV](#csv)
  - [Home Assistant](#home-assistant)
  - [Reports](#reports)
  - [Duplicate protection](#duplicate-protection)
  - [CSV export](#csv-export)
- [Backup / Restore](#backup--restore)
- [Demo mode](#demo-mode)

**Reference**
- [Log](#log)
- [Settings in detail](#settings-in-detail)

**Help**
- [Frequently asked questions](#frequently-asked-questions)

## First steps

1. **Install the app** — via the add-on store (add the repository
   `https://github.com/bertel2020/HA-Apps`) or manually. Details:
   [App README → Installation](../../README.en.md#installation).
2. **Copy the API token** — open Zeitarchiv from the Home Assistant sidebar,
   **Settings → Connection**, copy the token. The token is generated
   automatically on first start and is valid only for this Zeitarchiv
   installation.
3. **Install the integration** — [github.com/bertel2020/HA-Zeitarchiv](https://github.com/bertel2020/HA-Zeitarchiv),
   via HACS or manually. In Home Assistant, under **Settings → Devices &
   services → Add integration → Zeitarchiv**, enter host (`localhost`),
   port (`8127`) and token.
4. **Prepare a label** — Zeitarchiv preferably selects entities via Home
   Assistant labels. If a suitable label already exists (e.g. a thematic one
   such as "Energy" or one created specifically for Zeitarchiv), skip this
   step and select that label directly in step 5. Otherwise create one first:
   in Home Assistant under **Settings → Areas, labels & zones → Labels → Add
   label** — a name is enough, color and icon are optional. Then assign the
   new label to the desired entities, devices or areas: either individually
   via their settings page (gear icon → "Labels" field), or in bulk via
   **Settings → Devices & services → Entities** — select several entities
   there by checkbox and choose "Add label" in the toolbar at the bottom. This
   order — first create the label, then label the entities, only then
   configure Zeitarchiv — saves the detour via an empty selection in step 5.
5. **Define archive filters** — on the integration tile, **Configure → Edit
   archive filters**: select the prepared label — all entities linked to it
   end up in the filter automatically, including newly added ones, without
   ever having to add anything here afterwards. Under "Additional selection
   methods", individual entities, areas, devices or entity patterns can be
   added in addition or instead — useful for individual cases that do not
   justify a label of their own. Without a filter no data arrives; the app
   then waits idly without showing any error.
6. After the first received value, the entity appears automatically under
   **Entities** — with the global defaults from **Settings → Archiving**.
   These defaults can be overridden individually per entity at any time (see
   [Configuring an entity](#configuring-an-entity)).
7. Filters, token or defaults can be changed afterwards at any time —
   already archived values are unaffected, only future values follow the new
   settings. New entities with the same label appear automatically without the
   filter having to be edited again.

**How can I tell that data is arriving?** Under **Settings → Connection**,
"Last received value" shows the time of the most recently processed write. If
this value stays empty or old, the cause is either missing or wrongly assigned
archive filters (step 4/5) or a wrong token/host in the integration (step 3).

## Typical tasks

**"A sensor sends implausible outliers."**
→ Open the entity → gear icon → set outlier detection to a suitable multiple
and read off the rate below it to see what this does (switches do not have
this setting, see [Outlier detection](#outlier-detection)) → back to the
history view → **Clean up** → review and delete detected outliers
(soft delete, reversible) → **Housekeeping → Storage**, if the space is
actually to be freed.

**"I want to compare indoor and outdoor temperature over the last 12
months."**
→ **Tables** → new table → 12 columns (period type "Month", offset 0 to −11)
→ two rows (one entity each) → optionally a formula row for the difference.

**"A dashboard on a wall tablet should not be changed by accident."**
→ Open the dashboard → editor → enable "Locked".

**"I want to take over old Symcon data without duplicating live HA data."**
→ **Import → Symcon** → upload ZIP → check the mapping → start the import.
Timestamps that already exist are skipped automatically, regardless of the
source.

**"I don't use Symcon but still want to take over my existing HA history."**
→ **Import → Home Assistant** → select entities, optionally "Check
availability" → preview (dry run) → start the import.

**"An entity no longer sends, but I want to keep it."**
→ Simply leave the entity unchanged — already archived values are retained,
and charts and tables continue to show the existing history. Only if needed,
use **Delete all values** or **Remove entity** via the gear icon.

**"I want to play it safe before a larger intervention (import, cleanup,
update)."**
→ **System → Backup / Restore** → create a backup → download it or leave it in
the configured schedule.

**"I want to demo or try out the app without risking real data."**
→ Add-on configuration → enable option `demo_mode` → restart the add-on →
see [Demo mode](#demo-mode). The real data remains untouched in a completely
separate directory.

## The overview page

The overview (reachable via the house icon in the menu, `/uebersicht`) shows
a key figure summary at the top (number of entities, records, storage
requirements) and below it the **default dashboard** — the same tile view as
under **Dashboards**, just permanently assigned to the overview and not
renamable or deletable. Like any other dashboard it can be populated with
tiles, rearranged and locked (see below).

What appears when opening Zeitarchiv via the HA sidebar (the overview or the
energy dashboard directly) is determined by **Settings → Appearance → Start
page**. The "Overview" entry in the header always leads to the overview
itself, independently of that.

The bell in the header (visible on every page) shows what is currently working
in the background (see [What is running right now](#what-is-running-right-now))
and current system notices — e.g. a recommended index optimization, a failed
backup or retention run, or an available update. Non-critical notices
(info/warning) can be muted individually for 1 hour, 1 day, 7 days, 30 days or
permanently; errors never. Notices that have already been muted remain visible
under **Settings → Notices** and can be re-enabled early there.

## Searching and sorting overviews

The three overviews **Dashboards**, **Charts** and **Tables** work the same
way: one tile per entry, with a row above for searching and sorting.

- **Search** filters by name, ignoring case. Umlauts can also be typed in
  transliterated form: "ubersicht" or "uebersicht" finds "Übersicht" as well.
- **Sorting**: newest first (default), oldest first, name A–Z or name Z–A.
- **Favorites first** is a separate switch next to the sorting, not a sort
  mode of its own. When switched on, favorite entries appear at the top;
  within the favorites and below them the chosen sorting continues to apply
  unchanged — so the two can be freely combined. The switch is active by
  default.
- Search terms only apply for the moment; the chosen sorting and the favorites
  switch are remembered per overview in the browser and apply again on the
  next visit.

Clicking a tile opens the entry. Everything else — edit, duplicate, delete — is
in the tile menu (⋮) at the top right; the star next to it toggles the
favorite.

**Names** of dashboards, charts and tables are unique within their kind and at
most 50 characters long. In the comparison, case and leading/trailing spaces
do not matter: next to a chart "Wind", no second "wind" can be created. A name
that is already taken is rejected on saving with a notice. Duplicates count up
themselves — "Wind (copy)", then "Wind (copy 2)" and so on.

## Explanations for a field

Where a field or section has an explanation, a small "i" appears next to its
label. One click unfolds the text, another folds it again — so the
explanation is available during setup without permanently taking up space
afterwards. This applies equally at all screen widths.

Not everything folds away: **warnings** ("permanently removes marked
records") and **status lines** ("List is loaded on opening …", "12 duplicate
timestamps in the last 30 days") are shown without being asked. Where a
warning announces irreversible loss, it additionally carries a colored edge on
the left margin.

## On the phone

Zeitarchiv is the same application whether in the browser at the desk or on
the phone — only lists show less at once there. Two things therefore look
different on narrow screens.

**Lists become cards.** Instead of a table that has to be pushed sideways,
each row gets a card of its own in which each value carries its column heading
with it. The card is initially **collapsed** and shows the name and a lead
value — in the entity list the last value, on the Housekeeping page likewise.
The arrow at the top right unfolds the rest and folds it again; the star next
to it toggles the favorite. Collapsed, about seven entries fit on a screen
instead of three.

**All settings of a list are in the "View" menu.** Filters, column selection
and sorting come together there, next to the search field. The search remains
visible because it is the most frequent action. If at least one filter is set,
the number appears in the button: "View (2)". This applies to the entity list,
the CSV export and the overviews for dashboards, charts and tables.

**Nothing changes at the desk.** Both points apply below a window width of 640
pixels. If you drag a browser window narrow, you see the same view as on the
phone; when you widen it again, the familiar table with its toolbar returns.

Two small things on the side: Long entity IDs end in an ellipsis on cards
instead of wrapping onto a second line — the full name is above, the whole ID
on the entity's detail page. And a narrow gradient at the edge of a table
indicates that more is coming sideways; it appears only if there actually is
something to scroll.

## Dashboards

- The **Dashboards** menu item (main navigation) unfolds a list of all
  existing dashboards. From there: create a new dashboard, open an existing
  one, rename, favorite, duplicate, set as **default dashboard** (it then
  appears on the overview page and always comes first in the dashboard
  overview, regardless of sorting or "Favorites first") or delete. There is no
  upper limit on the number of dashboards. The default dashboard carries a
  colored header stripe for easier recognition (the same idea as with the
  energy dashboard tile at the top of the list); if **Settings → Appearance →
  Start page** is set to Energy dashboard, its tile additionally shows a small
  house icon.
- Clicking the tile opens the dashboard; "Edit", "Duplicate", "Set as default"
  and "Delete" are in the tile menu (⋮). Search field, sorting and the
  "Favorites first" switch above the tiles work as with charts and tables (see
  [Searching and sorting overviews](#searching-and-sorting-overviews)).
- Each dashboard shows up to 30 tiles — charts, tables and **value tiles**
  mixed — in freely selectable size (1×1 to 3×3, up to 6×6 in Precise mode).
  Arrange them by drag and drop; via the tile menu (⋮) resize, duplicate
  (charts/tables) or remove. Removing a tile only deletes the placement, not
  the underlying chart or table.
- **Sections** organize many tiles into named blocks: via the "+" tile, "Section"
  tab, assign a name. A section header can be collapsed/expanded via a chevron
  (remembered only locally in the browser) and removed via its own menu (⋮) —
  the contained tiles are kept and slide into the previous section (or become
  "no section" if it was the first). Sections do not count toward the 30-tile
  limit.
- **Value tile:** pins the current value of a single entity directly to the
  dashboard without creating a chart. The same entity can also be pinned
  several times, each tile with its own independent settings — e.g. one with
  main value "Current", a second with "Ø" over a different period. After
  pinning, the configuration opens immediately. For an entity of type
  **counter**, the large value is not the meter reading but the **increase**
  in the selected period (abbreviation "+") — the reading since commissioning
  can still be set via "Current". This applies to newly pinned tiles; existing
  ones stay as they are configured. Period (hour/day/week/month/year) and
  whether it is **Current** (started calendar period, "day" from midnight) or
  **Rolling** (fixed window relative to now, "day" = last 24 hours) together
  determine what key figures and sparkline are computed over. As the **main
  value** (the large number), besides "Current" you can also choose
  min/average/max/sum; additionally, an optional **key figures row** shows up
  to three of the remaining key figures smaller below. Combinations that make
  no sense (e.g. sum for a non-summable entity, or a key figure already chosen
  as main value) are grayed out. The sparkline is active by default and shows
  the raw points stored in Zeitarchiv for the last 24 hours; alternatively it
  can be condensed to one point per 5, 15 or 30 minutes or per hour. Entity,
  display of the last update, decimal places and title are editable directly in
  the tile. The period label (e.g. "Month" under the main value) can be
  shown/hidden separately — independently of the display of the last update;
  both can be shown at the same time. If the last value is too old, the card
  border is highlighted in yellow or red. From when on, you set per tile under
  **Mark as stale**: **Default** (yellow after 15 minutes, red after 1 hour),
  **Daily** (26 or 48 hours, for daily values), **Rare** (3 or 7 days, for
  counters that only report on activity) or **Off**. All settings of a value
  tile are in a dedicated, larger settings popup (⋮), since considerably more
  options come together here than with chart/table tiles.
- **Add tile:** the "+" tile opens a popup with tabs for charts, tables, value
  tiles and sections, each with a search field (sections instead with a name
  field). Charts and tables can be pinned directly from the list or created
  anew via "+ New chart"/"+ New table" (ends up on this dashboard
  automatically after saving); value tiles are selected via the entity search.
  A chart or table can be pinned to several dashboards at the same time.
- In the opened view of a saved chart or table, **Used in** shows on which
  dashboards the entry is placed. One dashboard is linked directly; with
  several, the counter opens a compact list with links. The assignment is still
  changed in the respective dashboard.
- Chart tiles from size 2×2 (in Precise mode from 3×3) can show a legend via
  the tile menu (⋮) ("Show legend") — appearance and content match exactly the
  legend of the underlying chart, including its "Show values" and decimal
  places setting; clicking the legend shows/hides the respective series
  without navigating to the chart.
- **Precise mode** (dashboard editor): doubles the tile grid from 3 to 6
  columns at half the row height — existing tiles keep their visual size
  because their size value is doubled automatically. **Fill gaps** lets later,
  smaller tiles fill free gaps in the grid instead of strictly following the
  pinning order. Both switches can be combined independently of each other. On
  narrow displays the presentation deliberately stays single-column even in
  Precise mode so that tiles remain readable and usable.
- **Lock dashboard** (editor, "Locked" switch): blocks rearranging, resizing
  and removing tiles on the view itself — protects against accidental moving
  on, for example, a permanently displayed wall tablet. Renaming and deleting
  the dashboard remain possible in the editor; only the tile view itself is
  locked.
- **Deleting a dashboard** only removes the tile arrangement of this
  dashboard — the underlying charts/tables are kept and can be pinned
  elsewhere again via "+ Add tile".
- The fade-in/out animation of the tile charts (appearing briefly rather than
  instantly) applies centrally to all tiles on all dashboards and can be
  switched off under **Settings → Appearance**.

## Energy dashboard

A standalone view (not an entry in the normal dashboard system) that shows a
household's energy flow as a Sankey diagram: from grid import and generators
via a central node to consumers, storage units and feed-in. It is switched on
and off via a fixed tile at the top of the dashboard overview and is
afterwards also reachable in the **Dashboards** menu.

### Setup

On first activation (and later at any time via the pencil next to the title,
**"Edit roles"**), the role assignment shows every possible role as its own
tile: grid import, feed-in, any number of generators, any number of storage
units, any number of consumers, costs, PV yield forecast and CO₂. A click on a
tile opens a popup with the corresponding fields; for generators/storage/
consumers the **"+"** tile creates a new row, the drag handle (⠿) reorders
existing rows. Input in a popup only takes effect after clicking **"Apply"** —
so a popup opened by accident can be closed safely without changing anything.
The whole assignment is only saved for good with **"Save"** at the bottom of
the page.

The **"General"** area additionally sets the name of the central node
(default "House"), the threshold for the anomaly marking (see
[below](#status-data-quality-and-anomalies)) and **"Visible tiles"** — which
of the optional cards (self-sufficiency & storage, consumer shares, supply
shares, cost analysis, CO₂ balance, daily load profile, balance & data
quality) are displayed at all. The energy flow itself cannot be switched off.

### Required and useful entities

The role assignment selects exclusively from already archived entities — so
nothing additional has to be set up beforehand for the energy dashboard that
does not already arrive in Zeitarchiv anyway.

| Role | Required? | Expected value |
| --- | --- | --- |
| Grid import | **yes** | Meter reading of electricity drawn from the grid (kWh, ascending) |
| Feed-in | no | Meter reading of grid feed-in (kWh, ascending) |
| Generators (any number) | no | One yield counter each (kWh, ascending) with its own name — e.g. rooftop system and balcony power plant kept separate |
| Storage: charge / discharge (any number of storage units) | no | Two meter readings each (kWh, ascending) — values across several storage units are added up |
| Storage: state of charge (SOC) | no | Instantaneous value in percent, not a counter — with several storage units averaged capacity-weighted |
| Storage: capacity | no | Total capacity in kWh (entity or fixed value; Wh entities are converted automatically) — only needed so that the state of charge is additionally shown in kWh and weighted correctly with several storage units |
| Consumers (any number) | no | One consumption counter each (kWh, ascending) with its own name and optionally a freely named group — everything not assigned individually automatically remains visible as "base load" |
| Electricity price (import/feed-in) | no | Currency/kWh entity (currency: see Settings → Appearance); without a suitable entity, a fixed amount in the subunit (cents, pence, rappen) as a substitute |
| CO₂ intensity | no | g/kWh entity; without a suitable entity, a fixed value as a substitute |
| PV yield forecast | no | kWh for "rest of today" and "tomorrow", e.g. from a Forecast.Solar integration |

Only grid import is required — all other roles merely unlock additional tiles,
rings or badges; without a storage role, for example, the storage tiles and
the efficiency ring simply stay hidden. The selection fields show only
entities with a suitable unit or counter type for the respective role from the
start.

For grid import, feed-in, generators, storage (charge/discharge) and
consumers, a **kWh total counter** is expected (Home Assistant device type
`total_increasing`), not an instantaneous power in watts — many device
integrations offer both in parallel; here the kWh counter entity counts, not
the watt entity. Storage SOC, storage capacity, electricity price, CO₂
intensity and PV forecast, on the other hand, are deliberately instantaneous/
measured values (`measurement`), not counters.

### Setting retention correctly

The [retention period](#retention-enforcement) set per entity affects the
energy dashboard to different degrees — not every role needs the same period:

- **Grid import, feed-in, generators, storage (charge/discharge/SOC) and
  consumers** should be retained generously — at least **2 years**, when in
  doubt **Unlimited**. The self-sufficiency, self-consumption, SOC and
  efficiency trends in the ring popup each evaluate the last three calendar
  years (as does the monthly course in the energy report); a shorter period
  makes these trends gappy over time.
- **Electricity price and CO₂ entities** (if connected via an entity instead
  of a fixed value) are factored in per displayed period bucket. If values are
  missing because the retention period has since removed them, the cost/CO₂
  balance for that past period just turns out smaller — not an error, merely
  an incomplete analysis. If you mainly need current to a few months old
  analyses, **90 days** or **365 days** suffice here and save storage: dynamic
  tariffs and CO₂ signals often update every minute and grow correspondingly
  fast.
- **Storage capacity and PV yield forecast entities** are read exclusively as
  the current value — regardless of the period currently displayed, no
  archived, old value is ever needed. The shortest available period (**30
  days**) suffices here; more retention brings no advantage for these roles
  but needlessly costs storage with frequently updating sources.

### Energy flow and consumer groups

The Sankey shows sources (grid import, generators, storage discharge) on the
left, sinks (consumers, storage charge, feed-in) on the right, with the
central node in between. The remainder — grid import plus generation minus
consumers minus feed-in minus storage charge — appears automatically as
**"base load"**, without a sensor of its own. Navigation works as with charts
via hour/day/month/year with forward/back.

A consumer with an assigned group hangs on the central node in two stages in
the Sankey (node → group → device), an ungrouped one directly on it like a
generator — keeps the flow clear with many individual consumers. Groups are
created directly when assigning a consumer (select an existing one or create a
new one via free text) or can be managed centrally via the dedicated
**"Groups"** button next to the consumers heading (rename, delete — affected
consumers merely become group-less again, their values remain unchanged).

The consumer shares table (see below) shows at most the eight largest
entries; with more consumers, **"show more"** reveals the rest — the donut
next to it always shows all, independently of that. If you have created
groups, you can additionally switch in the role assignment under **"Consumer
shares" → "Show by group"** whether the table shows each consumer
individually or grouped (one row per group, ungrouped devices stay
individual).

### Key figures, rings and badges

Directly below the Sankey are five KPI tiles (generation, consumption, grid
import, storage, feed-in) for the selected period; with several storage units
or generators, their tooltip additionally shows the breakdown per device. A
click on a tile leads to the chart of the underlying entity, with the same
period currently set in the energy dashboard; if more than one entity is
behind it (several generators/consumers, or a storage unit with separate
charge/discharge entities), a short selection window opens instead. The link
"← back to the energy dashboard" on the entity page (as well as the one from
the energy report, see below) leads back exactly to this period, not to the
default view.

Below that, two donut-plus-table cards sit side by side: **"Supply shares"**
(generators, grid import and storage — the supply side) on the left and
**"Consumer shares"** (the individual consumers plus base load — the
consumption side, see [Energy flow and consumer
groups](#energy-flow-and-consumer-groups) above) on the right. Hovering a
table row with the mouse highlights the corresponding donut segment; entities
with their own assignment are additionally linked.

A storage unit counts in "Supply shares" with its **net use** (discharged
minus charged) instead of the pure discharge — otherwise a generator with its
own storage (e.g. a solar bank) would be partly counted twice: once with its
full yield, once via the storage, although part of it has not yet reached
consumption at all. Net use can be negative (period mostly charged); the donut
then shows only the positive shares, the table the row nevertheless including
the minus sign.

The **"Self-sufficiency & storage"** card below shows four rings —
self-sufficiency, self-consumption, storage state of charge and storage
efficiency (with several storage units each combined capacity-weighted, so
that an empty and a full storage unit do not falsely appear as "50 %"). A
click on a ring opens its monthly trend of the last three calendar years.

Optional badges in the header area summarize the CO₂ balance (🌱) and the cost
balance (💰) — one click each opens the details, containing in each case an
additional third tile "Balance" (CO₂ emitted minus avoided) or the
already-known balance, both highlighted in color: if the balance is in the
plus (more avoided or earned than caused or paid), this is celebrated
specifically with a star and a short notice text instead of just being shown
as a number. A key figures bar above the Sankey additionally bundles
self-sufficiency, avoided CO₂, cost balance and the PV yield forecast for
"today" and "tomorrow" at a glance.

### Status, data quality and anomalies

The **"Status"** chip (✓ or ! when there are problems) opens a popup with the
balance check and the remaining data quality checks: stale sensor values,
counter resets, wrong unit, wrong counter type and doubly assigned entities.
The same popup lists anomalies — consumers or groups that are well above their
average of the last periods. The threshold for that (default +50 %) can be
adjusted in the role assignment under **"General"** or switched off entirely.

### Daily load profile

For day/hour, shows the hourly consumption of the last 7 calendar days; for
month/year, instead the consumption averaged by weekday (Mon–Sun) over the
selected period, so that it becomes visible on which weekdays more is
typically consumed.

### Energy report

An icon next to the period navigation (active only for month/year) opens a
print-optimized report for the currently selected period — key figures
including previous-year/previous-month comparison, cost and CO₂ balance
(including the CO₂ comparison as a car trip distance), consumer shares
including cost per consumer, and for annual reports additionally a monthly
course and all anomalies of the year. A link at the top leads back to the
normal view at any time. The page itself produces no new file format and no
library runs in the background — the **"Print / Save as PDF"** button merely
calls the browser's print dialog, where you can choose "Save as PDF" instead of
a real printer as usual. There is no automatic dispatch by e-mail — the
report, like everything in Zeitarchiv, stays exclusively local.

## Entities and histories

**Entities** lists all known entities, searchable via the name or the entity
ID. Via **Columns ▾**, eleven additional columns can be shown/hidden (type,
first/last value, resolution, retention, unit, records, size, value filter,
gap/outlier threshold) — the selection applies globally to the whole add-on,
not just in the current browser. If the columns **Type** or **Unit** are
shown, a matching filter appears next to them (type: default/counter/switch,
multiple selection possible; unit: one of the units actually occurring). A
click on a row opens the **history view** of that one entity.

Every entity has three equal-ranking views, reachable via the tab row below
the name: **History** (the diagram), **Edit values** (see [Cleanup](#cleanup))
and **Configuration** (see [Configuring an
entity](#configuring-an-entity)). All three carry the same header — display
name, favorite star, below it entity ID, type and the way back to the list —
so that switching only exchanges the content below. The options menu of the
history view therefore contains only actions and presentation switches, no
navigation anymore.

### Period navigation

- Choose hour, day, week, month, year or decade as the base unit; forward/
  back arrows page by one unit each.
- **Clicking the already selected unit again jumps back to the current
  period** — "Day" to today, "Month" to the current month. The same gesture as
  in the energy dashboard.
- **Current** ("up to today") shows the current, not yet completed period,
  e.g. "this week so far" — even before any data exists for the rest of the
  period, the axis extends to the full calendar boundary (e.g. to Sunday for
  "Week").
- **Rolling** (switch in the options menu) instead shows a fixed time window
  relative to now, e.g. "last 24 hours" or "last 30 days", independent of
  calendar boundaries. For "Month" this is exactly 30 days, not a calendar
  month — "one month before January 31" would be ambiguous.

### Presentation

- **Chart type:** line or bar; for switch entities (`switch`,
  `binary_sensor` etc.) additionally a timeline that shows the ON intervals as
  continuous bars instead of individual points.
- Lines can be smoothed (options menu), which visually suppresses short-term
  noise without changing the underlying values.
- **Raw values** shows every single stored measurement in the period instead of
  aggregated points — useful for closer inspection of short periods, limited
  by the query limit for very long periods.
- The options menu shows only what actually has an effect for the current
  presentation: **Points** and **Raw values** belong to line charts, **Show
  values** to bars. When switching the chart type, the offered options change
  accordingly.
- **Dynamic Y axis** scales the axis to the actual range of values of the
  displayed period instead of starting at 0 — makes small fluctuations more
  visible, but can also exaggerate the visual size of changes. For bar charts
  the axis is unaffected and always starts at 0, because a bar would otherwise
  suggest a wrong order of magnitude.
- **Show values** displays the numeric values directly next to the data
  points.
- **Average line** places a dashed horizontal line at the average of the
  displayed values across the chart, with the value next to it on the right.
  It is independent of the legend: the legend states the number, the line shows
  where it lies in the picture. Zoom does not change it — it belongs to the
  loaded period, not to the currently visible section.
- **Decimal places** overrides, for this view, the global display setting of
  the entity (Automatic or fixed 0–3).

### Zooming into a section

When a period contains many measurements — raw values of a frequently
reporting sensor, for example — the points lie denser than the screen can keep
apart. Then a section can be magnified:

- **On a computer:** hold **Ctrl** and turn the mouse wheel. The wheel alone
  continues to scroll the page, so nothing happens by accident. Dragging with
  the Ctrl key held moves the section.
- **Dragging out an area directly:** hold **Shift** and drag the mouse across
  the desired period. On release, the diagram shows exactly that section.
- **On the trackpad and on the phone:** pinch with two fingers apart or
  together. A swipe with one finger scrolls the page as everywhere else.

When a section is active, the **Section** button in the toolbar shows its time
span (e.g. "14:20 – 16:05"); a click on it shows the whole period again. The
section is deliberately transient: it is not saved, and changing the period or
changing something in the options menu restores the full view.

Below the diagram there is always a line that says where you stand: how many
data points are currently drawn and whether a section can be magnified from
them.

This is only offered where there is something to uncover — with few points
(such as twelve monthly bars in a year) the button stays gray. With the
**timeline**, on the other hand, it is always available: switching events of a
few minutes are narrower than a pixel in a month view and only become visible
when zooming in.

### Seeing marked values

If you "delete" values on the cleanup page, you only flag them at first —
they are gone only after the final cleanup under **Housekeeping → Storage →
Final cleanup**. Until then they disappear from the history but are still
there.

**Marked values** (options menu) shows where: the affected time segments are
highlighted in the diagram. Consecutive marks form a continuous field, widely
separated ones stay separate. This makes it possible to look once more before
the final deletion whether really only the intended spots were hit. The
setting applies per entity and is off by default.

If there are marks in the displayed period, a button with their count appears
in the toolbar. It leads, as you choose, to the cleanup page (where individual
marks can be taken back) or directly to the "Undo" preview, which shows what
the most recently marked deletion would restore.

Both are links, not actions — deliberately: the most recently marked deletion
can include far more values than the button shows (it counts only the period
currently displayed), and it can lie completely outside of it. The preview
shows the affected rows with count before anything happens.

In the table under **Housekeeping → Storage → Final cleanup**, each entity name
leads directly here, with the display already switched on.

### Comparison

Via the options menu, the current view can be overlaid with the previous
period or the previous year. The label adapts automatically to the selected
period (e.g. "Previous day" in the day view, "Same day last year" for the
year comparison of a day view) and appears directly in the button, so the
active comparison option remains recognizable without opening the menu.

For the periods **Year** and **Decade** there is only the previous period: for
"Year" it is the previous year, a second entry would be named the same there;
for "Decade", a previous-year comparison would shift the decade by only one
year and thus overlap almost completely with the one shown.

### Key figures and legend

Current/Min/Max/Ø/Σ (average/sum) of the displayed period can optionally be
shown directly — as compact chips or as a small table (adjustable via the
legend style). Both presentations are clickable to show or hide individual
series without leaving the period.

### Saving the view

**Save as chart** (options menu) stores the current view — including all
chosen options and any additionally added entities — as a standalone chart,
which afterwards can be edited like any other chart and pinned to a dashboard.

### Saved options

All options in the options menu (Rolling, Raw values, Marked values, chart
type, Show points, Show values, Dynamic Y axis, Average line, legend
statistics and key figures, legend style) are saved **per entity**
permanently and applied again automatically on the next visit. The starting
values for newly opened entities can be changed under **Settings →
Appearance**; "Reset options to default" (in the entity's options menu) puts
only this one entity back to those starting values. The zoomed section is not
part of this: it describes no property of the entity but only the currently
viewed image section, and therefore applies only until the next change.

## Charts

The chart overview lists all saved charts as tiles with search, sorting and
favorites switch (see [Searching and sorting
overviews](#searching-and-sorting-overviews)). Each tile names the chart type,
the number of entities and the period. If a chart contains line and bar series
at the same time — a temperature next to a counter, say — both types are
named.

Own editor, reachable via **Charts** → new chart or editing an existing one
(tile menu ⋮).

### Entities and presentation

- Overlay any number of entities; different units automatically get separate Y
  axes so that, e.g., temperature and humidity remain sensibly readable in one
  chart.
- Whether an entity is drawn as a line or as a bar cannot be chosen manually —
  the app decides automatically by entity type (counters and switches as bars,
  everything else as a line). Only the **timeline** (see below) is a deliberate
  choice.
- Points on/off, raw values, dynamic Y axis, show values, average line,
  decimal places, legend statistics — the same options as in the history view
  of a single entity, but here configured per chart instead of per entity;
  decimal places applies uniformly to all entities of the chart.
- **Area** (only here, not in the history view of a single entity) subtly
  fills the area under line series — on by default, matching the previous
  appearance of the dashboard tile.
- **Average line: Flat/Moving** (only here; the history view has only the flat
  line) — nested directly under "Average line". "Flat" is the previous
  horizontal line at the overall average, "Moving" instead draws a trend curve
  over line series that smooths short-term noise (e.g. weather data over a
  year) — the raw curve remains visible, just more subtle. Only for line
  series; for bar series (counter/switch) the choice has no visible effect.
- **Stacked** (only here, from two bar series in the chart): shows bar series
  of the same unit stacked on top of each other instead of side by side —
  composition and total sum at a glance, such as the daily consumption of
  several consumers. Directly below it, you can additionally switch to
  **Shares (%)** instead of absolute values; the tooltip continues to show the
  original value in parentheses as well. With stacking active, comparison and
  average line are not available — a stacked series no longer starts at 0, and
  neither could then be drawn meaningfully.
- For switch entities only (`switch`, `binary_sensor` etc.), a **timeline** is
  available as in the history view — here as a multi-row presentation with one
  row per entity, so that the ON intervals of several switches can be compared
  directly one below the other.

### Donut instead of time series

Via **Display type**, a chart can be shown as a donut instead of a time series
— one share per entity instead of a time axis, for example to compare the
consumption share of several devices in a total. **Aggregation** determines
which value per entity forms the size of its share: Sum, Average or Last
value. In donut mode, resolution, raw values, timeline, points, area, stacked,
orientation, dynamic Y axis and average line drop out — all time-axis/bar
concepts without an equivalent once there is no time axis anymore. **Rolling**
remains available: it still determines which time window is queried at all, not
its granularity. Legend statistics, key figures and style as well as decimal
places continue to apply unchanged.

### Period and comparison

The same period bar as in the history view (hour to decade), with a
**Rolling** switch for a rolling instead of calendar window (e.g. "last 24
hours" instead of "today").

**Ranking comparison:** For day, week, month or year, a resolution is
additionally available that condenses the complete period into a single total
per entity — handy, for example, for directly comparing the daily/weekly/
monthly/annual consumption of several consumers. Each entity gets its own
category on the axis, with **Orientation** either vertical or horizontal
(horizontal reads better with many or long entity names, since the names then
appear spelled out instead of rotated/truncated). The orientation choice is
always available regardless of the selected resolution — **Horizontal**
switches by itself into this ranking comparison if needed (resolution
"Full"), **Vertical** back to "Automatic"; a manual detour via the resolution
is not necessary. **Compare**, **Rolling**, **Dynamic Y axis** and
**Stacked** are disabled or not selectable at this resolution, since they
would add no meaningful statement for a ranking comparison of individual totals
or would contradict it (Stacked wants to merge all entities into ONE category,
the ranking comparison gives each its own).

**Compare** sets the previous period or the same period of the previous year
against the current one — as in the history view of a single entity. Not
available with active raw values, with the ranking comparison or with active
stacking (see above). Conversely the same applies: raw values, timeline,
donut, stacked, orientation and resolution "Full" are disabled as long as
Compare is active — Compare takes precedence instead of being silently
switched off in the background when toggling.

### Differences from the history view of a single entity

When viewing, a chart always shows the **current period** — unlike the
history view of a single entity, there is no forward/back navigation to past
periods here. Zooming into a section is also missing (see [Zooming into a
section](#zooming-into-a-section)) — a chart can only be narrowed via period
and resolution, not by mouse/touch. Anyone who needs both will find the same
chart type with both functions on the page of the individual entity itself.

### Order, names and visibility

Several entities can be rearranged by dragging or via arrow buttons — this
determines the order in legend, statistics display and color assignment. There
you can also assign, per entity, a deviating display name for this chart only
(effective in legend, statistics and tooltip) and permanently hide the series
via the eye icon — unlike showing/hiding by clicking the legend (see [Key
figures and legend](#key-figures-and-legend)), this is saved with the chart,
not just remembered for the current view.

### CSV and image export

**CSV** (next to "Customize") downloads the currently displayed chart data —
for the time series a timestamp grid with one column per entity, for the donut
one row per entity with the value actually shown. The small icon at the top
right of the chart itself instead saves a PNG snapshot of the current view.
Both files carry the same name made from chart title and period, e.g.
"Household_appliances_September_2026_to_10.09.2026" — a running period
("today"/"up to today") is resolved to the actual date. Available only here,
not on the dashboard tile or in the history view of a single entity.

### What is saved — and what is not

Entities including names/order/visibility, display type (including aggregation
for donut), period including the Rolling switch, resolution (including
orientation for the ranking comparison), dynamic Y axis, show values, average
line (including flat/moving), decimal places, legend statistics, area,
stacked/shares (%) and timeline are all saved with the chart and then also
apply to its preview on dashboards. **Points on/off, raw values and Compare,
on the other hand, are not** — these three are pure view settings for the
current visit and are back to their initial value on the next opening.

A saved chart always shows the currently available data when viewed, not a
frozen snapshot from the time of saving. The opened view shows under **Used
in** the dashboards on which the saved chart lies as a tile and links directly
there. Charts that are pinned nowhere anymore are listed under [Housekeeping →
Unused items](#housekeeping).

## Tables

The table overview lists all saved tables as tiles with search, sorting and
favorites switch (see [Searching and sorting
overviews](#searching-and-sorting-overviews)); each tile names the number of
its rows and columns.

Own editor, reachable via **Tables** → new table or editing an existing one
(tile menu ⋮).

### Rows

A row is a quantity: a single entity, a group of several entities (condensed
into a sum value), a formula (see [Formulas](#formulas)), or a purely visual
divider line without data of its own. A divider line can optionally show a
section name as its own heading — adjustable independently per divider line,
not globally for all.

Each row can be **hidden** via its menu: it disappears from preview and tile
but is still included in calculations — handy for a helper row that only a
formula should access without appearing in the table itself. Also in the row
menu: **Bold** highlights individual rows, independent of the global "Bold
label" setting under [Presentation](#presentation-1) (which affects the entire
label column). Row labels are limited to 30 characters.

Rows can be rearranged by dragging or via arrow buttons and duplicated via
their card (⧉), including all options; formula rows with automatically
corrected letter references.

### Columns

A column is a period: freely named (e.g. "Today", "Aug last year", "2026"),
with a period type (hour, day, week, month, year or decade) and an offset
relative to today (0 = current, −1 = previous, etc.). This makes it possible,
for example, to place the same month over twelve consecutive years side by
side in twelve columns. The label can contain placeholders such as `{jahr}`,
`{monat}`, `{quartal}` or `{woche}` (also English: `{year}`, `{month}`,
`{quarter}`, `{week}` — both spellings work in every language, the insert
helper uses the one of the interface), which resolve automatically to the
respective period of the column (insert helper directly in the label field,
with live preview of the resolved value). The time zone of the app (option
`timezone`) is decisive, not that of the browser — so even while traveling,
`{jahr}` shows the year of the period the table actually queries.

**Year-over-year comparison** automatically sets the offset of a column to the
same period one year earlier (leap-year safe) — it always shifts exactly one
year, so it is not sensibly usable with period type "Decade". If next to a past
column (previous day, previous month, previous year …) there is a column with
offset 0 of the same period type, the past column automatically compares only
the part of its period elapsed so far ("same point in time" comparison) — a
day still in progress is thus compared fairly against "previous day up to the
current time" instead of against the complete previous day.

Like rows, columns carry a short label for unambiguous reference when talking
about the table ("column 3") — for rows a letter (also the formula reference,
see [Formulas](#formulas)), for columns a number (purely for display, without
function in formulas).

Like rows, a column can also be **hidden** via its menu — it is still
calculated, for example so that a hidden previous-year column can still show
its percentage deviation on a visible column (see "Comparison" under
[Presentation](#presentation-1)). **Multi-level header:** Columns with the
same, non-empty group label (e.g. "2025" above several month columns)
automatically get a common, spanning header row above them. Columns can be
duplicated like rows (⧉).

### Aggregation and formatting

- **Aggregation per row:** Automatic (sum for counters, otherwise the average),
  Ø average, Min, Max or Σ sum. Min/Max use the true extreme values of the
  underlying raw data, not the average of the smallest available time slice.
- **Decimal places per column:** Automatic or fixed 0–3.
- **% share** (row menu "Options"): shows instead of the absolute value the
  percentage share of the sum of all entity/group rows of the same column since
  the last divider line.
- **Hide at 0** (row menu "Options"): automatically hides an entity/group row
  as soon as it has either no value or 0 in all visible columns — a
  decommissioned device, for example, without having to hide and show it
  manually.
- **Sum row** (its own row type): sum or average of all entity/group rows since
  the last divider line, updates automatically when rows are added or removed
  above it.
- **Color scale** (column option): colors the cells of a column by their value
  relative to the other entity/group rows in the same section of the same
  column — lighter for low, stronger for high values. Formula, sum and divider
  rows are neither colored nor taken into account for the scale.

### Formulas

Formula rows reference other rows via their letter label (A, B, C …), e.g.
`A / B * 100`. Only rows *above* the formula row can be referenced. When
rearranging rows (dragging or arrow buttons), the letter references in
existing formulas are corrected automatically, so that a formula continues to
reference the same logical row as before the move — not simply the same
position.

Supported are `+ − * /`, parentheses, row letters and number literals (comma
or period as decimal separator, e.g. `A * 3,5`) — no functions such as
rounding or absolute values. A formula row automatically adopts the unit of
the first referenced row unless specified separately in the field provided
for that. Via its row menu, a formula row can additionally be visually
**highlighted** — independent of the general "Bold" (see [Rows](#rows)).

### Presentation

Purely visual settings, they never affect the calculated values:

- **Highlighting:** zebra stripes, highlight first column, highlight header,
  bold label.
- **Comparison:** set comparison columns (previous day, previous month,
  previous year …) apart visually, show percentage deviation from the
  associated comparison column.
- **Numbers / units:** show/hide units, align them in a fixed column or show
  them smaller, align the decimal separator column-wise, spell out missing
  values as "No data" instead of as a dash.
- **Layout:** borders (horizontal/grid/none), density (comfortable/compact),
  header/value alignment (left/center/right, default right-aligned in each
  case), all value columns equal width ("Even columns", the label column is
  unaffected). **Freeze first column** and **Freeze header** keep the label
  column or header row visible while scrolling — Freeze header limits the
  preview/tile to a fixed height with its own scroll bar for that. Column
  widths can be adjusted via a drag handle on the right edge of each header
  cell (double-click resets a column to automatic width); without a manual
  width, each column adapts to its content.

The **CSV** button exports the currently visible rows/columns (including
% share/unit settings) as a semicolon-separated file.

Saved tables always show current values when viewed — like charts, not a
frozen snapshot from the time of saving. Under **Used in**, the dashboards on
which the table lies as a tile are directly reachable. Tables that are pinned
nowhere anymore are listed under [Housekeeping → Unused
items](#housekeeping).

## Statistics

Shows entity count, records, storage requirements and growth over time, as
well as breakdowns by type, resolution and retention. An internal scheduler,
independently of page views, records a real inventory snapshot at most hourly,
so that the growth view remains meaningful even without regular visits to the
page.

The row of tiles at the top names, besides the inventory, also the **growth**:
"New records" counts the last 24 hours, "Ø/hour" and "Ø/day" give the same
measurement as an average over 24 hours and seven days respectively. "New
records" and "Ø/day" carry the same unit — if one is clearly below the other,
the last day was quieter than the week before (or vice versa). All three come
from the same inventory snapshots, so there is no separate event log for it;
as long as less than 24 hours of history exist, a dash is shown there.

All tables can be sorted by clicking their column headings, like the entity
list. The growth diagram adapts its two Y axes dynamically to the respectively
visible range of values.

In the storage usage, **Index** leads to a detail page. It breaks down which
SQLite tables contain entity metadata, write safety and cleanup,
charts/tables/dashboards, statistics histories, and settings and maintenance
histories. For each table and area, the number of entries, occupied data
pages, associated SQLite indexes and their total size are shown. Internal
structures and free SQLite pages are reported separately. The actual
measurement series remain in the hot buffer (current month, uncompressed),
archive and rollups, not in the index.

The index detail page also shows the completely free storage that can be
reclaimed by compaction. SQLite reuses these pages automatically during
operation. A manual **Optimize index** action rewrites the database file
compactly; during this, write access pauses briefly. A recommendation appears
only at an index size of 50 MB or more, at least 10 MB of reclaimable storage
and at least 25 % free pages. In this case, the index in the storage usage is
also marked **Optimization recommended**. Before execution, Zeitarchiv checks
the free disk space and afterwards the SQLite integrity; no automatic
optimization takes place.

During optimization, Zeitarchiv first waits until write operations already in
progress have completed. New transmissions from the Home Assistant
integration pause at the maintenance lock. If the optimization takes longer
than the HTTP timeout, the integration keeps the affected batch and retries it
without a fixed retry limit. Stable event IDs ensure that a batch sent again
or already partially processed does not create duplicate measurements. In
normal operation, therefore, no values are lost due to the optimization.

The integration queue, however, exists only in memory and is limited to 5,000
new events. If it fills up during an exceptionally long backlog, further new
events are dropped; a restart of Home Assistant or the integration likewise
discards values not yet transmitted. Queue size and dropped events are visible
on the integration's device page under **Diagnostic**.

The storage breakdown links directly to import reports and backups, since
these also occupy storage but are not included in the pure entity statistics.

## Configuring an entity

Via the gear icon in the entity list or via the **Configuration** tab of an
opened entity. Every setting applies **only to this one entity** and
overrides the global default from **Settings → Archiving**. New global
defaults never apply retroactively to entities that already exist.

### Where do I start?

The eight fields act at very different points. This classification matters
more when configuring than the order in the form:

| Acts on … | Fields | Reversible? |
| --- | --- | --- |
| **What is stored at all** | Resolution, value change filter | No — what was not stored is gone |
| **How long it stays** | Retention | Yes, until the next deletion |
| **What is shown** | Display name, decimal places, display mode | At any time |
| **What cleanup marks** | Gap detection, outlier detection | At any time |

The first two fields discard measurements on arrival. Everything below can be
changed as often as you like without losing anything.

One exception to this clean separation: **decimal places** is not purely
visual — the value change filter uses the same rounding to decide whether two
values are "equal" (see below).

### App display name

Optional, up to 40 characters. Overrides the presentation **only in
Zeitarchiv** (lists, selection fields, diagrams, tables) — Home Assistant's own
`friendly_name` and the entity ID remain untouched. A tag icon marks
everywhere where a custom name is active. Leaving it empty restores the HA
name.

### Resolution

**Minimum interval between two stored values.** Selectable: raw data, 30
seconds, 1, 5, 15 minutes, 1 hour. "Raw data" stores every incoming state
change and is the default for newly detected entities. The setting applies only
to newly arriving values; already archived ones remain unchanged.

What becomes of values arriving too densely depends on the entity type:

- **Counters** (consumption, generation, anything with a steadily rising
  reading): the last value per time window is kept, the rest discarded —
  counter readings can still be carried on exactly from it. The time window
  lies on a fixed clock grid (with "5 min." always at the full five-minute
  mark), not relative to the last stored value — so after a pause (restart,
  connection dropout) the grid does not shift.
- **Standard entities** (temperature, humidity, power …): all raw values of a
  time window are condensed into one row — average plus min/max, so that a
  short outlier (e.g. a temperature spike) is not lost without replacement. The
  row's timestamp is the end of the time window.
- **Switch entities**: resolution is locked to "Raw data" so that a real state
  change can never be discarded by a time window. Duplicates (unchanged state)
  are still caught by the independent [value change
  filter](#value-change-filter) instead.

If you want to see condensed values over a longer, freely selectable period,
use the aggregation in charts and tables — or, for already archived months,
the [compaction target](#compaction-target) below.

### Compaction target

**Target time grid for retroactive compaction of already archived months** —
independent of the resolution above, which applies only to newly arriving
values. Selectable: Off, 30 seconds, 1, 5, 15 minutes, 1 hour.

Two ways in which a month is actually compacted:

- **Manually** in the entity's editing area, **Compact** tab — choose period
  and target resolution, view the preview, confirm. Not reversible.
- **Automatically**, if enabled under **Housekeeping → Compact**: runs in the
  background as soon as an archived month has reached the minimum age set
  there. **Off** by default.

For counters, the last value per bucket is kept (counter readings can thus be
carried on exactly), for standard entities average as well as min/max per
bucket. An already compacted month is never compacted again for standard
entities; for counters only to a still coarser target. The default for new
entities is the global default under **Settings → Archiving**.

**Not available for switch entities** — a retroactive compaction could
compress away a real state change (ON/OFF).

### Retention

How long values are kept: Unlimited, 30 days, 90 days, 365 days, 2 years, 5
years. The default for new entities is **Unlimited**.

**The field alone deletes nothing.** It only defines what counts as "too old".
Whether and when deletion actually happens is controlled by **Settings →
Retention**: if automatic enforcement is off there, the data keeps
accumulating no matter what is set here. A look at **Housekeeping → Retention**
shows how much would be removed at the next enforcement.

### Decimal places

"Automatic" shows up to three places and omits trailing zeros (4 instead of
4.000). A fixed selection (0–3) rounds to exactly that many places and pads
(4.00 with two places).

**The setting acts beyond the display:** the value change filter decides, using
the same rounding, whether a new value equals the previous one. If you set the
decimal places from 3 to 1 and have the filter active, you thereby also discard
more values — 21.04 °C and 21.03 °C both become 21.0 °C, i.e. one filtered
value. "Automatic" behaves like 3 here.

### Value change filter

Skips values that, according to the decimal places rule, equal the last stored
one — saves considerable space with sluggish sensors.

**At the latest every 6 hours a value is stored anyway**, even if nothing has
changed. This sign of life is the difference between "the sensor reports an
unchanged 21.0 °C" and "the sensor reports nothing at all anymore" — without
it, the two could not be distinguished in the data, and the inactivity notice
would trigger wrongly.

Active by default for newly detected entities.

### Gap detection

From which pause between two values the cleanup page marks a **gap**: 1, 5, 15,
30 minutes, 1, 6, 12 hours, 1 day — or Off. The marking is pure analysis; it
changes nothing in the data.

**The value is raised automatically if it cannot apply.** A resolution of 1
hour already enforces a minimum interval of one hour between values; a gap
detection of 5 minutes would then trigger on *every* normal cycle. The same
applies to the activated value change filter with its 6-hour sign of life.
Zeitarchiv therefore raises the threshold to the next step that still makes
sense when changing resolution or filter, and tells you so. Afterwards it can
be reduced manually again at any time. Existing entities with such a
combination are listed under **Housekeeping → Configuration**.

### Outlier detection

Marks **implausible single values** on the cleanup page: shifted digits,
transmission errors, sensor dropouts. High values are not what is meant — a
hot day or a washing machine that is currently running are not outliers. Like
gap detection, pure analysis, with no intervention in the data.

A **multiple** can be set: 10, 20, 50, 100 — or Off. The default is 50×. A
smaller number means more sensitive.

**Multiple of what? Of what is usual for this entity.** That is the core of the
matter, and it depends on the type:
**Counters** (consumption, generation, anything with a steadily rising
reading) — the reference is the **usual increase**: the median of the last 50
increases. An increase that is a multiple of that is marked. The counter
reading itself plays no role.

> An electricity meter usually grows by 0.0016 kWh per measurement. The
> largest *real* increase over a month was 10 times that. If, on the other
> hand, a digit slips — 10,123 becomes 101,230 —, this one increase is 56,941,875
> times that. Between normal operation and a real error lie six orders of
> magnitude; any threshold in between only hits the error.

A steadily running counter never triggers. Decreases are **not** marked here —
there is a separate mark for that, **Counter decrease**.

**All other sensors** (temperature, power, humidity …) — the reference is the
**usual fluctuation of the last fifteen values**: how far a value typically
lies from the middle of this window. A value that lies a multiple farther away
than that is marked.

> A room temperature oscillates around 21 °C, usually ±0.3 °C. At 20×, what is
> more than 6 °C off gets marked — a single measurement of 60 °C, that is, not
> the normal ups and downs.

Fifteen values as the window means: a slowly drifting signal does **not**
stand out, because the reference moves along. Only what breaks out of its
immediate neighborhood stands out.

**Why a multiple and not a percentage?** Because a percentage means different
things on two sensors of the same kind. 5 % is a full 0.6 on a freshly
connected counter (reading 12) and a full 60,000 on an old one (reading
1,200,000) — the same setting would be strict on one and ineffective on the
other, although both measure the same consumption. The same holds for the
scale: a jump from 20 to 60 is 200 % in degrees Celsius, but in Kelvin (293 to
333) only 13.6 %. A multiple of the usual knows no such dependency: the same
setting means the same on every sensor.

**Two limits worth knowing:**

- **The first values of a period are not checked.** Only when enough history
  exists (five values or five increases) is there a reference.
- **A completely constant signal is skipped.** If all values in the window are
  equal, there is no "usual fluctuation" against which a multiple could be
  measured — then nothing is marked rather than guessing. Rare in practice,
  because the value change filter thins out constant series anyway.

**For switches the setting is not available.** For values that can only be 0 or
1, the usual fluctuation is mathematically always zero — the detection would
never mark anything. A control that demonstrably does nothing would be false
information; the field is therefore grayed out and states the reason.

**What the threshold actually does is shown below the field:** the share of
values it marks across the complete history, with bar and absolute number. If
the rate has not been calculated yet or belongs to a different threshold,
**Check now** appears instead; if it is already there, it can be refreshed
with **Recalculate**. The calculation reads the entire inventory of the entity
and therefore runs only on click.

> **After updating from an older version:** The threshold used to be a
> percentage (5, 10, 25, 50, 100 %). Saved settings are carried over once to
> the new ladder, by their position on it — the most sensitive old step becomes
> the most sensitive new one (5 % and 10 % → 10×, 25 % → 20×, 50 % → 50×,
> 100 % → 100×). "Off" stays off. Since the numbers now mean something
> different, it is worth a look at the rate below the field.

### Display mode

Only for switches (`binary_sensor`, `switch`, `input_boolean`, `device_tracker`, `person`):

- **ON/OFF (raw value)** shows the state as 0/1.
- **Time (duration)** instead shows the cumulative on-time per period,
  formatted readably (e.g. `1h 29m`) — useful for presence, door and motion
  sensors.

The domains `binary_sensor`, `switch`, `input_boolean`, `device_tracker` and
`person` count as switches — for the latter two, `home` counts as "on", any
other state (`not_home` or a named zone) as "off".

### What applies retroactively — and what does not

Resolution, retention and decimal places act **only on values arriving or
being calculated in the future**, never retroactively on already archived
data. A coarser resolution therefore does not thin out the existing inventory,
and a finer one does not bring back anything that was never stored.

Gap and outlier detection, on the other hand, **always act immediately on the
whole inventory**: they are recalculated from the raw values of the displayed
period on every visit to the cleanup page, none of it is stored, and the data
itself remains untouched. A changed threshold therefore applies immediately
and retroactively — there is no inventory of old marks that would have to be
caught up. (The only exception is the rate below the threshold field: it
belongs to a full scan over the entire history and is only refreshed on
click.)

### Data management

At the bottom of the page there are two final actions:

- **Delete all values** removes all data of this entity (current month,
  archive, rollups) but keeps the individual configuration (resolution,
  retention etc.). Useful for starting again from zero with a wrongly
  configured source without having to set the settings anew.
- **Remove entity** additionally deletes the configuration as well. If Home
  Assistant continues to send the entity, it is automatically created anew
  with the current global defaults at the next received value.

Both actions require an unambiguous confirmation before execution (entering
the entity name) and cannot be undone afterwards. Charts or tables that use
this entity simply show no more data for it from that point on.

## Cleanup

Reachable via the **Edit values** tab of an opened entity, containing three
areas:

### 1. Clean up

Detected outliers, gaps, duplicates and repeats that are equal after rounding
are shown as a list, each with a short reason — visible when the mouse pointer
is on the red marker. It names the rule the mark is based on and the
immediately preceding value with its time:

> `3 h 50 min since previous value 21.2 °C at 08:10`
> `21.56 is 23× farther from the median of the last 15 values (24.27)`
> `than usual (±0.12) — previous value 22.06 at 11.08.2026 00:01:41`

Select individual entries or all together and delete — this is initially a
**soft delete**: the values disappear immediately from every display (charts,
tables, raw values) but can be restored via "Undo (last deletion)" as long as
no final cleanup has taken place yet (see below).

For rising counters (Home Assistant `state_class` `total_increasing`, e.g.
energy meters), lower subsequent values are logged separately as possible
counter resets and marked under "Counter decreases"; they remain stored by
default, since a reset (e.g. meter replacement) can be a valid event and is not
automatically treated as an error.

Repeats (subsequent values equal after rounding) can also be condensed
retroactively using the same six-hour sign-of-life rule as the ongoing value
change filter — useful if the filter was only activated later and older data
is still uncondensed.

### 2. Correct

Edit individual values directly (click on the value cell in the raw value
table) instead of deleting — for example to set a recognizable sensor outlier
to a plausible value instead of leaving a gap at that spot. The raw value table
shows the unit directly next to each value.

### 3. Add

Add a missing measurement point manually with timestamp and value — numbers in
German format with a comma as the decimal separator (e.g. `21,5`).

### Header and final removal

The header of the cleanup area shows both the record count in the currently
selected period and the visible total inventory including
outliers/gaps/duplicates/repeats across the **complete** history of the entity
— independent of the section currently displayed.

Soft-deleted values continue to occupy storage until they are physically
cleaned up under **Housekeeping → Storage**. There, a preview shows in advance
how many rows can actually be removed (including a breakdown by current month
and archive) before the step is actually executed. This step is final —
afterwards "Undo" is no longer possible.

Alternatively this runs automatically: the same area has a switch for
automatic cleanup (off by default) with an adjustable minimum age of the
deletion mark (1 week to 3 months). Only when a mark is at least that old does
the automation physically remove it — fresher marks remain untouched so that
"Undo" has a real time window even with the automation enabled.

## Data handling

This section explains in more detail what happens behind the scenes when
values are deleted, changed, added or cleaned up automatically — and how this
affects charts, tables, storage and recoverability in each case.

### The journey of a value

Every incoming value passes through the same stations, regardless of whether
it comes from the integration or was added manually:

```text
Incoming value
      │
      ▼
Hot buffer (current month, uncompressed)
      │
      │  Rotation: automatically at the first value of a new calendar month,
      │  or caught up manually (e.g. for an entity that was silent for a long time)
      ▼
Archive (completed months, compressed)  ──►  Rollups (hour/day · month · year)
      │                                                     │
      │                    Retention                         │
      │     removes entire overdue months from archive       │
      │              AND the associated rollups              │
      ▼                                                     ▼
              permanently removed — no "Undo"
```

For short, recent periods, charts and tables access the hot buffer, and for
longer/past periods the archive and rollups — this switch happens
automatically and is not visible when viewing. The rotation itself changes
nothing about the values; it only moves the current month from the hot buffer
into the archive as soon as it is completed.

### Retention enforcement

The retention period configured per entity (gear icon → **Retention**, see
[Configuring an entity](#configuring-an-entity)) is not applied continuously,
but only when retention enforcement actually runs:

- **Manually** via **Housekeeping → Retention** — with a preview that shows
  what a run would remove before it is actually executed.
- **Automatically**, if a schedule (daily or weekly, with time) is set under
  **Housekeeping → Retention**. A single maintenance scheduler regularly
  checks in the background whether the next planned run is due; if the app was
  not active at the planned time, **at most one** missed run is caught up,
  never several at once.
- A run always affects **only whole, completed calendar months** — a month is
  removed completely as soon as its end is older than the retention period,
  never partially. The archive month and the associated rollup rows
  (hour/day, month, year) are removed simultaneously so that the two never
  diverge; in the current month (hot buffer), overdue rows are removed
  directly.
- Entities marked **Unlimited** are skipped completely.
- Retention, backup, import and rotation never access the inventory at the
  same time — if one of these operations is already running, an automatic
  retention run that falls due at the same time does not wait but is skipped
  for that slot (the next regular slot runs normally).

**Important:** Unlike a changed resolution (which acts only on newly arriving
values, see [Frequently asked questions](#frequently-asked-questions)), a
shortened retention period acts **retroactively** on already stored, completed
months at the next run. Raising a period or setting it to Unlimited, on the
other hand, is harmless at any time — it never deletes anything; what has
already been removed is gone for good.

This deletion is **not** the soft delete from the Clean up tab but immediately
final — there is no preliminary stage and no "Undo". A backup before a
retention period that is activated for the first time or shortened
significantly is therefore recommended.

### Deleting values: three levels

Values that are removed via **Clean up** (individually or as a selection) go
through three clearly separated levels — only the last of them is final:

```text
Value present
      │
      │  Clean up → "Delete"
      ▼
Marked as deleted (soft delete)
  · disappears immediately from charts, tables, raw value lists, export, statistics
  · the file on disk remains unchanged
  · still counts toward occupied storage
      │                                    │
      │  Clean up →                        │  Housekeeping → Storage →
      │  "Undo (last deletion)"            │  "Clean up" (with preview)
      ▼                                    ▼
Value visible again                 Physically removed
                                       · archive or hot buffer file rewritten
                                       · affected rollups recalculated
                                       · final, no "Undo" possible anymore
```

On the marking ("Delete"):

- Nothing is removed from a file — it is merely noted that this occurrence
  (entity + timestamp) is to be hidden everywhere from now on. With two values
  with exactly the same timestamp (duplicate), only one of the two can thus be
  deleted deliberately without taking the other along.
- Each deletion (one click on "Delete", whether one value or a whole
  selection) forms a **batch** of its own.

On "Undo (last deletion)":

- Undoes **only the most recently executed batch** — there is no longer
  history and no jumping back across several deletions. A second click on
  "Undo" without an intermediate new deletion does nothing anymore.
- Works only as long as the batch has not yet been physically cleaned up (see
  below) — afterwards the mark no longer exists, so there is nothing left to
  undo.

On "Clean up" under **Housekeeping → Storage**:

- This is the only step in this chapter that actually changes files on disk:
  the affected archive or hot buffer file is rewritten without the marked rows,
  and the rollup values depending on it are recalculated. If all values in an
  entire archive month are marked as deleted, the month file (including rollup
  rows) is removed completely instead of being rewritten empty.
- A preview shows in advance how many rows can actually be removed (broken down
  by current month and archive) before the step is confirmed.
- Afterwards the affected values are irretrievably gone — even a new backup
  cannot undo this within the app (only a restore from an **older** backup
  created before this step would bring the values back).

### Changing and adding values

**Correct** (editing an existing value) and **Add** (adding a missing
measurement point manually) work technically **differently** from deleting:
these are direct write operations, not the marking model described above.

> ⚠️ **There is no "Undo" for Correct and Add.** Unlike with deleting, no mark
> is set; instead the value is overwritten or added directly in the hot buffer
> or archive file immediately. A value corrected or entered wrongly by mistake
> can only be fixed by correcting it manually again — or, if noticed too late,
> by restoring a previous backup. Before more extensive manual corrections, a
> short look at **System → Backup / Restore** is therefore worthwhile.

With "Correct", if several values share the same timestamp (duplicate), only
the first value found for that timestamp is adjusted, the others remain
unchanged. If the value changes between loading the page and confirming the
Correct dialog (e.g. because it has been deleted in the meantime), simply
nothing happens — without an error message.

### Gaps, duplicates, repeats and counter decreases in detail

The four additional marks in the Clean up tab (besides outliers) each follow a
rule of their own:

| Category | Rule | What "Clean up" concretely does |
| --- | --- | --- |
| **Gap** | The time distance between two consecutive values exceeds the threshold set per entity (**Gap detection**, in minutes; "Off" disables the detection completely) | Only a mark, no automatic action — gaps are shown, not deleted |
| **Duplicate** | Two or more values share exactly the same timestamp (not just a similar one) | The chronologically first stored value is kept, all further ones at the same timestamp are proposed for deletion |
| **Repeat** | A value is (after rounding to the configured decimal places) identical to the last *kept* value — the same rule that is also used continuously for condensing when new values arrive (see [Value change filter](#value-change-filter)) | The first value of a series of equal values is kept, all following ones are proposed — **unless** 6 hours have already passed since the last kept value (sign-of-life rule), in which case an unchanged value is kept as well |
| **Counter decrease** | Only for counter entities: a value is lower than the immediately preceding kept value | Only a mark, **never** deleted automatically — a decrease can be a real event (e.g. meter replacement, reset after restart) |

Important: these marks are **not mutually exclusive**. One and the same value
can, for example, be marked simultaneously as an outlier **and** as part of a
duplicate — the **Mark** selection above the list only chooses which values
carry a particular mark; it does not divide the list into separate,
non-overlapping groups. Each entry of the selection names its hit count in the
selected period; categories without hits are not selectable.

## Housekeeping

A menu item of its own under **System**, below Statistics — collects in one
place what is otherwise easily overlooked, with the same side navigation as the
settings:

| Area | Shows |
| --- | --- |
| **Inactive entities** | Entities without a new value for a selectable threshold (1 to 30 days). Entities never received always appear regardless of the threshold. Usually harmless (standby, rare sensor), but an early hint of a dead integration or a renamed/removed HA entity. |
| **Duplicates** | Duplicate timestamps of the last 30 days detected archive-wide, per entity — the same hourly background snapshot that also triggers the "Duplicates found" notice. Removable via "Remove duplicates automatically" on the respective cleanup page. |
| **Outliers** | Entities for which the configured outlier threshold marks more than 1 % of their values — with threshold, absolute number and rate. Then the threshold is too tight for this signal: what gets marked is no longer the implausible but normal behavior. These are the same numbers shown below the threshold field of the respective entity (see [Configuring an entity](#configuring-an-entity)); the list calculates nothing of its own. No bulk correction — the appropriate threshold depends on the signal. |
| **Configuration** | Entities whose gap detection can structurally never apply because the chosen resolution or the active value change filter itself already enforces a larger minimum interval between values (see [Configuring an entity](#configuring-an-entity)) — with resolution, current and recommended gap detection per entity. Purely informational, no bulk correction: the appropriate target value differs per entity. |
| **Storage** | Free storage on the host file system (tile with utilization bar — a different question from the numbers below, not Zeitarchiv's own storage consumption). The "Check storage" button triggers two separately reported checks at once: **Index consistency** (does the derived index cache match archive/hot buffer — deviations can be fixed via "Repair index") and **Data integrity** (are the raw data in the hot buffer itself still readable — damaged rows almost always arise from an unclean restart, power failure or hard kill, and are not repairable; the affected entity and month are listed, the value remains irretrievably lost). Below: permanently remove marked records from hot buffer and archive. The "Show marked records" button lists them beforehand in a searchable way — first per affected entity with count and last marking time (search field, pagination), a click on a row shows below, in the same dialog, the individual marked points in time including value. Purely read-only: the actual undoing of an individual mark continues to run via the entity itself (see [Cleanup](#cleanup)). Below that, the switch for automatic cleanup (off by default) and the minimum age of the deletion mark for it — removes only marks that are at least that old so that "Undo" for a row that was just deleted does not lead nowhere. If the preview reports "Deletion marks without matching raw data row", the associated raw data row had already been removed by a compaction or an expired retention — both have since cleaned up the affected marks themselves, so this number tends toward 0 over time. |
| **Retention** | Overview of currently due and already deleted records; preview of due deletions; schedule for automatic enforcement (daily or weekly with weekday); run history. |
| **Compact** | Switch for the automatic, retroactive compaction of archived months (off by default) and the minimum age for it — affects only entities with a compaction target set (see [Configuring an entity](#configuring-an-entity)). The manual Compact action, on the other hand, lies with the respective entity itself, in the editing area. |
| **Activity** | The latest Correct, Add, Clean up, Compact and Retention operations in a list, with entity, trigger (manual/automatic), row count and status — filterable by entity, action type, status and period. A click on a Compact or Clean up row shows its detail (target resolution, affected period, rows before/after). Backup is not included there — it affects the whole installation, not individual records. |
| **Rotation** | Entities with a previous month not yet archived (normally happens automatically at the next received value) — can be caught up manually if needed, e.g. when an entity has sent no values for a longer time. |
| **Unused items** | Charts and tables that are pinned to no dashboard — open or delete directly. Disappears from the list automatically as soon as pinned anywhere. |

Each area is linked from the matching system notice (see below) if something is
currently pending — Housekeeping itself does not have to be visited regularly
for that.

**Where the outlier rates come from:** Determining them means reading the
complete history of an entity — for all entities at once that would be too
expensive on every page visit. Zeitarchiv therefore calculates one entity
after another in the background and refreshes each about every six hours.
Below the list it says how far along this is ("4 of 5 entities … measured");
anyone who does not want to wait will find the **Check now** button on the
entity's configuration page. An empty list with measurements still open thus
means "nothing found so far", not "everything checked" — which is why the
section names the highest value measured so far even then.

### What is running right now

Some actions take noticeable time: a Symcon trial run over a few hundred
variables takes minutes, a cleanup over many years of history seconds to
minutes. So that it is never unclear whether something is still happening,
they show their status — depending on where they are triggered — in up to
three places:

- **On the button.** It turns pale for the duration of the request, gets a
  small rotating ring and cannot be pressed a second time. If it takes longer
  than three seconds, a clock additionally counts along in the button.
- **Below the button.** Where there is an honest number (imported rows, checked
  variables, cleaned months), a progress bar with "X of Y" appears. If there is
  no reliable total, the display deliberately shows only the number reached so
  far instead of estimating a percentage — and where there is nothing to count
  at all (index check, rotation), it stays with the button.
- **At the bell.** A second, colored badge on the **left** of the bell (the red
  one on the right stays reserved for problems), and in the panel above it the
  section **Running now** with operation, current step and, where available, a
  bar. It flashes briefly when an operation starts or finishes — not
  permanently. If several run at once, their number appears in the badge.

The bell is the most reliable place: only it knows all twelve operations, and
they keep running in the background even if the page is switched or the tab
closed. And some of them — backup, cleanup, retention, rotation, index
optimization — pause all other write access for their duration, including
ingestion from Home Assistant. If Zeitarchiv seems sluggish for no apparent
reason, the explanation is there.

Three of these operations start on their own, without anyone having pressed
anything: the storage index check shortly after the app starts, the
subsequent build-up of the hourly analysis after a change in the energy
dashboard, and the recalculation of aggregates when Home Assistant suddenly
delivers a different measurement type for an entity. They too appear in the
list.

### System notices

The notice center (bell in the header) collects hints that disappear
automatically as soon as their cause is resolved — no separate "done" status
needed. Besides update availability, recommended index optimization and failed
backup/retention runs, Zeitarchiv checks, among other things:

- Storage index check incomplete or with deviations found (usually already
  repaired automatically)
- Damaged raw data found in the hot buffer (irretrievably lost rows, usually
  after an unclean restart)
- Maintenance scheduler or storage index background reconciliation has not
  responded for more than 5 minutes (self-healing protection)
- No automatic backup schedule active
- Retention configured for entities, but automatic enforcement switched off
- Last import failed or only partially completed
- Final cleanup possible, duplicates found, rotation pending
- Inactive entities, three-tiered by age (1/3/7 days, with increasing
  severity)
- Gap detection of an entity can structurally never apply because of its
  resolution or the active value change filter (see [Housekeeping →
  Configuration](#housekeeping) and [Configuring an
  entity](#configuring-an-entity))
- Outlier detection marks more than 1 % of all values for at least one entity.
  A single notice for the whole installation, even with a hundred affected
  entities: it names the count and the most pronounced case and leads to the
  list (see [Housekeeping → Outliers](#housekeeping)). Pure information, hence
  mutable
- Daily load profile in the energy dashboard is still being completed
  retroactively after a configuration change
- Free storage on the host file system is running low (two-tiered:
  warning/critical) — a different question from Zeitarchiv's own storage
  consumption
- Unpacked import source data is still in the data directory (from 100 MB and
  at the earliest one day after the last change). They deliberately stay so
  that mapping and trial run can be repeated without uploading again — after a
  completed import they can be removed under **Import** with "Delete data".
  Pure information, hence mutable
- Connected Home Assistant integration is outdated or a newer version is
  available (separated into bugfix/feature update)

All notices except genuine errors can be muted via the 🔕 icon (1 hour to
permanently) — viewable and restorable early under **Settings → Notices**.

### Tips

A short practical tip about app features also rotates in the notice center —
30 tips in total, changing daily. Under **Settings → Notices**, the tip display
can be switched off completely or a dialog can be opened with all tips and
their current status; in it, the currently displayed tip can be hidden for the
rest of the day without interrupting the rotation.

## Import and export

Reachable via **Import**, four tabs:

### Symcon

Upload the ZIP of the `db` folder, optionally add a `settings.json` for names
and units. Then: check the variables and map them to the desired Home
Assistant entities. If source and target units differ (e.g. `klx` in Symcon vs.
`lx` in Home Assistant), a notice appears and a conversion factor can be
entered (here `1000`). Before the actual import, the mapping can be checked
once more.

For already completed months with an existing archive file, month granularity
applies: the whole month is skipped, even if it actually contains gaps — see
"Duplicate protection" further below.

### CSV

Freely map separator as well as time, value and target column, and check the
result before the actual import (preview of the first rows with detected
values).

### Home Assistant

Take over existing recorder data directly from the running Home Assistant
instance, without Symcon or an uploaded file. Only entities already known in
Zeitarchiv are available for selection — that is, those configured by the Home
Assistant integration that have transmitted at least one live value.

The recommended **Full import** connects both sources automatically. The
individual modes remain available for targeted imports:

- **Full import:** first determines the raw history actually available and
  supplements the older hourly statistics before it. The interface is placed,
  for each entity individually, on the next full hour from the first retrieved
  raw value — but only if the statistics extend gaplessly up to that hour
  there. The last known raw state is carried forward at this boundary;
  statistics buckets end exactly before it. If there is a gap between the two
  HA sources themselves, nothing is rounded: the boundary then lies exactly at
  the first available raw value so that Zeitarchiv does not artificially
  enlarge this gap. This way Zeitarchiv never creates an overlap and never
  enlarges an existing gap between the sources.

- **Raw history:** individual measurements via the Home Assistant REST API.
  Home Assistant, however, keeps these by default for only a few days, thus
  covering only the most recent past.
- **Long-term statistics:** hourly/daily aggregates that Home Assistant retains
  permanently by default (mean or running sum, depending on what the entity
  carries in Home Assistant) via the Home Assistant WebSocket API — thus also
  covering considerably older periods, but only as an aggregate instead of an
  individual measurement. Available only for entities with a Home Assistant
  `state_class` (usually `sensor.*` entities), recognizable by the marking
  "Not supported" in the "Kind" column for all others.

Procedure: choose import mode and period (the available presets differ by
source — for long-term statistics, for example, "Last year" is additionally
available), optionally "Check availability" for a preview of which entities in
Home Assistant actually have data of the chosen source and for which period.
Only the marked entities are checked; unmarked rows remain unchanged. The check
result is kept per source/resolution — even after a page change or a switch
between full import, raw history and long-term statistics, until the next
restart of the add-on. A status chip to the right of "Check availability"
shows the running check status and afterwards the time of the last check; from
15 minutes on, a notice appears that the state may be outdated.

With the full import, raw and statistics periods are chosen separately. The
dry run reports for each entity both ranges used, the calculated interface,
deliberately discarded transition values and the carried-forward raw value
anchor. Entities without long-term statistics are still imported completely
with their available raw history; a failure of one source does not prevent
successfully retrieved values of the other source from being processed.

The current calendar month is always imported automatically into the hot
buffer, independent of the data already present. Without the option "Fill
archive gaps", an already completed month with an existing archive file is
skipped completely — as with Symcon and CSV (see "Duplicate protection"
further below). Only with the option activated are such months supplemented
row by row with missing timestamps; existing timestamps and values remain
unchanged.

After a dry run, a debug file can be downloaded as a ZIP. It contains all
retrieved, adopted and discarded values relevant for diagnosis including
reasons, source ranges and interface, month assignment, import plan and
current archive/hot buffer state. Access tokens and authorization headers are
not included. Since the export nevertheless contains measurements and entity
metadata, it should be shared only selectively.

This import requires the add-on permission `homeassistant_api` as well as a
Home Assistant installation with Supervisor (not available with Home
Assistant Container).

### Reports

Every import actually executed (Symcon, CSV or Home Assistant) remains
traceable here with source, mapping, runtime, imported and skipped records and
any errors. Pure previews (e.g. "Check availability") produce no report. The
list can be filtered by source and status (takes effect immediately on
selection) and sorted by any column; a click on a row opens the detail view
with JSON download. Reports can be displayed page by page and deleted
collectively when no longer needed. Home Assistant reports distinguish raw
history and long-term statistics in the full import and report newly archived
values, additions to the current month, filled archive gaps and values rescued
from an invalid current archive into the hot buffer separately.

### Duplicate protection

The current calendar month always lands in the hot buffer and is deduplicated
row by row in the process: only timestamps not yet present there are added.

For already completed archive months, the behavior depends on the source. With
**Symcon and CSV import**, month granularity applies: if an archive file
already exists for a month, the entire month is skipped — even if it actually
contains gaps. A subsequent supplement is not possible here; for that, the
month has to be deleted manually if necessary and imported again. With the
**Home Assistant import**, this can be lifted deliberately with the option
"Fill archive gaps": then even completed months are supplemented with missing
timestamps.

In all cases: existing measurement points of the same entity and the same
timestamp are skipped — even with a different event ID — and never replaced. A
repeated Symcon or CSV upload of the same source therefore duplicates nothing,
nor does a repeated Home Assistant import over the same period.

### CSV export

From an entity's history view: download the complete raw data history of this
one entity up to the export limit as CSV.

## Backup / Restore

Menu item of its own **System → Backup / Restore** (not under Settings):

- **Create backup:** complete inventory (index, hot buffer, monthly archives,
  rollups) as a ZIP, directly downloadable. In addition to, not instead of, the
  automatic Home Assistant snapshots — a Home Assistant snapshot backs up the
  add-on state as a whole, a Zeitarchiv backup is portable independently of it
  and can also be kept outside Home Assistant.
- **Import existing backup:** upload a backup ZIP file by drag &amp; drop or via
  the file dialog — e.g. one that comes from another Zeitarchiv installation or
  was kept externally (Home Assistant reinstallation, device move). The file is
  checked completely (checksums, ZIP structure, index integrity) before it
  appears in the list as a restorable backup — with a file that is too large or
  damaged, the existing inventory remains untouched. Upper limits: 2 GiB
  upload, 5 GiB unpacked.
- **Schedule:** automatically on a schedule (interval, time, weekday if
  applicable), with automatic cleanup of older backups by count and/or age so
  that storage does not grow without limit.
- **Check:** checksum check of an existing backup without applying it — useful
  to confirm the integrity of a backup before an actual need for restoration
  arises. Applies equally to self-created and imported backups.
- **Restore:** only prepares the swap — it is applied only at the **next
  restart of the Zeitarchiv add-on**, before the database is opened again.
  After the click, a dialog actively asks "Restart now?"; a confirmation
  triggers the restart directly via the Supervisor, "Later" postpones it (the
  restore remains scheduled anyway and is applied at the next regular restart).
  Without a restart, the current inventory remains usable unchanged until
  then. At restart, the previous state is moved into a restore rollback before
  being overwritten, not deleted — so if needed the state before the
  restoration can be brought back (see below). After a restore, a short look at
  **Statistics** is recommended to check whether the expected entities and
  record counts are back.
- **Run history:** lists every backup run as well as every restoration actually
  applied (its own trigger "Restoration") with time, status, duration/"—" and
  size. A failed or interrupted run is marked red; a click on the row shows the
  reason for the error.
- **Restore rollback:** the intermediate state created automatically on
  restoring, immediately before the last restoration, listed with time and
  size. Two actions: "Restore this state" undoes the last restoration (runs via
  the same restart mechanism as above — the current state is itself kept again
  as a new rollback), or "Delete rollback" removes it immediately. Only the
  latest one is ever kept — a rollback undoes only the immediately preceding
  restore, not a longer history; anything further back is retrieved via a
  regular backup. A new restore replaces it automatically, it does not count
  among the regular backups and therefore needs no schedule of its own.

## Demo mode

Lets the app run completely separated from the real archive data with
synthetic showcase/test data — a household with rooftop PV system, wallbox,
balcony power plant, home battery and around 50 further sensors, all with
prefix `demo_`. Handy for a first impression before connecting to Home
Assistant or as a permanent shop-window instance, e.g. for screenshots or for
trying things out without changing your own system.

### Turning it on

Demo mode is an add-on option, not a click in the app itself:

1. In Home Assistant, go to **Settings → Add-ons → Zeitarchiv →
   Configuration**.
2. Enable the option `demo_mode` and save.
3. Restart the add-on (the Supervisor offers this by itself after saving).

After the restart, the app works exclusively with the demo data — the real
archived values remain unchanged in a completely separate directory
(`<data directory>/demo` instead of the real data directory) and are never
read, changed or overwritten at any time. If no demo history exists yet at the
first start in this mode, the app generates it automatically (about 3 years,
takes about a minute in the background — the app is reachable normally in the
meantime but shows complete values only afterwards).

Changing the option **always** requires a restart, like any add-on
configuration change — there is no switch that changes between real and demo
data without a restart.

### The "Demo data" section

As long as relevant, an additional section "Demo data" appears under
**Settings** between **Diagnostics** and **About Zeitarchiv**, in one of three
states:

**Demo mode active.** Shows the scope (entities/values) and occupied space of
the demo instance, when it was last supplemented automatically and when the
next supplement is due. Three controls:

- **Supplement automatically** (choice: Off, every 5/15/30 minutes, hourly) —
  keeps the demo instance at the current date by itself without anyone having
  to act. Default is **Off**.
- **Supplement now** — immediately supplements the values since the last run
  without touching the existing history. The same happens automatically when
  "Supplement automatically" becomes due.
- **Regenerate** — discards the complete demo history and rolls it anew (with a
  confirmation prompt). Useful for starting again with "fresh" 3 years.

There is deliberately no "Remove" here: as long as the instance itself runs in
demo mode, deleting would pull its own running database out from under it. For
that, first turn off demo mode as described above and restart.

**Demo data is being generated.** Appears shortly after turning on (first
start) as well as while "Supplement now"/"Regenerate" are running — a progress
bar shows the status per entity. Other pages of the app remain normally usable
in the meantime but show the new or complete values only after completion.

**Demo mode is off, but demo data is still present.** Appears when the option
was deactivated again without removing the demo data beforehand — it stays on
disk until removed manually. Shows occupied space, approximate entity count and
the time of the last change, plus the **Remove demo data** button (with a
confirmation prompt, **final**: values, configuration and index of the demo
instance are deleted completely). A notice in the bell additionally reminds you
as long as the data stays.

### Safety and limits

- Demo and real data never share a directory — not even when switching. Turning
  demo mode off makes the real data visible again, unchanged since the last
  time.
- Demo mode is intended for a single app instance, not for parallel operation
  with a second one that writes to the same directory at the same time.
- The connected Home Assistant integration can display the operating mode
  (production/demo mode) of an instance via a sensor entity of its own — see its
  [documentation](https://github.com/bertel2020/HA-Zeitarchiv).

## Log

A menu item of its own under **System**, right next to the settings — shows the
current app messages in the interface, without a detour via the Home Assistant
sidebar.

| Element | Effect |
| --- | --- |
| **Source**: Live / Supervisor history | "Live" reads the limited buffer of the running process — reacts fastest. "Supervisor history" reaches further back but takes somewhat longer to load when opened. |
| **Level** | Filters the display to a minimum severity (All, Errors, Warnings, Information, Debug). Affects only the presentation — not the application log level itself (see below), which determines what is written at all. |
| **Search log** | Free-text search within the currently loaded lines. |
| **Refresh now** / **Automatic (15 s)** | Manual or automatic reloading. |
| **Download** | Downloads the current view as a text file. |

Above it lies the actual setting, **Logging**:

| Setting | Options |
| --- | --- |
| **Application log level** | Errors, Warnings (default), Information, Debug. |
| **Log HTTP requests** | Off, Failed requests only (recommended), All requests. |

For normal operation, `Warnings` and **Failed requests only** are the
recommended settings — both take effect immediately, without a restart.
`Debug` and the entity trace (see [Settings in detail →
Diagnostics](#settings-in-detail)) are intended to be time-limited for
troubleshooting and generate noticeably more data. Credentials are always
masked before output; write captures and entity traces can nevertheless
contain entity IDs and measurements and should remain active or saved only as
long as necessary.

## Settings in detail

| Area | Contains |
| --- | --- |
| **Appearance** | Language (Automatic/German/English — default German; "Automatic" follows the browser language; untranslated places appear in German), number and date format (Automatic/German/English UK/English US — "Automatic" follows the browser: `en-GB` → `1,234.5` and `07/10/2026`, `en-US` → `10/07/2026` with a 12-hour clock, `de-*` → `1.234,5` and `07.10.2026`; independent of the language; CSV exports and file names stay unchanged), currency (Automatic/EUR/GBP/USD/CHF — "Automatic" takes it from Home Assistant, otherwise euro; amounts are not converted, enter your prices again after changing it), start page (overview/energy dashboard), color scheme (Zeitarchiv/Home Assistant/Modern), light/dark/automatic, font size, dashboard tile fade animation, starting values for the chart options of the entity history view |
| **Archiving** | Default values for newly detected entities (never act retroactively on existing entities): resolution, retention, decimal places, value change filter, gap/outlier detection |
| **Notices** | Switch the tip display on/off and dialog with all tips (see [Housekeeping](#housekeeping)); overview of muted system notices with remaining duration, individually re-enableable early |
| **Connection** | Show/regenerate API token, last received value, number of writes and auth errors since start, connected integration version with "last seen" time (notice for outdated or newly available version) |
| **Diagnostics** | Record the next write once completely (sensitive raw data, automatic deletion after 60 minutes at the latest); trace a single entity for 15 minutes including ingest result; download diagnostic report; process start and uptime; **Background processes** overview (last run/status of each maintenance scheduler task) |
| **Demo data** | Only visible with active demo mode or a leftover demo instance — see the separate section [Demo mode](#demo-mode) |
| **About Zeitarchiv** | Version (with notice as soon as an update is available), time zone, data directory, links to documentation/changelog/bug report |

A newly generated API token under **Connection** replaces the previous one
immediately — the Zeitarchiv integration must then be updated with the new
token, otherwise further write attempts fail.

Application log level, HTTP access logging and the log view itself are not a
settings section but are on the separate page [Log](#log).

## Frequently asked questions

**Does a changed resolution affect already stored values?**
No. A changed resolution acts exclusively on values arriving in the future,
never retroactively on already archived data.

**Does a changed retention period affect already stored values?**
Yes, as soon as retention enforcement runs next — unlike with the resolution,
this is deliberately meant to be retroactive here: a shortened period then also
removes long-archived, overdue months. Details and how to handle this safely
(preview, Unlimited, backup beforehand) are under [Data handling →
Retention enforcement](#retention-enforcement).

**Is a deleted entity really gone?**
After "Delete all values" or "Remove entity", yes, for good. Beforehand, a
backup is worthwhile (see above) in case the deletion was a mistake.

**Why does a chart see no new values despite an active integration?**
Usually a suitable archive filter is missing in the integration (see [First
steps](#first-steps), step 4/5), or token/host in the integration configuration
do not match **Settings → Connection**.

**Can I use a chart or a table for several dashboards?**
Yes — one and the same chart or table can be pinned to any number of
dashboards; there is only one shared definition, and changes take effect
everywhere at the same time.

**What happens to a tile when the underlying chart or table is deleted?**
The tile disappears from all dashboards on which it was pinned.
