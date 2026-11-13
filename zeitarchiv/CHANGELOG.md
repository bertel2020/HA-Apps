# Changelog

> ### 🗂️ Your data stays yours. Do you like the archive?
>
> Zeitarchiv is a hobby project and stays free. You can help in three ways:
>
> - ⭐ **Star it on GitHub**: it makes the project easier to find
> - 🐞 **Report a bug or an idea**: as an [issue](https://github.com/bertel2020/HA-Apps/issues/new)
> - ☕ **Tip the archivist**: [Buy Me a Coffee](https://buymeacoffee.com/bertel2020) · [Ko-fi](https://ko-fi.com/bertel2020) · [PayPal](https://paypal.me/RobertoMartins)
>
> 🇩🇪 [Änderungen auf Deutsch](https://github.com/bertel2020/HA-Apps/blob/main/zeitarchiv/CHANGELOG.de.md)

## 1.50.0 - 2026-11-13

### New

- **Dashboards**: History popup instead of a page change
  - Clicking a chart, table or value tile opens its history as a popup over
    the dashboard; the dashboard stays where it was
  - Period, navigation, comparisons and options work as on the page itself;
    “Edit” and “Open as page” lead to the full page
  - On smartphones the popup slides up from the bottom
  - Energy dashboard popup: same frame and close button as the tile popup,
    larger, axes in the app format

- **Charts**: Several stacks in one chart
  - With “Stacked” on, every bar series in “Selected” can be assigned to a
    stack; bars without a stack stand next to the others
  - Stacks can be added, named and dissolved; entries are assigned by
    dragging or with the “Stack” field
  - The tooltip lists the entries of a stack together, with the sum below;
    “Shares (%)” is calculated per stack
  - The legend groups the entries of a stack: as a header row in the table, as
    a shared capsule with chips, each with the sum
  - Also at resolution “Full” and horizontal: every stack sits in its own
    category, the other entries stand next to it
  - The order within a stack can be changed in the stack block, the colors
    stay the same
  - Also together with the previous-year/previous-period comparison: every
    stack gets a paler comparison stack next to it
  - Also applies on dashboards and in the card; without a choice everything
    stays in one stack as before

- **Charts**: Line or bar per series
  - In “Selected”, every row of a counter or measurement has an “Appearance” field: Auto, Bar or Line; “Auto”
    stays as before (counters as bars, measurements as lines)
  - This allows, for example, a total consumption as a bar next to stacked single bars, or a daily value as a line
    over bars
  - A line also has “Own axis”: small values next to large ones stay readable, and the axis takes the color of
    the line
  - Lines do not take part in stacks and in “Shares (%)”
  - Also applies on dashboards, in the popup and in the card

- **Weather dashboard**: Sunshine and sky condition by sun position (Settings →
  Sun and daylight)
  - Brightness is related to a clear sky at the current sun elevation, so the
    same percentages apply all year
  - Applies automatically once the “Sun elevation” role is set; without it,
    or with the switch off, the fixed lx values stay in use
  - Adjustable: the share for sunshine (default 60 %) and a calibration of the
    clear-sky curve to your own sensor

- **Tables**: New “+ Blank row” adds an empty row between sections; it is a
  separator with the line hidden (option “Hide line”)

- **Entities**: The selection assistant offers matching dashboard roles
  - Under “Selection & history”, recently added entities show which empty role
    in the weather or energy dashboard they could fill; “Assign and open the
    dashboard” opens the setup prefilled, saving happens there
  - For the energy dashboard, the energy configuration of Home Assistant is the
    basis (grid, producers, storage, consumers); the setup has a button “Take
    from Home Assistant” for it
  - Hits in the list show which empty role of the weather dashboard they could
    fill
  - A new view “Dashboards” lists the roles still empty per dashboard, with
    suggestions as a standard table

- **Dashboards**
  - Title and period bar stay at the top while scrolling
    - Applies to dashboards with period navigation and the overview; in the
      weather and energy dashboards the tab row with the period controls stays
      in place
    - In the weather and energy dashboards the period controls sit in the tab
      row, which stays in place while scrolling; on the “Long term” tab it
      holds the year selection
    - On smartphones the bar scrolls with the page
  - Unlock by clicking the lock
    - The lock next to the title asks for confirmation and switches editing
      back on, no detour via Dashboards → Edit needed
  - Link back to the dashboard on chart, table and entity pages
    - Opening a tile shows “← back to dashboard” (or “to the overview”) at the
      top, even if the browser does not report the originating page
    - “Used in” now opens the default dashboard as the overview, like the
      navigation

  - Cards with a soft shadow in the weather and energy dashboards, same
    spacing below the top bar everywhere

### Changed

- **Dashboards**
  - Load faster, especially with many entities and on slow systems
    - Tiles, weather and energy dashboards refresh long periods less often and
      without flicker
    - The tile menu no longer repeats the entity list per tile
    - The bell in the top bar does not poll while the tab is hidden
    - Comparison tables of the weather and energy dashboards and the temperature
      chart of the weather dashboard load faster
  - Value tiles without a line through the number when low
    - On low tiles (precise mode, smartphone, large text) the line above the
      min/max values no longer runs through the main value; the sparkline is
      shortened instead

- **Weather dashboard**
  - The hint for the “Sun elevation” role now mentions the “Solar elevation”
    sensor of the Sun integration
  - The daily picture starts with the current day and looks back to the right
  - The wind tile of the overview shows today’s low and high again
  - Entries switched off in the legend of the heating chart and the monthly
    comparison are kept, also after changing room temperature or heating limit
    and after reloading

- **Tables**
  - “Equal columns” no longer truncates the label column
    - The width follows the longest label instead of the width at the time the
      option was switched on; widths set with the drag handle still apply
  - Formulas follow their rows when deleting and duplicating too
    - Row letters stay alphabetical from top to bottom; formulas such as
      “A / B” are updated when deleting, duplicating and when switching to or
      from “Separator”, not only when moving
    - If a formula points at a deleted row it shows “?” and the cell shows an
      error instead of a wrong value

- **Selection assistant**: Recommends less
  - Temperature & climate: only sensors with an area; targets and offsets are
    listed under “More matches”
  - System & server: only the main values of the host, no single values per VM,
    container, disk or app
  - Batteries: “Battery low” only if the device has no percentage value
  - Doors & windows: opening sensors are listed under “More matches”
  - Duplicates: the same device from two integrations is listed once; the
    other one appears under “More matches”
  - Pre-selection: only the key points are ticked; some topics start without
    ticks
  - The tile shows “recommended” and how many of them are “new”

- **Housekeeping**
  - Outliers: A lasting level change is marked only once, no longer up to eight times
    - After the update the background runs re-measure the outlier rates; until then they show as “not measured”, for
      power sensors they are expected to drop noticeably
  - Counter decreases: more accurate classification as a bad value
    - A decrease counts as a bad value if the reading reaches the previous value again within 30 minutes (the first
      following value always counts); before, the longer of 30 minutes or 10 values applied, for slow senders hours
    - A decrease across midnight that returns straight away is a bad value, not a daily reset
    - A run of low values counts as one bad value and is marked as a whole
    - Counter outliers: after a decrease the reading before it stays the reference, the good value after it is no
      longer marked as an increase; after a pause the threshold is scaled with the gap
  - The “Entire period” row shows the old numbers first after deleting and undoing and recounts in the background,
    with a note under the table; the click no longer waits up to 20 seconds
  - The duplicate numbers of the matrix and of removal are explained side by side
  - Safer after a crash: rollups interrupted during rotation, removal of marked values, import or rebuild are
    caught up at startup
  - The forms of the interface can no longer be submitted from foreign websites
  - Error messages of jobs no longer show server paths, only the cause and the file name

### Fixed

- **Weather dashboard**: The rain tile no longer breaks the page when there is
  no sensor tile for it

- **Housekeeping**
  - Compact: Values marked as deleted flowed into averages and last values and could not be brought back; they are
    now dropped as with removal
  - “Undo” reverts exactly the deletion shown; a second submit finds nothing
  - Duplicates: removal keeps the highest value, and ticking a single duplicate marks exactly that row
  - Add raw value: “21.5” was stored as 215; the input is read strictly, the message names the stored value
  - Adding and correcting values updates hints, counter decreases, min/max and the last value at once; NaN and
    infinity are rejected
  - Bad-value rule: an error with one counter no longer holds up the others
  - The cleanup view shows a note when an action fails; the last page of long periods no longer uses all the memory
  - Period jump: with the browser in a different time zone than the add-on, a jump to today’s date around midnight
    landed one period off
  - English interface: marking reasons and dates in cleanup were partly German

## 1.40.0 - 2026-11-10

### New

- **Appearance**: Wide layout (experimental) under Settings → Appearance, off
  by default
  - Overview, dashboards, charts, tables and entities use more width on large
    screens (from 1200 px up to 1920 px), energy and weather dashboard up to
    1440 px
  - At most 5 cards per row (weather dashboard: 6 tiles); the hints behind the
    (i) start collapsed
  - Per dashboard: 4 tiles per row (from 1500 px), a hint suggests it and can
    be dismissed

- **Energy dashboard**: Layout like the weather dashboard
  - New "Comparison" tab with "Periods compared": balance and consumers in one
    table, with reference date, previous-year columns, filter and sorting;
    "Measured" shows how much your own meters record
  - With several producers the table lists them individually, with the
    specific yield per plant (can be switched off in the setup); in the annual
    balance the generation is then stacked by producer
  - "Breakdown of consumption" as a stacked bar per period ("Comparison" tab) and per year ("Long term" tab)
  - "Long term" shows one quantity over all years, month by month, as a table
    or lines, and now also an overview
    - Annual balance with self-sufficiency and self-consumption, two new
      records, new notable days ("Days without grid import", "High feed-in")
    - "Years compared, day by day" as colour fields
    - "Yield and weather": yield per sunshine hour and consumption against
      temperature, once the weather dashboard knows the values
  - "What has changed?" is gone, the consumer table shows the change

- **Weather dashboard**: More sensors by room and floor
  - Up to 20 additional sensors (was 5), temperature and humidity only
  - "Periods compared" with a collapsible block per room, the room comes from
    the area in Home Assistant (and can be set by hand); with floors the rooms
    sit below them (needs the new version of the integration); the Lovelace
    card shows them as well
  - "Averages only" shows one row per sensor
  - "Temperatures compared" and "Humidity compared": rooms can be shown and
    hidden
  - "Precipitation" tab: new table "Precipitation per month" (4 years, with the
    wide layout 5 from 1440 px; plus average, deviation, rain days); the chart
    lists the range and the daily values in its legend, and the daily values
    have their own axis

- **Demo mode**: The demo now comes with a ready-made interface and covers 5
  years of history
  - Energy and weather dashboards set up, three dashboards ("Overview",
    "Energy", "Weather"), five rooms for "Temperature & climate"
  - "Regenerate" recreates everything; your own charts and tables are kept

- **Charts**: The legend can be switched off and shown in two sizes (Normal,
  Small), per chart or as the default under Settings → Appearance

- **Tables**: New row aggregation "Current value", e.g. for the battery level;
  a hint in the editor helps when a label and the period type do not match

- **Refresh**: Open tables, charts and an entity's history reload their values
  every minute while the tab is visible
  - A chip shows the state, explains pauses and offers "Refresh now"
  - Can be switched off under Settings → Appearance

### Changed

- **Weather dashboard**: "Temperatures compared" shows 7 days, 30 days or 12
  months (was 7, 30 or 90 days)

### Fixed

- **Energy dashboard**: The "Long term" tab stopped working as soon as an
  area was switched off in the setup
- **Charts**: The values in the legend no longer run past the edge on narrow
  screens but wrap onto a second line
- **Dashboards**
  - In precise mode a tablet in portrait (521–820 px) now shows two instead of
    three tiles per row, as in normal mode
  - The state donut adjusts its size when the tile changes
  - A copy now takes over precise mode, "Fill gaps" and period navigation from
    the original

## 1.31.1 - 2026-11-08

### Changed

- **Charts**: The previous-year comparison shows the comparison series over the
  whole previous-year period (for a running month, the whole month of last
  year)
  - The "up to the same day" total and the comparison tables stay on the same
    reference day

### Fixed

- **Security**: A sensor's unit is now escaped in chart tooltips (monthly
  comparison and precipitation in the weather dashboard)
- **Charts**: A chart can no longer be saved with more than 25 entities; such a
  chart could not be displayed afterwards
  - The editor blocks the 26th selection with a notice
  - A chart that is already too large stays editable
- **Chart editor**: A server error is shown as an error with "Try again"
  instead of "No data"; after clearing the selection, a request still in
  progress no longer shows anything
- **Tables**: A loading error in the table editor is shown as an error instead
  of empty cells
- **Messages**: A message that directly followed a confirmation disappeared
  immediately
- **Mobile menu**: The closed menu can no longer be reached with the Tab key
- **Year comparison**: The legend calculates the previous year up to the same
  day and shows the correct year, even if the browser is in a different time
  zone
- **Weather dashboard**:
  - A slow earlier selection no longer overwrites a later one
  - Enter in a text field of the setup no longer submits the form
  - The monthly comparison stays visible with "Try again" after a loading
    error
  - The limits of the sky classes can be left empty even with a very low or
    high sun threshold
  - An unknown pressure unit no longer produces a trend; PV power is also
    recognised with "KW" or "w"
  - A value that has held since the evening before now also fills the start of
    the day when an evaluation begins on exactly that day (1 January, the
    start of a month for the notable days)
  - Record values no longer overflow their tile at medium widths
  - The dialogs have a name for screen readers
- **Demo mode**: The weather history had a duplicate data point at one spot

## 1.31.0 - 2026-11-07

### New

- **Weather dashboard**: Clicking a tile in the day overview opens the day in
  detail.
  - Light curve with the sun threshold
  - Temperature, precipitation and wind of the day, where assigned
  - Fourth tile: air pressure, otherwise humidity, otherwise peak illuminance
  - "Set as reference day" applies the day to the whole page
- **Weather dashboard**: The setup explains what each role expects.
  - Short help with examples for each role in the selection dialog
  - "Precipitation (amount)" needs a counter (daily or total amount); sensors
    that only provide instantaneous values are shown greyed out under "Not
    selectable"
- **Weather dashboard**: The tiles have a new look, with an icon and a larger
  value.
  - Tile style selectable in the setup: "Badge" or "Tinted surface"
  - Applies to the overview and to the figures under precipitation and
    daylight
- **Weather dashboard**: The Heating tab starts with four tiles.
  - Consumption, kWh per degree day, degree days and heating days of the
    selected heating season, each compared with the previous season
  - The running season is compared with the same stretch of the previous one
  - The heating season selection is now at the top and applies to the whole page

### Fixed

- **Weather dashboard**: Winter months were missing from the monthly overview
  and from the notable days when the temperature stayed the same for a long
  time
  - The last value now counts for up to 12 hours; a longer outage stays a gap
  - Daily means become more accurate as a result
- The buttons at the bottom of dialogs in the weather and energy dashboards and
  in the migration had no gap between them

## 1.30.0 - 2026-11-05

### New

- **Weather dashboard**: More from the weather station's brightness sensor.
  - **Sunshine duration**: New role "Illuminance"; shown as a table row with
    hours of sunshine and the sunniest day. The threshold from which an hour
    counts as sunny is adjustable
  - **Daylight**: New role "Illuminance dusk/dawn". The Wind & sun tab shows
    the curve of the day with night, twilight and day, start and end of the
    day, day length and a comparison of the last days; the thresholds are
    adjustable
  - **Weather conditions**: A card on the overview summarises sky, wind
    (Beaufort), rain and temperature, with a symbol, a short sentence and
    details such as gusts, daily high and low and sun hours. For an earlier day
    it shows the summary of that day
  - **Day overview**: On the overview one card per day shows the last 14 days as
    sunny, changeable or overcast, with the hours of sunshine out of the bright
    hours. A click selects the day
  - **Hours of sunshine**: On the Wind & sun tab a curve shows the course of the
    south brightness over the day; the hours above the sun threshold are
    shaded, and the headline gives the hours of sunshine and the peak
  - **Sunshine tile**: The hours of sunshine today are also shown as a tile on
    the Wind & sun tab, with the previous day below
  - **Sun in the long-term view**: The monthly comparison also shows the hours
    of sunshine per month, the key days add sunny and overcast days per year,
    the year overview colours each day by its class, and the records name the
    sunniest day and month and the longest sunny and overcast streak
  - **Heating**: The degree-day table only shows months with values and the
    Δ column only once two completed heating periods exist; it uses the full
    width
  - The hint texts behind the (i) are collapsed by default on the whole page
  - **Switch cards on and off**: In the setup under "Display" the cards can be
    hidden individually; only cards with an assigned entity are offered. A tab
    without any card is dropped
  - **Additional sensors**: Humidity sensors (unit "%") can be added like
    temperature sensors; the Temperature & climate tab shows them in a
    separate "Humidity comparison" chart
  - The card selection is grouped by tab; the Heating tab and its settings are
    in a separate "Experimental" block
  - The weather series of the demo data have five years of history so the
    long-term cards have something to show; everything else keeps its period
  - The demo data contains both brightness sensors, wind direction and sun
    elevation with a history; the brightness now varies between overcast and
    sunny days
  - **Suggest** also recognises wind direction, sun elevation and PV power
  - **Experimental**: On the Heating tab the scatter plot points can be coloured
    by sunshine, and a table compares kWh per degree day on sunny, changeable and
    overcast days; a difference is only stated if it stands out from chance
  - **Experimental**: With the new role "PV power" the Wind & Sun tab shows the
    daily yield against the expectation from brightness, with marked deviations
    and the ratio by time of day
  - **Weather summary**: With the new role "Wind direction" it names where the
    wind comes from; a chip shows how long it was light yesterday. The limits of
    overcast, cloudy, fair and sunny can be set in the setup
  - **Daylight**: One line per year shows the day length over the year. With the
    new role "Sun elevation" the comparison of the last days also shows at which
    sun position the sensor reports day (a hint of shading)
  - **Weather summary**: The card comes in three layouts (Large, Compact,
    Strip), selectable under "Display"; the sentence only wraps between
    sentences
  - **Monthly overview**: The "Mean temperature per month and year" table can
    be switched to precipitation and hours of sunshine. The colour shows the
    deviation from the month's mean in percent, the last column is the total of
    complete years; the current month is shown without a comparison
  - **Daylight** (experimental): The table of the last days has fewer columns
    (twilight and sun elevation as pairs) and shows the change in day length
    from the previous day; today is shown as "ongoing". The day-length curve over
    the year gets smoother and, depending on location, starts above 0 h
  - **PV and brightness**: The expectation is a dashed frame behind the yield, a
    chip names the deviation. Clicking a day shows below how the system did
    against the expectation hour by hour on that day, with a band for "within
    range"
  - **Records**: The card now sits at the very top of the Long term tab and
    shows the records in three coloured groups (temperature, wind and rain,
    sun), with the key values large; each row leads to the day. A streak that is
    still running ends "today"
  - **Heating**: The degree-day table shows kWh per degree day large for each
    period, with degree days and kWh small below; the change sits next to it
    as a coloured pill. "Heating and sun" shows three tiles with a share bar
    and a result chip instead of a table
- **Overview**: At the top, tiles that lead to the dashboards and pages replace
  the statistics tiles, with colour and an info line
  - Which tiles (up to 10) and in which order can be set under Settings →
    Appearance
  - The statistics tiles are optional: "Only when something needs attention"
    shows a slim strip when something is open in Housekeeping, "Always" shows
    them as before. Existing installations keep them, new ones start with the
    strip; the figures are still under System → Statistics
- **Lovelace card, dashboard elements**: Besides charts and tables, the card
  shows ready-made parts of the dashboards (source "Dashboard element")
  - Tables: periods compared from Energy and Weather, and years × months from
    Energy, each with the figures of the dashboard
  - Charts: monthly comparison from Energy and Weather (years as lines with
    average and range), the weather year overview as a heatmap, as well as
    consumption or supply shares and the daily load profile from Energy
  - Key figures: tiles with value, change to the previous period and trend from
    Energy (generation, consumption, grid import, feed-in) and from Weather
    (value with high and low, or daily total)
  - The energy key figures can add self-sufficiency and self-consumption (change
    in percentage points) and, if a storage is set up, the storage with balance,
    state of charge, charge and discharge
  - A larger storage card shows the storage with a battery symbol (charge level),
    balance, change, charge and discharge; for a day with the curve of the state
    of charge
  - Energy long-term: records as tiles, key days per year (until today or whole
    years) and "What has changed?" with a choice of how many consumers
  - Shares, load profile and Energy key figures have a period (day, week, month,
    year) and an offset in periods back
  - Roles and formulas stay set up in the dashboard; elements that are not set
    up are missing from the choice
  - Administrators only; needs the current integration

### Changed

- **Charts**: Tiles and the chart page show the Y axes better.
  - **Tile**: The unit is shown at the top of each axis
  - **Chart page**: With two axes the right one is no longer cut off, and only
    the left one draws dashed grid lines
  - The "Save as image" icon at the top right of the chart has been removed

### Fixed

- **Import**: When an earlier month is imported afterwards, the first day of the
  following month now shows the correct increase for counters instead of 0.
- **Index repair**: After "Repair index" the card is no longer red, and the
  notification in the bell disappears.

## 1.20.0 - 2026-11-03

### New

- **Weather dashboard**: A new page for weather values, built like the energy
  dashboard.
  - Outdoor temperature, precipitation, humidity, pressure, wind, gust and
    radiation are each assigned to an entity
  - Tiles with the current value; table with Today, Yesterday, month and year
    against the previous year (reference date selectable via the dashboards bar)
  - Precipitation with a counter or with an on/off rain sensor
  - Monthly comparison of the years as lines
  - Precipitation tab (running total, rain days, dry spells) and Long-term tab
    (characteristic days, records, year matrix)
  - With assigned heating counters: Heating tab with degree days and kWh per
    degree day
  - Switch on via the dashboards overview; included in backups and the
    selective restore, selectable as start page, demo data included

- **Energy dashboard**
  - **Long-term tab**: A new tab compares periods across the
    years, built like the long-term part of the weather dashboard.
    - Comparison table for This month, Last month and This year against the
      previous year, with a reference date; plus self-consumption,
      self-sufficiency, battery and, with the system size (kWp per generator)
      entered, the yield per kWp and per irradiation
    - Monthly comparison of the years as lines with average and range, matrix
      **Years × months** with the deviation from the average
    - "What has changed?" shows the consumers with the biggest change
    - Records since the archive began
    - **Key days per year** in three pairs (almost self-sufficient and surplus
      days, sunny and dull days, battery heavily and barely used), for full years
      or only up to today; a click shows the days in the year calendar
    - The key day thresholds are in the setup under "Key days"
    - "Last 5", "Last 10" or "All" limits the years for the monthly comparison,
      matrix and key days
    - In the setup under "Long term" the table blocks and every card can be
      switched on and off
  - **Date picker**: Clicking the date in the energy flow opens the
    calendar. Depending on the period it jumps to a day, month or year.

- **Entities**
  - **Categories**: In addition to the stars, entities can be grouped
    by topic, for example heat pump or solar.
    - New **Categories** tab: create, rename, color, reorder by dragging (with
      suggestions on first open)
    - Entity list: **Category** column, sorting and filter; assign in selection
      mode or in an entity's configuration
    - The selection assistant suggests a fitting category
    - Filter also in "Selection & history" and in the export
    - The selective restore brings categories back
  - **Key facts in the header**: Above the tabs of every entity you now
    see the key facts from the entity list (type, unit, first and last value,
    records, size, depending on the type value range or counter reading). The
    tiles on the Configuration tab are gone.

- **CSV export, file format**: The wizard has a final step "Format and
  download" with a preview of the first rows. With several entities you can
  choose a **combined file**, **side by side** (one column per entity, one row
  per point in time, from the minute resolution, for at most 50 entities) or
  **one below the other** (one row per value with entity and unit). Date and
  time can be written in separate columns, and the Unix time can be left out.
  The number and file format comes as "Standard" (comma, point), "German /
  Excel" (semicolon, decimal comma, UTF-8 byte order mark for umlauts in
  Excel) or custom with separator and decimal separator. Without changes you
  get the same file as before.

- **Tips**: Seven new tips in the message center, including the weather dashboard,
  categories, the long-term tab, data quality and AI access. The backup and CSV export
  tips now mention the external backup target and the shared file.

### Changed

- **CSV**
  - **Import, preview**: If the file has no time of day, a note under "Timestamp read as"
    says that 00:00 is used.
  - **Export, wizard**: The wizard is clearer.
    - The step bar is the header of a card and shows the choices made so far
    - The bar with "Next" stays visible at the bottom
    - Selected rows are highlighted, the per-row download is now "↓ CSV"
    - The period shows a note on the available data; two clicks select a range
      of several months or years
    - The resolutions sit side by side with a bar for the file size

- **Energy dashboard**
  - **Anomalies**: The check can be adjusted more easily.
    - New thresholds 200 % and 300 %, a note in the settings says when "Off" makes sense
    - With "Off", the row, list and comparison note disappear from the status dialog
  - **Comparison with the previous period**: When paging back,
    the previous period is calculated with only the energy amounts the comparison
    needs. The page loads faster; the numbers stay the same.

- **Configure an entity, restructured**: The configuration page is clearer.
  - Sections General, Recording, Retention, Display and Checks with a jump line
  - Each field has a short line; the detailed text is behind the info button
  - The "customised" badge shows deviations from the default and resets after a
    confirmation
  - Locked fields show their fixed value

### Fixed

- **Index check reported false alarms**: For entities with a resolution set
  (e.g. 15 min), the check kept showing "deviations" although index and data
  had the same row count, and the "Index deviations fixed automatically"
  notice came back. The end of the time window now counts as matching.

- **Interface**
  - **Entity list remembers the page**: Opening an entity from page 2 and going
    back used to land on page 1 again. The list now opens on the page last shown.
  - **Errors and waiting**: When a request fails, the restore wizard,
    entity and chart pages, export, statistics and entity migration now show a
    message (chart pages with "Try again") instead of blank areas or a false
    "Done". Saving in the editors and compacting can no longer be triggered
    twice; compacting shows "Wird verdichtet …". Slow answers no longer overwrite
    newer displays, which applies to the energy dashboard and tiles. Progress
    polling during ZIP import survives short hiccups.
  - **Keyboard use**: Dropzones in the import open with Enter or Space, headers
    of sortable tables are focusable and announce their sort order, dashboard
    sections announce their state, and hints also appear on focus.
  - **Texts**: Missing translations in the report, the Home Assistant import, the
    dashboard tiles and the settings were added; the warning colour in the energy
    dashboard follows the theme.

- **Ingestion and archive**
  - **Monthly files crash-safe**: A power failure or crash in the middle of a
    write could damage the current month's file or an archived month, and a
    damaged archive then stopped ingestion for all entities. Files are now
    replaced atomically, and a previous month that cannot be archived no longer
    holds ingestion up.
  - **Export, import list and statistics held up ingestion**: After a CSV
    export, later backups and maintenance runs could hang, and the import page or
    the statistics page stopped ingestion from Home Assistant during a long
    import. Deleting reports and backups answers "busy" after a few seconds while
    an import runs instead of waiting.
  - **Set resolution, long months**: For measurements with a set resolution
    (e.g. 30 s), ingestion read the whole month file for every value; at the end
    of the month that took a good 50 ms per value and slowed ingestion down. Now
    only the end of the file is read (under 1 ms). A value inserted afterwards or
    arriving late no longer shifts the mean of the running time window but gets
    its own window.
  - **Type change of an entity**: When Home Assistant changes the kind of an
    entity (for example measurement to counter), rebuilding the analyses held up
    ingestion of all other entities. It now locks only this entity.
  - **Pages stay usable**: Saving settings, muting notices, swapping tiles, the
    backup schedule and the backup import no longer compute in the event loop.
    With many archive files the whole server stood still there.

- **Security and access**
  - **Chart tooltips**: Entity names and units are shown as text in the
    tooltips of the energy dashboard, the chart editor and the entity page,
    never as markup.
  - **Invalid API token**: A token with special characters caused error 500
    instead of 401. When writing, the token is now checked before the content.
  - **Uploads and credentials**: Files that are too large are refused right away
    instead of filling the disk first. The VictoriaMetrics import no longer
    follows redirects and sends the credentials only to the address entered.
    "Delete all backups" leaves backups that have not yet been transferred to the
    external target.
  - **AI access, sharing and accounts**: The sharing of entities now applies in
    every tool. The energy overview shows consumers and generators that are not
    shared only as "Other (not shared)" (the totals stay complete), and with a
    restricted selection, notices that name entities are left out. User accounts
    without administrator rights can no longer query individual entities through
    the AI, as with the card; the energy overview and the notices stay available
    to them. Settings → AI access counts rejected calls.

- **Energy dashboard**
  - **Costs when a price changes within the period**: If a
    price (for example a tariff helper) changed in the middle of a month or year,
    all hours before the first change counted as "no price". Costs, average
    price, avoided costs and the costs per consumer came out too low. The price
    that already applied at the start of the period is now used. Past periods are
    therefore shown differently than before.
  - **Long-term**: Consumers appear with their Home Assistant
    name instead of the entity ID.

- **Weather dashboard, key days and rain days in other units**: Frost, ice,
  summer and hot days are now counted correctly for a sensor in °F or K
  (before, almost every day was a summer day), and rain days for a rain counter
  in inches.

- **Analyses**
  - **Minimum and maximum values in the analyses**: For measurements with a set
    resolution, the true minimum and maximum were lost because the month closing
    calculated only from the means of the time windows (a short frost was no
    longer visible in the hourly mean). Months that are closed or recalculated
    from now on keep the true extremes; existing analyses stay unchanged.
  - **Previous month of rarely sending entities**: Entities that had sent nothing
    since the month changed were missing from all analyses for the previous
    month. The previous month is now archived hourly.
  - **Time zone and number format**: Year, month and day boundaries in state
    charts, weather and long-term pages, the chart editor and tiles follow the
    server's time zone instead of the browser's (with a different zone a year
    began an hour early). Tables sort numbers in the selected number format,
    including English.
  - **Numbers at time boundaries**: A value exactly on the boundary between two
    periods now counts only for the later one. Daily values of the Home Assistant
    statistics land on the right day on days with a clock change.
  - **Min/max in the running month**: Lowest and highest values of the running
    month (for example for frost days) now come from the real extremes instead of
    the window means, as is already the case for completed months.
    - Applies to new data; existing rollups are not rebuilt

- **CSV**
  - **Import, files from Excel**: A CSV in "ANSI" format (Windows-1252, ä/ö/ü
    as single characters) or as "Unicode text" (UTF-16) made the upload fail with
    error 500, and afterwards the whole import page was unreachable. Such files
    are now converted on upload; a file that cannot be read at all is removed
    with a notice.
  - **Import, clock change and duplicates**: The repeated hour when the clock
    goes back to standard time now gets its own timestamps (before, it fell on
    the same time), and overlapping files no longer write the same points twice
    into the archive (they count as duplicates). Uploading and deleting the file
    are refused while an import or a preview is running.
  - **Export, formulas**: Texts from Home Assistant that start with =, +, -
    or @ get an apostrophe in front so that Excel does not execute them as a
    formula.

- **Selective restore, abort**: If restoring entities fails after the database
  was written, files and database stay in the old state. The Symcon names are
  replaced as the very last step.

- **Import reports of VictoriaMetrics imports** show up in the list and can be
  opened and deleted (before, they were invisible).

- **Comparison tables**: When saving, the same limits apply as for the query
  (at most 25 different entities, 100 columns); before, a table could be saved
  that could no longer be loaded afterwards. The card answers with a reason
  instead of error 500 for tables that are too large.

- **More robust input and shutdown**: An invalid entity ID or a broken number in
  the table view now gives an error message instead of a server error. On
  shutdown the storage only counts as cleanly closed if all background tasks
  have really stopped; otherwise it is checked on the next start.

- **Container**: The base image is pinned to a fixed version.

## 1.13.0 - 2026-11-01

### New

- **Energy dashboard, chart popup**: Clicking any of the five tiles now opens a
  chart for the selected period instead of a selection list or a direct jump to
  the entity. Generation and consumption show stacked bars per source
  (consumption including the base load), switchable to lines and percentage
  shares; storage shows charge and discharge side by side, plus the state of
  charge as a line for hour and day if a state-of-charge sensor is set; grid
  import and feed-in show bars (or a line) with the previous period as a dashed
  line. The rows below and "Open entity" lead to the respective entity as
  before.

### Fixed

- **Late values no longer overwrite an archive**: If values for the previous
  month arrived after the month change (for example from the integration's queue
  after an outage), the next rotation replaced the already archived month with
  those few values and the rest was lost. The archive is now extended,
  duplicates are counted once, and the month's daily and monthly figures are
  recomputed.
- **Energy dashboard, storage check for the month**: The message "Discharge
  exceeds charge — charge/discharge swapped?" wrongly appeared for the current
  month when the storage was full at the start of the month and the month had
  little PV yield. Without a state-of-charge sensor the storage capacity is
  now the allowance; if no capacity is set either, only a full year is
  checked.
- **Cleanup, deleting many rows**: With a page size of 1000, "Delete" did
  nothing when all rows were selected. The same limit affected bulk actions with
  very many entities (deleting entities, the usage check and the data-quality
  bulk actions). Both work again.
- **Cleanup, page size**: "Delete", "Remove duplicates automatically",
  "Condense repetitions", "Mark missing-value drops" and "Undo last deletion"
  reset the chosen page size to 20. It is now kept.
- **Charts, Wh and kWh**: Series in Wh and kWh in the same chart ended up on two
  separate axes with their own scale and could not be compared. They now share
  one axis in the largest unit present; pure Wh charts stay in Wh.

## 1.12.0 - 2026-10-31

### New

- **Lovelace card (app side)**: The app now provides the data for the new
  Zeitarchiv card of the integration: saved charts and tables, entities
  directly and sparklines. The card needs the matching version of the
  integration; with an older app it reports “App too old”.

- **Chart editor, series colors**: Under **Selected**, clicking a series' color
  dot opens the color picker: “Auto” as before, one of the eight palette
  colors, or a custom color. The choice applies in the chart and in all
  dashboard tiles of that chart. Palette colors follow the theme, custom colors
  stay the same everywhere.

- **Tables, sum column**: An extra column shows the sum over the periods for
  each row, on the left or right, with a label of your choice. Each column can
  be set to count or not, for example 7 of 8 days for a weekly sum. Counters
  and switches are summed; average, minimum, maximum and states show “–”.
  Formula rows continue to calculate on the sums, and the sum row adds up the
  sums of its rows. The column is also available in dashboard tiles and the
  CSV export.

- **Entity list, “Counter decreases” column**: An optional column shows for each
  counter whether decreases are reported (“Report”) or allowed (“Allowed”). It
  is hidden by default and can be switched on under “Columns”.

- **Fourth decimal place**: “4” can now be chosen for decimal places, in the
  entity configuration, the options menu, the chart editor, dashboard tiles and
  the table editor. A price such as 0.0796 €/kWh stays visible instead of
  appearing as 0.08.

### Changed

- **Chart tooltip**: When you hover a chart with several lines or bars, the
  tooltip now always shows all visible entities, each with its last known value
  at that point in time. Before, entities without a reading of their own there
  were missing, for example hourly bars next to a finer line. If a line's value
  is noticeably older than the others, its time and age appear below it, for
  example “10:30 · 5m ago”. Applies in the chart editor and in dashboard tiles.

- **Energy dashboard, anomalies in the month view**: The month is now compared
  with the same month in previous years instead of the previous months. A
  consumer with seasons, such as a heat pump, no longer shows up as “+1000 %” in
  winter. If there is no earlier year yet, the average of the last three months
  applies as before; the note in the dialog names the comparison.

- **Decimal places “Auto”**: Values below 0.1 now appear with four significant
  digits, for example 0.0796 instead of 0.08.

### Fixed

- **Chart tooltips**: Entity names and units are now escaped in the tooltip.
  Before, a display name containing HTML code could have been executed when
  hovering.

- **Cleanup, “Correct” tab**: Saving one row discarded all other unsaved entries.
  Now only the affected cell is updated. A new bar with “Save all (n)” and
  “Discard all” handles several open rows at once; when a row is opened, its
  current value is selected and can be overwritten directly.

- **Symcon import, ZIP upload**: If the upload fails without a message from the
  app, for example with 413 or 502 from a proxy, the error message now includes
  the HTTP status. Before, it only said “The ZIP could not be processed.”

- **Entity view and chart editor**: In the chart, long y-axis labels lost a digit
  on the left (“4,782 ct/kWh” instead of “24,782 ct/kWh”). The margin now follows
  the actual width of the labels and grows with them.
- **Energy dashboard, counter reset**: Counters for which decreases are allowed
  are no longer reported as “Counter reset detected”.
- **Energy dashboard, storage notice**: “Discharge exceeds charge” now appears in
  the month and year view only if at least 90 % of the period is archived, also
  with an SOC sensor. If the archive starts mid-month, the charge level at the
  start was missing and the notice was a false alarm.
- **Entity list**: Showing or hiding columns no longer jumps the list back to
  page 1.
- **Energy dashboard**: In the English interface the Sankey chart and the load
  profile heatmap still showed German labels (“Grundlast”, “(Ladung)”,
  “(Entladung)”, weekdays “Mo”/“Di”/…). They are now translated.
- **Rolling periods**: the first, partial daily bar stuck out to the left over
  the y-axis and covered its labels. The axis now starts slightly earlier so the
  bar sits fully inside the chart (chart editor and tiles).
- **Entity configuration**: The dropdowns (Resolution, Compaction target,
  Retention) now open directly below their button instead of beside the card.
- **Live resolution**: Entities with a resolution set (e.g. 15 min) regularly
  showed the notice “Index discrepancies fixed automatically”. The index now
  counts the merged values correctly from the start. The measurements were
  never affected.

## 1.11.2 - 2026-10-26

### New

- **Chart editor**: The search under “Add entity” splits search terms at spaces;
  every term must appear in the name or the entity ID, as in Home Assistant.

### Changed

- **Energy dashboard, average price**: The tile shows the price in the subunit,
  for example “28.8 ct” per kWh, the way you enter the fixed price.

### Fixed

- **Chart editor**: After adding an entity the search text stays, so several
  results of one search can be picked one after another.
- **Energy dashboard, price entities**: If a price entity delivers cents per kWh
  (unit “ct/kWh”), the app now recognises that from the unit and converts it
  correctly. Before, the costs were shown a hundred times too high. The setup hint
  now says an entity may deliver euros or cents per kWh.
- **Energy dashboard, missing price history**: If the price entity has no value at
  all in a period (for example in months before it was set up), the cost analysis
  now shows “Price missing for 100 %”. Before, the grid import costs were silently
  missing and only the base price showed up as costs.

## 1.11.1 - 2026-10-25

### Fixed

- **Tables**: A sum row placed directly below a separator stayed empty. In this
  case it now sums the section above.

## 1.11.0 - 2026-10-25

### New

- **Selection assistant**: Under Entities → “Selection & history” you tell it
  what you want to archive, for example energy or weather, and Zeitarchiv
  suggests matching entities from your Home Assistant, each with a reason and a
  confirmation before any change.
  - **Topics and search**: Energy, Weather, Temperature & climate, Air quality,
    Water & gas, Network, Batteries, System & server, plus the topics Doors &
    windows and Presence, which are marked as sensitive. Alternatively, a search
    finds matching entities, for example for “power” or a device name.
  - **Patterns**: For a topic the assistant suggests patterns such as
    `sensor.shelly_plug_*` and shows beforehand how many entities they cover.
  - **Applying**: The default is the label “Zeitarchiv” in Home Assistant,
    alternatively individual IDs. The integration applies the change within about
    a minute; data that is already archived stays unchanged.
- **Selection & history**: A new tab on the Entities page shows how each entity
  got into the selection (assistant, by hand in Home Assistant, pattern, area,
  device or label), with filter and search, plus the history of all changes.
  Entries can be removed, excluded and included again, also several at once;
  actions of the assistant can be undone.
- The assistant needs the Zeitarchiv integration 1.2.0 or later. With an older
  one the app only shows the suggestions.
- **Energy dashboard, cost analysis**: When the grid price comes from an entity
  (for example a dynamic tariff), a new tile shows the average electricity price
  per kWh. Energy without a price is not counted. If the price is only applied per
  day, month or year, a note says the costs can be inaccurate with a changing
  tariff.
- **Energy dashboard, costs with a dynamic tariff**: Grid import, feed-in
  revenue, self-consumption, costs per consumer and CO₂ are now calculated hour
  by hour for week, month and year when the price is an entity and the meters
  have hourly values. Before, the price from the start
  of the day or month applied to everything drawn in it, which could show the
  costs far too low or too high with a day-time or hourly tariff. While the hourly
  values are still missing (right after setup), the previous price per day
  applies, with a note.
- **Demo data**: New entity `sensor.demo_strompreis_dynamisch` with a dynamic
  electricity price (a new price every 15 minutes), for trying out the cost
  calculation in the energy dashboard. An existing demo gets the history
  retroactively with “Supplement now”.

### Fixed

- **Energy dashboard**: entities marked as “historic” no longer trigger a
  “Stale” message in the “Sensor values up to date” status, since they
  intentionally no longer receive new values.

## 1.10.0 - 2026-10-24

### New

- **Energy dashboard**: in the “Balance, data quality & anomalies” dialog the
  affected entities are now clickable: for detected counter resets and for
  unusually high consumption the name leads to the entity view for the selected
  period.
- **CSV import**: the import reads more files without manual work.
  - **Preview “read as”**: a second table shows how the timestamp and value of each row are read. Unreadable cells are red, ambiguous numbers such as “1.572” yellow.
  - **Time format**: detected from the first rows when uploading (Unix, ISO 8601, DD.MM.YYYY, also with time) and available as a choice. A custom pattern is no longer needed for dates such as 15.01.2026.
  - **Totals per period**: the option “Values are totals per period” builds the running sum of monthly or daily values and stores it as a meter reading, without recalculating the file first.
  - **Several columns**: a file with several value columns (e.g. PV generation, grid import, feed-in) can be imported in one go. With “+ Import another column …” you assign each column its own entity.

### Changed

- **Data quality**: when something is found, the row now reads “Counter reset
  detected” instead of “No counter resets”, and the heading is “Data quality:
  notices” instead of “check failed”.
- **Anomalies, comparison**: for “Day” the comparison uses the same weekday of
  the last three weeks, for “Hour” the same hour of the last three days
  (previously the three periods directly before). The dialog states the basis
  and notes that the current period is not complete yet.
- **Anomalies**: consumption only counts as unusual if, besides exceeding the
  percentage threshold, it is also at least 0.1 kWh above the comparison value.
  If a sub-meter rises because of an unusual device behind it, only the device
  is reported instead of both.
- **Energy flow colors**: the Sankey, its legend, the key figures, the energy
  report and the sections of the setup now use the colors from Home Assistant:
  solar orange, grid import blue, export purple, storage pink (charging) and
  teal (discharging). They are the same in every color scheme; in dark mode
  only the export color is slightly lighter.
- **CSV import, preview**: the dry run is more informative.
  - Skipped rows are listed with line number and reason (timestamp does not match the format, value is not a number, too few columns).
  - If values of a counter are below their predecessor, the preview warns that the history shows 0 there. The same applies to the first value, which has no predecessor; it names the way out (a row with the start value before it).
  - The help text explains that counters expect running meter readings.

### Fixed

- **Counter decreases**: “Mark bad-value decreases” in an entity’s cleanup now
  marks all returning decreases in the selected period (e.g. “All”), no longer
  only those of the last 30 days.

- **Rolling periods**: with a resolution of one hour or more (e.g. “1 day” or
  “6 hours” in the “Week” period) the bars sat next to the axis labels because
  they were aligned to the start of the window (e.g. 2:39 pm) instead of
  midnight. Charts and tiles now align them to the day boundary.
- **Tables**: in the column editor the placeholder of the “Group label” field is
  no longer cut off; the note that the group spans several columns is now in
  the tooltip of the field label.
- **CSV import**: numbers with a thousands separator (“1.572,4” as exported by Excel, or “1,572.4”) are now read. Previously such rows were skipped without notice.

## 1.9.0 - 2026-10-22

### New

- **States**: entities that report text instead of a number (e.g. a
  thermostat with "Heating"/"Idle" or a robot vacuum) are now archived when
  they are selected individually in the integration. The history view shows
  them as a coloured timeline with duration and share per state and a change
  log; outages appear as "unavailable". The import from the Home Assistant
  history takes them over as well. The demo data contains three examples
  (robot vacuum, washing programme, heating operation). From the "Month"
  period on, the history view shows the shares per day, month or year as
  bars; you can switch to timeline, changes, donut or a heatmap (weekday ×
  hour). The timeline stays readable over long periods too: where there are more
  periods than the chart is pixels wide, it draws a column per pixel with the
  shares of the states. In the chart editor, several states appear as rows of a
  shared timeline, as donuts or as heatmaps.
  Dashboard tiles show the current state with "for" and the last 24 hours as
  a strip, wide tiles the shares of the last 7 days, optionally as a donut.
  When values are discarded because the type does not match or an entity
  reports more than 50 states, a notice appears, with the matching
  integration also as a repair in Home Assistant. Comparison tables do not
  CSV import and export carry the text of states; the Symcon import can
  turn variables with a profile into states.
  support states yet.
- Lights, fans, automations, scripts, remotes, sirens, humidifiers, updates
  and calendars are now archived like switches (on/off) – with timeline and
  on-time. Previously they never reached the app. This also applies to the
  import from the Home Assistant history; live transfer additionally needs the
  matching integration.
- **AI access**: Claude, ChatGPT or Home Assistant's conversation agent can
  answer questions about the archive, such as "How did electricity use in July
  compare to last year?". Access goes through Home Assistant's MCP server, is
  read-only and off by default. Under Settings → AI access you choose whether all
  entities or only a selection are visible, whether presence is excluded and
  which tools the AI may use; you can also add your own notes for the model.
  The page shows usage per day, rejected calls and a history of changes. This
  needs the matching version of the integration. The log can be filtered by area.
- **Dashboards**: the new "Period navigation" switch in the dashboard editor
  adds a date bar with calendar and step size (day, week, month, year) to the
  title row. All tiles then show the period containing the chosen day; a notice
  reminds you that you are looking back.

### Changed

- **Chart editor**: entities are now added through a search field instead of
  a long list of checkboxes. Matches can be filtered by type and unit,
  favorites come first, and entities with a fitting unit are listed before
  those that would need another Y axis. "Selected" lists the chosen entities
  with their unit, and they can be removed there as well.
- **Consumer shares**: groups and sub-meters are collapsed by default so the
  top level stays in view.
- The "Import incomplete" notice now says what is missing (errors and rows that
  could not be imported) and points to the reports.

### Fixed

- Chart tiles stayed empty after a date change or the automatic refresh,
  although the legend showed values.
- The number above the tallest bar was cut off on tiles.
- Importing from the Home Assistant history could show doubled daily values for
  counters. Data that was already imported wrongly stays as it is: clear the
  entity and import again.
- The "Import incomplete" warning stayed forever because unavailable states were
  counted as errors.
- Dropdown menus whose button sits far to the left (e.g. "Gap detection" in an
  entity's configuration) no longer extend past the left edge of the window and
  are fully readable again.

## 1.8.0 - 2026-10-19

### New

- **Long-term comparison**: In the chart editor, “Compare” now offers
  “Long-term” for the period “Year” with one entity. Every year is drawn as its
  own line from January to December, with the average of the previous years as
  a dashed line; the “Years” field sets how many previous years (2, 5, 10 or
  All). Below the chart, a year × month table shows the values and the deviation
  from the average; the current year is compared with the average up to the
  same day.
- **Comparison in dashboard tiles**: A chart's tile now shows the comparison
  saved with it, like the chart page – previous year, running total with target
  and long-term. For long-term, the legend names the current year and the
  average; a 1×1 tile draws only these two.
- **Charts with a fixed period**: When customizing a chart, you can page back
  to past periods with ‹ and › or a calendar, like on the page of a single
  entity. A chart saved at 2025 still shows 2025 later, when viewing and on the
  dashboard; saved in the current period, it always stays current. “Save as
  chart” on an entity's page takes over the period shown there.
- **Back to now**: Like the chart editor, an entity's page now has a “Back to
  now” button whenever a past period is shown. In the chart editor, the
  navigation now sits next to the period selection, as it does there.

### Changed

- **Selection lists by keyboard**: The selection lists in filters, settings
  and housekeeping can now be used without a mouse: Enter, Space or Arrow Down
  opens the list, the arrow keys move through the entries, Enter picks one,
  Escape closes it.
- **Error messages for the WebDAV target**: If a transfer fails, the message
  now names the step that failed (such as “Upload”) and, with Nextcloud, the
  server's reason.
- **Import data not in the Home Assistant backup**: Uploaded Symcon and CSV
  import data no longer ends up in Home Assistant's backup – as was already
  the case for Zeitarchiv's own backup. It can be uploaded again at any time.
- **New logo**: Zeitarchiv has a new logo; the header shows its wordmark,
  matching the light and dark color scheme.

### Fixed

- **Chart comparison was not saved**: “Previous year”, “Long-term” and the other
  comparisons, including running total, target entity, years and Ø line, were
  back to “Off” the next time a chart was opened. They now stay with the chart.
- **Table tiles with quotation marks in column names**: If a column label
  contained a quotation mark, the deviation hint in the dashboard tile was cut
  off or missing. It is now shown in full.
- **Selection lists with apostrophes**: If a unit, a column header in the CSV
  import or an entity name in the activity filter contained an apostrophe, the
  entry could not be selected. This now works.
- **External backup after a lost connection**: If a stalled network storage
  came back while a new attempt was already running, the two transfers could
  get in each other's way and damage the copy at the target. Each attempt now
  writes to its own temporary file.
- **Typos in table formulas**: A number such as “1,2,3” or “2..5” in a
  calculated row used to give a wrong value without notice. The cell now shows
  a formula error.
- **Migration wizard on errors**: If the preview could not be calculated
  (for example for an unknown target entity), the wizard still moved to step 2
  and showed the error response there. It now stays in step 1 and names the
  reason.
- **Deleting in the chart and table editor**: If deleting failed, the editor
  still went back to the list, where the chart or table was still there. The
  editor now stays open and reports “Deleting failed.”
- **Chart options of an entity**: If a change in the options menu could not
  be saved, it still looked saved and was gone on the next visit. A message now
  appears; the same applies to “Reset to default”.
- **English interface**: The “Header row” selection in the CSV import showed
  “Ja”/“Nein” and the mark filter showed “Markierung:” in German. Both are now
  translated.
- **WebDAV backup to Nextcloud**: From Nextcloud 31 on, the transfer failed
  with “status 400” even though the connection test succeeded. Backups reach
  the target again.

## 1.7.0 - 2026-10-19

### New

- **Historic**: Entities that deliberately stop receiving values (for example a
  decommissioned meter) can be marked as “Historic”. They then no longer count
  as inactive, neither in the housekeeping list nor in the status tile or the
  notice. Data and charts stay unchanged. You set it on the entity's
  “Configuration” tab or directly in the list of inactive entities; a badge on
  the entity shows it, and a separate housekeeping section lists all marked
  entities. If values arrive after the mark, the mark stays, but the section
  warns (even when collapsed) and a notice points it out. The entities overview
  gets an optional, sortable column “Historic” (hidden by default).
- **Counter decreases**: A bulk button deletes all decreases rated “Probably a
  bad value”. Genuine resets and meter replacements are left untouched.

### Changed

- Notices stay up to date: for example “Import source data still present”
  disappears right after deleting it, and duplicates and outliers are
  re-evaluated after marking or restoring.
- The links for duplicates, outliers and counter decreases in housekeeping open
  the cleanup with the matching filter.
- The back link from data quality is kept after cleaning up and correcting.

### Fixed

- The chart option “Points” is saved and also applies to the tiles on the
  overview.
- The note “You can leave the page …” in the progress of an entity cleanup no
  longer appears several times in a row.

## 1.6.0 - 2026-10-16

### New

- **Data quality**: A new page shows outliers, gaps, duplicates and counter
  decreases per entity, with search, finding filter, sorting and period (7 days
  to all time). A status line and the “As of” column tell you how current the
  numbers are, and a notice points to new findings. The colors follow the
  question “Can I do something?”: yellow means you can clean up, grey is
  information only.
- **Remove duplicates** as a bulk action across several entities, with preview
  and undo.
- **Restore with selection**: A wizard guides you through the restore in four
  steps. You can restore the whole backup, single entities or just the
  settings. The last two run without a restart, add charts and tables as a copy
  by default and can be undone.
- **Delete several entities**: With “Select” in the entities overview you mark
  several entities. The dialog first tells you where they are still used
  (charts, tables, tiles, energy dashboard) and offers a backup. Charts and
  tables that mention a deleted entity show “Entity missing”.
- **Ignore values** per entity: Sensors that briefly report 0 at startup can be
  filtered this way. You name the values that should not be stored.
- **Edit values**: Counter decreases carry an assessment (“Probably a bad
  value”, “Probably a reset”, “Still open”) with a reason. The markings are
  colored by meaning instead of everything in red.
- **Settings → Reset**: You can delete just the measured values or the entire
  data set. Deleting requires typing a confirmation and offers a backup first.
  The API token, backup settings and the backup files are kept.
- Table column labels know the placeholders `{weekday}` and `{weekday_short}`.
- The type display names the entity’s `state_class`. For the class `total`,
  data quality shows “n/a” because it is not checked for decreases.
- Energy dashboard: “Show by group” in the consumer shares shows each group as a
  total row with its devices, collapsible.

### Changed

- Data quality is measured in the background within a time budget and only for
  entities with new values. Silent entities no longer count as outdated.
- The assessment of counter decreases is worded more clearly. The action is now
  called “Allow decreases”.

### Fixed

- Charts that mention a deleted entity load again instead of staying on
  “Loading …”.
- Daily counters (“… today”) no longer show the scheduled reset at midnight as a
  counter decrease, even for large entities over the period All time.

## 1.5.0 - 2026-10-11

### New

- **Energy dashboard: devices behind a sub-meter**. A consumer can now be
  attached behind another consumer (“Behind sub-meter” in the dialog,
  one level). Nothing is counted twice: consumption, base load and
  self-sufficiency use the sub-meter, and the devices break it down. Whatever
  the meter measures beyond its devices appears as “… other consumers”. Sankey,
  consumer shares, chart and energy report show the devices under their
  meter; if the devices count more than the meter, the status says so. If you
  remove a sub-meter, its devices are kept as separate consumers.
- **Energy dashboard: standing charge** in euros per month, as an entity or a
  fixed value. It is calculated pro rata per day, is part of the balance and has
  its own tile in the cost analysis and in the report.
- **Missing prices are visible**: If a price (energy price, feed-in tariff,
  standing charge) is missing for part of the period, the cost tile shows a
  warning symbol, the affected tiles are outlined in yellow, and a note gives
  the share. The report has a matching line.
- When setting up prices, a note recommends an entity or helper: earlier
  months stay correct after a tariff change, while a fixed value applies
  retroactively.

### Changed

- The list of network shares for the external backup target can be collapsed;
  shares that are not mounted are shown separately below.
- Price entities whose gap detection is set to “Off” are no longer considered
  stale, since they only report changes.

### Fixed

- A rarely changing price helper (e.g. “Number”) only applied from today in the
  current month; the rest of the month had no price. The last set price now
  also applies to the start of the month.

---

## 1.4.0 - 2026-10-11

### New

- **External backup target**: Every successful backup can additionally be
  copied to network storage (NFS or SMB/CIFS, mounted in Home Assistant under
  Settings → System → Storage) or a WebDAV server (e.g. Nextcloud). Set it up
  on the Backup page in the “External target” card, with “Test connection”.
  The local backup is kept. Retention at the target is “same as local” or
  “Never delete”; only Zeitarchiv backups are ever deleted. Backups that have
  not been transferred yet are not removed by local retention. The WebDAV
  password is only set in the add-on options so that it does not end up in
  the backups. “Number to keep” has a new step of 1.
- The backup table has an “External” column showing the transfer status. If a
  transfer fails, a notice appears; the local backup stays valid and the
  transfer is retried with the next backup.
- **CSV export as a wizard**: choose the time range and resolution (raw,
  minute, hour, day …), and export several entities at once. With several
  entities a ZIP with one CSV per entity is created automatically. The columns
  stay `timestamp`, `unix_ts` and `value`. The wizard estimates the row count
  beforehand and warns if a file would be too large.
- **Counter decreases**: The table is clearer. “Mark bad values” shows
  which values are affected and what will happen before you confirm; afterwards
  a message lists the marked values with links to the “Marked” tab and can be
  dismissed with “OK”. Below the table you see how many values are marked in
  total.

### Changed

- The “Counters with allowed decreases” list is styled like the table.
- Docs: operations guide for the external backup target, a note about using
  host `127.0.0.1` if `localhost` is not reachable.

## 1.3.0 - 2026-10-07

### New

- Number and date format as a separate setting (Appearance): German,
  English UK or English US, default “Automatic” following the browser.
  The format is independent of the language and applies to numbers, dates
  and times (en-US with a 12-hour clock) in the interface, charts and tables.
- Currency as a separate setting (Appearance): automatic from Home
  Assistant, euro, pound, US dollar or Swiss franc. It determines symbol,
  position and subunit (cent, pence, rappen) in the energy dashboard. Nothing
  is converted — when you switch, you enter your prices again.
- Stored notices (e.g. hints, cleanup and backup texts) appear in the current
  display language. Background tasks use the configured or last seen language.
- Demo data in English: the script (`--language en`) and demo mode generate
  English names and entity IDs. The default dashboard “Übersicht” shows as
  “Overview” in the English interface.
- Add-on configuration and port descriptions are translated into German and
  English. The Home Assistant integration passes the HA language to the API,
  so error messages come in that language.
- Uptime and age values (“3 days, 4 h”) are translated.

### Changed

- The language now defaults to “Automatic (browser)”, falling back to German.
  Existing installations without a stored choice follow the browser as well.
- Error texts of the settings routes are consistent and follow the language.
- Charts consisting only of switches open as a timeline by default; raw values
  are kept when changing the time range.

### Fixed

- Time zone: “Add value” and “Compact” calculate in the server’s time zone.
- The Sankey legend crashed on entries with identical names.

## 1.2.0 - 2026-10-06

### New

- English interface: The whole app is now also available in English. Choose
  the language in the settings (“Automatic”, German or English); the default
  stays German. The documentation is available in English as well.
- Counter decreases: When a counter reading drops, the app reports it and
  classifies it (“returns” = probably a bad value, “stays low” = probably a
  meter replacement). Housekeeping has its own section for it, and the
  cleanup page shows the classification on the value. Bad values can be
  marked for deletion in bulk in Housekeeping and undone again.
- Counters that reset on a schedule (e.g. daily) can be set to “Allow” in the
  configuration, so their decreases are no longer flagged. With “Report”
  there is also the rule “Mark bad values automatically” (off by default),
  which marks returning decreases itself every hour. If bad values repeat
  over several days, the notification bell points to a problem at the
  source.
- “Marked” tab: All values marked for deletion for an entity can be viewed,
  undone individually or as a batch, and removed permanently. Each batch
  shows its origin (manual, duplicates, repetitions, bulk action, rule) and,
  if automatic cleanup is on, when it will be removed. Pending cleanups also
  appear in the bell, the banner and the entity list.
- “History” tab: Shows per entity what was changed (marked, undone,
  corrected with old and new value, added, removed, compacted), grouped by
  day and filterable.
- Charts: When comparing with the previous year or period, the legend now
  also shows the total “up to the same day”. The legend style “Table” now
  also works for the year comparison with running total, and the table no
  longer breaks between number and unit on a smartphone.
- “Status” tile: A new tile in the overview’s row of figures and as a
  dashboard tile (size 2×2 or 1×1). It shows the inactive entities as well
  as open counter decreases, duplicate timestamps and marked values, linking
  to Housekeeping. The “+” popup for adding tiles is wider, has a fixed
  height and a “More” tab.
- Import: VictoriaMetrics as a fourth import tab for a one-off backfill.
- Tips: New tips on history, automatic cleanup and the legend table; the tip
  on counter decreases now describes the notification.

### Fixed

- The previous-year comparison and “Same point in time” counted partial
  periods in full (e.g. “2025 up to 06.10.” included the whole of October).
  The values now end on the chosen day.

Versions before the first bilingual one are in German only: see [CHANGELOG.de.md](https://github.com/bertel2020/HA-Apps/blob/main/zeitarchiv/CHANGELOG.de.md).
