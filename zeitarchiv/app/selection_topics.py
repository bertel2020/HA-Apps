"""Themen des Auswahl-Assistenten außer „Energie“ (ROADMAP 1.33, Phase 1).

Regelbasiert über die Felder des Katalogs (Domain, ``device_class``,
``state_class``, Einheit, Plattform, Energie-Rolle, Namen). Jede Regel liefert
eine Begründung und ob die Entität **empfohlen** oder nur ein **weiterer
Treffer** ist. Die Regeln sind bewusst ehrlich begrenzt: was sich aus Metadaten
nicht sicher erkennen lässt (z. B. ob ein Temperatursensor draußen hängt), wird
über Namenshinweise geraten und landet im Zweifel unter „Weitere Treffer“.

Gemeinsam für alle Themen: versteckte Entitäten und Einstellungs-Entitäten
(``entity_category: config``) fallen weg, Diagnose-Entitäten werden nie
empfohlen, Tages-/Monatszähler werden wie bei Energie nur gezählt.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Collection, Iterable
from typing import Any

from .i18n import N_, tr
from .selection_suggest import _TOTAL_CLASSES, is_derived_counter, result, suggestion

#: Anzeigename und Kurzbeschreibung je Thema; ``sensitive`` zeigt einen Hinweis.
TOPIC_INFO: dict[str, dict[str, Any]] = {
    "energie": {"label": N_("Energie"), "sensitive": False},
    "wetter": {"label": N_("Wetter"), "sensitive": False},
    "klima": {"label": N_("Temperatur & Klima"), "sensitive": False},
    "wasser_gas": {"label": N_("Wasser & Gas"), "sensitive": False},
    "netzwerk": {"label": N_("Netzwerk"), "sensitive": False},
    "anwesenheit": {"label": N_("Anwesenheit"), "sensitive": True},
}

RECOMMENDED, MORE, DERIVED = "recommended", "more", "derived"

_OUTDOOR = re.compile(
    r"(au(ß|ss)en|outdoor|outside|exterior|draussen|draußen|garten|garden|balkon|balcony|terrasse|terrace|wetter|weather)",
    re.IGNORECASE,
)
#: Temperaturen von Geräten selbst (Prozessor, Akku, Platine) — für das Raumklima wertlos.
_DEVICE_TEMP = re.compile(
    r"(cpu|chip|board|platine|prozessor|processor|soc\b|device.temp|ger[äa]tetemp|akku|battery|core|nvme|ssd|hdd|disk)",
    re.IGNORECASE,
)
#: Integrationen, die Wetterdaten liefern (Dienste und Wetterstationen).
WEATHER_PLATFORMS = frozenset({
    "met", "openweathermap", "dwd_weather", "dwd_weather_warnings", "pirateweather", "accuweather",
    "weatherflow", "weatherflow_cloud", "ecowitt", "meteo_france", "meteoalarm", "buienradar", "smhi",
    "tomorrowio", "open_meteo", "brightsky", "aemet", "ipma", "nws", "environment_canada", "metoffice",
    "netatmo", "weatherkit", "knmi", "bom", "zamg", "awattar_weather",
})
#: Integrationen für Router, Netzwerk und Internetanschluss.
NETWORK_PLATFORMS = frozenset({
    "fritz", "fritzbox", "unifi", "speedtestdotnet", "fastdotcom", "upnp", "ping", "netgear", "asuswrt",
    "mikrotik", "tplink_omada", "tplink_router", "openwrt", "luci", "vodafone_station", "glances", "pi_hole",
    "adguard", "nmap_tracker", "keenetic_ndms2", "ubus", "opnsense", "pfsense", "starlink",
})
_SPEED_PLATFORMS = frozenset({"speedtestdotnet", "fastdotcom"})
_NETWORK_NAME = re.compile(r"(wlan|wifi|wi-fi|lan\b|router|internet|dsl|download|upload|ping|latenz|latency)", re.IGNORECASE)


def _hint_text(row: dict[str, Any]) -> str:
    object_id = row["entity_id"].split(".", 1)[1].replace("_", " ")
    return " ".join(str(part) for part in (row.get("name"), object_id, row.get("area"), row.get("device")) if part)


def is_outdoor(row: dict[str, Any]) -> bool:
    """Namenshinweis (außen, outdoor, Garten, Balkon …) in Name, ID, Bereich oder Gerät."""
    return bool(_OUTDOOR.search(_hint_text(row)))


def _counter(row: dict[str, Any]) -> bool:
    return row.get("state_class") in _TOTAL_CLASSES


Rule = Callable[[dict[str, Any]], "tuple[str, str] | str | None"]


def rule_wetter(row: dict[str, Any]) -> tuple[str, str] | str | None:
    domain, dc, platform = row["domain"], row.get("device_class"), row.get("platform")
    if domain == "weather":
        return tr("Wetterdienst: Zustand und Vorhersage"), RECOMMENDED
    if domain != "sensor":
        return None
    weather_source = platform in WEATHER_PLATFORMS
    if dc in {"precipitation", "precipitation_intensity"}:
        if _counter(row) and is_derived_counter(row):
            return DERIVED
        return tr("Niederschlag"), RECOMMENDED
    if dc == "wind_speed":
        return tr("Windgeschwindigkeit"), RECOMMENDED
    if dc == "atmospheric_pressure":
        return tr("Luftdruck"), RECOMMENDED if weather_source or is_outdoor(row) else MORE
    if dc == "irradiance":
        return tr("Sonneneinstrahlung"), RECOMMENDED
    if dc in {"temperature", "humidity"} and (is_outdoor(row) or weather_source):
        if dc == "temperature":
            return tr("Außentemperatur (Name oder Wetterdienst)"), RECOMMENDED
        return tr("Außenluftfeuchte (Name oder Wetterdienst)"), RECOMMENDED
    if dc == "illuminance" and is_outdoor(row):
        return tr("Helligkeit draußen"), MORE
    if (row.get("unit") or "").lower() == "uv index" or re.search(r"\buv\b", _hint_text(row), re.IGNORECASE):
        return tr("UV-Index"), MORE
    if weather_source and dc not in {"timestamp", "enum", "date"}:
        return tr("Weiterer Wert eines Wetterdienstes ({platform})", platform=platform), MORE
    return None


def rule_klima(row: dict[str, Any]) -> tuple[str, str] | str | None:
    domain, dc = row["domain"], row.get("device_class")
    if domain == "climate":
        return tr("Thermostat oder Klimagerät"), RECOMMENDED
    if domain in {"humidifier", "water_heater"}:
        return tr("Luftbefeuchter oder Warmwasserbereiter"), MORE
    if domain != "sensor":
        return None
    if row.get("platform") in WEATHER_PLATFORMS or (dc in {"temperature", "humidity"} and is_outdoor(row)):
        return None  # gehört zum Thema „Wetter“
    if dc == "temperature":
        if _DEVICE_TEMP.search(_hint_text(row)):
            return tr("Gerätetemperatur, kaum für das Raumklima"), MORE
        return tr("Raumtemperatur"), RECOMMENDED
    if dc == "humidity":
        return tr("Luftfeuchtigkeit"), RECOMMENDED
    if dc in {"carbon_dioxide", "co2"}:
        return tr("CO₂-Gehalt der Luft"), RECOMMENDED
    if dc in {"volatile_organic_compounds", "volatile_organic_compounds_parts", "pm25", "pm10", "pm1", "aqi"}:
        return tr("Luftqualität"), MORE
    return None


def rule_wasser_gas(row: dict[str, Any]) -> tuple[str, str] | str | None:
    domain, dc, role = row["domain"], row.get("device_class"), row.get("energy_role")
    if role == "gas":
        return tr("Im Energie-Dashboard als Gas"), RECOMMENDED
    if role == "water":
        return tr("Im Energie-Dashboard als Wasser"), RECOMMENDED
    if role == "water_consumer":
        return tr("Im Energie-Dashboard als Wasserverbraucher"), RECOMMENDED
    if domain == "binary_sensor" and dc == "moisture":
        return tr("Wassermelder"), MORE
    if domain != "sensor":
        return None
    if dc in {"gas", "water"} and _counter(row):
        if is_derived_counter(row):
            return DERIVED
        return (tr("Gaszähler ({state_class})", state_class=row["state_class"]) if dc == "gas"
                else tr("Wasserzähler ({state_class})", state_class=row["state_class"])), MORE
    if dc == "volume_flow_rate":
        return tr("Durchfluss, ändert sich oft"), MORE
    return None


def rule_netzwerk(row: dict[str, Any]) -> tuple[str, str] | str | None:
    domain, dc, platform = row["domain"], row.get("device_class"), row.get("platform")
    network_source = platform in NETWORK_PLATFORMS
    if domain == "binary_sensor" and dc == "connectivity":
        if network_source or _NETWORK_NAME.search(_hint_text(row)):
            return tr("Verbindung (online/offline)"), RECOMMENDED
        return tr("Verbindungsstatus eines Geräts"), MORE
    if domain != "sensor":
        return None
    if platform in _SPEED_PLATFORMS:
        return tr("Messung der Internetgeschwindigkeit"), RECOMMENDED
    if dc == "data_rate":
        if network_source:
            return tr("Durchsatz am Router ({platform})", platform=platform), RECOMMENDED
        return tr("Datenrate"), MORE
    if dc == "data_size":
        if _counter(row) and is_derived_counter(row):
            return DERIVED
        return tr("Übertragene Datenmenge"), MORE
    if dc == "signal_strength" and (network_source or _NETWORK_NAME.search(_hint_text(row))):
        return tr("Signalstärke im WLAN"), MORE
    if network_source and dc not in {"timestamp", "date", "enum"}:
        return tr("Weiterer Wert der Netzwerk-Integration ({platform})", platform=platform), MORE
    return None


def rule_anwesenheit(row: dict[str, Any]) -> tuple[str, str] | str | None:
    domain, dc = row["domain"], row.get("device_class")
    if domain == "person":
        return tr("Person: zu Hause oder unterwegs"), RECOMMENDED
    if domain == "device_tracker":
        return tr("Geräte-Tracker (Handy, Router)"), MORE
    if domain == "binary_sensor" and dc in {"occupancy", "presence"}:
        return tr("Präsenzmelder"), RECOMMENDED
    if domain == "binary_sensor" and dc == "motion":
        return tr("Bewegungsmelder, schaltet oft"), MORE
    return None


RULES: dict[str, Rule] = {
    "wetter": rule_wetter,
    "klima": rule_klima,
    "wasser_gas": rule_wasser_gas,
    "netzwerk": rule_netzwerk,
    "anwesenheit": rule_anwesenheit,
}

_HIGH_RATE = {"wind_speed", "irradiance", "volume_flow_rate", "data_rate", "illuminance"}


def suggest_topic(topic: str, entities: Iterable[dict[str, Any]], archived: Collection[str] = ()) -> dict[str, Any]:
    """Wendet die Regel eines Themas auf den Katalog an (Form wie ``suggest_energy``)."""
    rule = RULES[topic]
    recommended: list[dict[str, Any]] = []
    more: list[dict[str, Any]] = []
    derived = 0
    for row in entities:
        if row.get("hidden") or row.get("entity_category") == "config":
            continue
        verdict = rule(row)
        if verdict is None:
            continue
        if verdict == DERIVED:
            derived += 1
            continue
        reason, bucket = verdict
        if bucket == RECOMMENDED and row.get("entity_category") == "diagnostic":
            bucket = MORE
        high_rate = row.get("device_class") in _HIGH_RATE or (row["domain"] == "binary_sensor" and row.get("device_class") == "motion")
        (recommended if bucket == RECOMMENDED else more).append(suggestion(row, reason, archived, high_rate=high_rate))
    return result(topic, recommended, more, derived)


def topic_label(topic: str | None) -> str:
    """Übersetzter Name eines Themas; unbekannte Themen erscheinen, wie sie heißen."""
    info = TOPIC_INFO.get(topic or "")
    return tr(str(info["label"])) if info else (topic or "")
