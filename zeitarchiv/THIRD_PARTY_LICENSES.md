# Drittanbieter-Komponenten und Lizenzen

Stand: 27. August 2026 · Zeitarchiv App 0.30.1 · Zeitarchiv Integration 0.12.0

Diese Datei dokumentiert die im Zeitarchiv-Quellcode und in der veröffentlichten
App erkennbaren Drittanbieter-Komponenten. Maßgeblich sind die jeweiligen
Original-Lizenztexte der Projekte. Diese Übersicht ersetzt weder diese
Lizenztexte noch eine rechtliche Prüfung.

## Projektcode

| Komponente | Version | Lizenz |
| --- | ---: | --- |
| Zeitarchiv App | 0.30.1 | Apache-2.0 |
| Zeitarchiv Integration | 0.12.0 | Apache-2.0 |
| Eigene Templates, Stylesheets und JavaScript-Dateien der App | 0.30.1 | Apache-2.0 |

App und Integration enthalten jeweils den vollständigen Apache-2.0-Lizenztext
und einen NOTICE-Hinweis. Die Integration wird eigenständig veröffentlicht:

- https://github.com/bertel2020/HA-Zeitarchiv/blob/main/LICENSE
- https://github.com/bertel2020/HA-Zeitarchiv/blob/main/NOTICE

## Python-Laufzeit der App

Die Versionen stammen aus `requirements.txt`. „Direkt“ bezeichnet Einträge aus
`requirements.in`; alle übrigen Pakete werden transitiv installiert.

| Paket | Version | Art | Lizenz |
| --- | ---: | --- | --- |
| FastAPI | 0.141.1 | direkt | MIT |
| Uvicorn | 0.52.4 | direkt | BSD-3-Clause |
| PyArrow | 25.0.1 | direkt | Apache-2.0 |
| Jinja2 | 3.1.6 | direkt | BSD-3-Clause |
| python-multipart | 0.0.32 | direkt | Apache-2.0 |
| annotated-doc | 0.0.5 | transitiv | MIT |
| annotated-types | 0.8.0 | transitiv | MIT |
| AnyIO | 4.14.2 | transitiv | MIT |
| Click | 8.5.0 | transitiv | BSD-3-Clause |
| h11 | 0.16.0 | transitiv | MIT |
| httptools | 0.8.0 | transitiv | MIT |
| idna | 3.19 | transitiv | BSD-3-Clause |
| MarkupSafe | 3.0.3 | transitiv | BSD-3-Clause |
| Pydantic | 2.13.4 | transitiv | MIT |
| pydantic-core | 2.46.4 | transitiv | MIT |
| python-dotenv | 1.2.3 | transitiv | BSD-3-Clause |
| PyYAML | 6.0.3 | transitiv | MIT |
| Starlette | 1.6.0 | transitiv | BSD-3-Clause |
| typing-extensions | 4.16.0 | transitiv | PSF-2.0 |
| typing-inspection | 0.4.4 | transitiv | MIT |
| uvloop | 0.22.1 | transitiv | MIT oder Apache-2.0 |
| watchfiles | 1.2.0 | transitiv | MIT |
| websockets | 17.1 | transitiv | BSD-3-Clause |

Projektseiten und Lizenznachweise:

- FastAPI: https://github.com/fastapi/fastapi
- Uvicorn: https://github.com/Kludex/uvicorn
- PyArrow/Apache Arrow: https://github.com/apache/arrow
- Jinja2 und MarkupSafe: https://github.com/pallets
- Pydantic und pydantic-core: https://github.com/pydantic/pydantic
- uvloop und httptools: https://github.com/MagicStack
- Python-Paketmetadaten: Die mit den Wheels installierten `*.dist-info`-
  Verzeichnisse enthalten die jeweils ausgelieferten Lizenzdateien.

PyArrow enthält nativen Code und kann weitere Drittanbieterbestandteile
einschließen. Für eine Binärdistribution sind deshalb zusätzlich die im
konkreten PyArrow-Wheel enthaltenen Dateien `LICENSE.txt` und `NOTICE.txt`
maßgeblich.

## Browserbibliotheken und Schriftarten

| Komponente | Version | Bereitstellung | Lizenz |
| --- | ---: | --- | --- |
| Alpine.js | 3.14.1 | lokal gebündelt | MIT |
| htmx | 2.0.3 | lokal gebündelt | 0BSD |
| Apache ECharts | 5.5.1 | lokal gebündelt | Apache-2.0 |
| ZRender | 5.6.0 | Bestandteil des ECharts-Bundles | BSD-3-Clause |
| IBM Plex Sans | durch Google Fonts bestimmt | extern geladen | SIL OFL-1.1 |
| IBM Plex Mono | durch Google Fonts bestimmt | extern geladen | SIL OFL-1.1 |

Lizenznachweise:

- Alpine.js: https://github.com/alpinejs/alpine/blob/main/LICENSE.md
- htmx: https://github.com/bigskysoftware/htmx/blob/master/LICENSE
- Apache ECharts: https://github.com/apache/echarts/blob/master/LICENSE
- ZRender: https://github.com/ecomfe/zrender/blob/master/LICENSE
- IBM Plex: https://github.com/IBM/plex/blob/master/LICENSE.txt

## Von Home Assistant bereitgestellte Komponenten

Die Integration deklariert in ihrem `manifest.json` keine eigenen Python-
Abhängigkeiten. Folgende importierte Komponenten werden von der jeweiligen
Home-Assistant-Installation bereitgestellt; ihre konkrete Version richtet sich
daher nach der installierten Home-Assistant-Version.

| Komponente | Version | Lizenz |
| --- | ---: | --- |
| Home Assistant Core | durch Home Assistant bestimmt | Apache-2.0 |
| Requests | durch Home Assistant bestimmt | Apache-2.0 |
| Voluptuous | durch Home Assistant bestimmt | BSD-3-Clause |
| PyYAML | durch Home Assistant bestimmt | MIT |

Lizenznachweise:

- Home Assistant Core: https://github.com/home-assistant/core/blob/dev/LICENSE.md
- Requests: https://github.com/psf/requests/blob/main/LICENSE
- Voluptuous: https://github.com/alecthomas/voluptuous/blob/master/COPYING
- PyYAML: https://github.com/yaml/pyyaml/blob/main/LICENSE

## Container- und Systemkomponenten

| Komponente | Version | Lizenz |
| --- | ---: | --- |
| Python | `3.12-slim`; Patchversion nicht fixiert | PSF-2.0 sowie Lizenzen eingebundener Bestandteile |
| Debian Slim | durch das Python-Image bestimmt | komponentenabhängig |
| nginx | nicht fixiert | BSD-2-Clause |
| SQLite | durch Python/Debian bestimmt | Public Domain |

Lizenznachweise:

- Python: https://github.com/python/cpython/blob/main/LICENSE
- nginx: https://github.com/nginx/nginx/blob/master/LICENSE
- SQLite: https://www.sqlite.org/copyright.html

Das Basis-Image `python:3.12-slim` ist nicht auf einen unveränderlichen Digest
festgelegt. Auch nginx wird während des Builds ohne Versionspin aus den
Debian-Paketquellen installiert. Deshalb lassen sich die exakten Versionen und
sämtlichen transitiven Debian-Pakete erst anhand eines konkret gebauten Images
vollständig bestimmen. Für formale Auslieferungsnachweise sollte zusätzlich
eine SBOM des Release-Images erzeugt werden.

## Entwicklungs- und Testwerkzeuge

| Komponente | Version | Lizenz |
| --- | ---: | --- |
| pytest | im Repository nicht fixiert | MIT |

Jinja2 und PyArrow werden auch in Tests verwendet und sind bereits in der
Python-Laufzeittabelle aufgeführt. Python-Standardbibliotheksmodule werden nicht
einzeln gelistet; sie fallen grundsätzlich unter die Python-Lizenz, soweit in
der Python-Distribution nicht für einzelne eingebundene Komponenten eine
abweichende Lizenz angegeben ist.

## Hinweise für Distributionen

- Vollständige Lizenz- und NOTICE-Texte der tatsächlich ausgelieferten Wheels
  und Binärpakete müssen bei einer Distribution entsprechend den jeweiligen
  Lizenzbedingungen erhalten bleiben.
- Für Apache-2.0-Komponenten sind insbesondere vorhandene NOTICE-Inhalte zu
  übernehmen.
- Die lokal gebündelten Browserbibliotheken sollten zusammen mit ihren
  Lizenztexten ausgeliefert werden.
- Änderungen an Versionen in `requirements.txt`, den Dateien unter
  `app/static/vendor/`, dem Docker-Basisimage oder den Google-Fonts-URLs müssen
  in dieser Übersicht nachgezogen werden.
