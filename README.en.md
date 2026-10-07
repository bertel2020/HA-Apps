# Home Assistant Apps by bertel2020

*[Deutsche Version](README.md)*

This repository provides various apps for Home Assistant. Each app lives in
its own folder and can be installed and updated independently.

<p align="center">
  <a href="https://buymeacoffee.com/bertel2020"><img src="https://img.shields.io/badge/Buy%20Me%20a%20Coffee-support-FFDD00?logo=buy-me-a-coffee&logoColor=black" alt="Buy Me a Coffee"></a>
  <a href="https://ko-fi.com/bertel2020"><img src="https://img.shields.io/badge/Ko--fi-support-FF5E5B?logo=ko-fi&logoColor=white" alt="Ko-fi"></a>
  <a href="https://paypal.me/RobertoMartins"><img src="https://img.shields.io/badge/PayPal-donate-00457C?logo=paypal&logoColor=white" alt="PayPal"></a>
</p>

## Available apps

| App | Version | Description | Documentation |
| --- | --- | --- | --- |
| Zeitarchiv | 1.3.0 | Compact time series archive with Parquet, Ingress, energy dashboard, charts, imports, and safe logging and ingest diagnostics. | [Guide](zeitarchiv/README.en.md) · [User guide](zeitarchiv/docs/en/user-guide.md) |

Further apps can later be added as an additional folder in the repository root
and entered in this table.

## Add the repository to Home Assistant

[![Add add-on repository to My Home Assistant](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fbertel2020%2FHA-Apps)

Or manually:

1. In Home Assistant, open **Settings → Apps → App store**.
2. Open the menu at the top right and select **Repositories**.
3. Add the following address:

   ```text
   https://github.com/bertel2020/HA-Apps
   ```

4. Reload the app store.
5. Select the desired app and install it.

The apps are published as prebuilt images for `amd64` and `aarch64` on
`ghcr.io/bertel2020`; the Home Assistant host does not have to build anything
itself when installing or updating.

## Repository structure

```text
HA-Apps/
├── repository.yaml
├── README.md
├── zeitarchiv/
│   ├── config.yaml
│   ├── Dockerfile
│   ├── README.md
│   ├── docs/
│   └── ...
└── another-app/
    ├── config.yaml
    ├── Dockerfile
    └── ...
```

## Report issues

Please report bugs and suggestions via
[GitHub Issues](https://github.com/bertel2020/HA-Apps/issues).

## License

[MIT](LICENSE)
