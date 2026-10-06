# Zeitarchiv

*[Deutsche Version](DOCS.md)*

Zeitarchiv archives selected Home Assistant states for the long term, in a
space-saving way, as Parquet files. The separately installed Zeitarchiv
integration sends the state changes to the app; Ingress provides archive,
charts, tables, import/export, cleanup, retention and backup / restore in the
Home Assistant interface.

**[User guide](docs/en/user-guide.md)** — setup, every page in detail,
settings reference, typical tasks.

<p align="center">
  <a href="https://buymeacoffee.com/bertel2020"><img src="https://img.shields.io/badge/Buy%20Me%20a%20Coffee-support-FFDD00?logo=buy-me-a-coffee&logoColor=black" alt="Buy Me a Coffee"></a>
  <a href="https://ko-fi.com/bertel2020"><img src="https://img.shields.io/badge/Ko--fi-support-FF5E5B?logo=ko-fi&logoColor=white" alt="Ko-fi"></a>
  <a href="https://paypal.me/RobertoMartins"><img src="https://img.shields.io/badge/PayPal-donate-00457C?logo=paypal&logoColor=white" alt="PayPal"></a>
</p>

## Setup

1. Install and start the app.
2. Open Zeitarchiv from the Home Assistant sidebar.
3. Under **Settings → Connection**, copy the API token that was generated
   automatically on first start.
4. Install the Zeitarchiv integration (via HACS or manually from
   [github.com/bertel2020/HA-Zeitarchiv](https://github.com/bertel2020/HA-Zeitarchiv))
   and set it up with host `localhost`, port `8127` and this token.
5. On the integration tile, under **Configure → Edit archive filters**, select
   the domains, entities, areas or devices to archive. Without filters no data
   arrives — and no error message is shown.

How can I tell that data is arriving? Under **Settings → Connection**,
"Last received value" shows the time of the most recently processed write; if
it stays empty or old, the cause is step 5 (filters) or step 3/4 (token/host).

The full interface is only reachable through the authenticated Home Assistant
Ingress. The published port `8127` serves the integration and accepts only
token-protected health and write requests.

## Configuration

The app option `timezone` sets the IANA time zone for navigation, schedules
and presentation; the default is `Europe/Berlin`. Resolution, retention and
other archiving defaults are set globally under **Settings → Archiving** and
can be overridden per entity. Further settings are managed directly in the app
and stored persistently in the app data directory.

The app option `demo_mode` switches the app to a separate instance, completely
isolated from the real data, with synthetic showcase/test data — see
[User guide → Demo mode](docs/en/user-guide.md#demo-mode). Both options require
an add-on restart after a change.

The optional energy dashboard (energy flow as a Sankey diagram, plus
self-sufficiency, cost and CO₂ analysis) can be activated via a tile on the
dashboard overview once the relevant meter entities are being archived.

Before updates or extensive imports, a backup under **System → Backup /
Restore** is recommended.

Executed Symcon and CSV imports are logged permanently under **Import →
Reports**. There, results can be filtered, inspected in detail and downloaded
as JSON. Import previews do not create a report.

Detailed notes on features and limits are in the bundled
[README.md](README.en.md).
