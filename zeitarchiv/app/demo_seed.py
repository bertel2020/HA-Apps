"""Demo-Modus: vordefinierte Oberfläche (Energie- und Wetterdashboard, Dashboards, Charts, Tabellen).

``demo_generation`` erzeugt nur die Messwerte. Damit eine frische Demo-Instanz sofort etwas
zeigt, legt dieses Modul danach die Konfiguration darüber an: die Rollen des Energie- und des
Wetterdashboards, gespeicherte Charts und Vergleichstabellen sowie drei Dashboards, auf denen
alles angeheftet ist.

Was angelegt wurde, merkt sich die Instanz in der Einstellung ``demo_seed``. Ein „Neu erzeugen“
räumt genau diese Objekte wieder ab und legt alles frisch an (``reset=True``); ein „Jetzt
ergänzen“ legt nur an, wenn noch nie angelegt wurde — eigene Änderungen in der Demo bleiben
dort also unangetastet.
"""

from __future__ import annotations

import json
import logging

from .demo_generation import localized_id
from .i18n import N_, translate
from .storage.index import Index

logger = logging.getLogger("zeitarchiv.demo_seed")

SETTING_SEED = "demo_seed"

_PREFIX = "sensor.demo_"


def _sensor(name: str) -> str:
    return _PREFIX + name


# --------------------------------------------------------------------------------------------
# Energiedashboard
# --------------------------------------------------------------------------------------------

#: (Entität, Name, Gruppe) — Gruppen als Schlüssel in _GROUPS.
_CONSUMERS = (
    ("waschmaschine_energie", N_("Waschmaschine"), "geraete"),
    ("spuelmaschine_energie", N_("Spülmaschine"), "geraete"),
    ("trockner_energie", N_("Trockner"), "geraete"),
    ("kuehlschrank_energie", N_("Kühlschrank"), "geraete"),
    ("herd_energie", N_("Herd"), "geraete"),
    ("boiler_energie", N_("Boiler"), "geraete"),
    ("wallbox_energie", N_("Wallbox 1"), "mobilitaet"),
    ("wallbox2_energie", N_("Wallbox 2"), "mobilitaet"),
    ("homeoffice_energie", N_("Homeoffice"), "freizeit"),
    ("entertainment_energie", N_("Entertainment"), "freizeit"),
    ("waermepumpe_energie", N_("Wärmepumpe"), "heizung"),
    ("heizstab_energie", N_("Heizstab"), "heizung"),
    ("konvektoren_energie", N_("Konvektoren"), "heizung"),
)
#: Zwischenzähler „Werkstatt“ mit drei Geräten dahinter.
_WORKSHOP = ("werkstatt_energie", N_("Werkstatt"))
_WORKSHOP_CHILDREN = (
    ("werkstatt_kompressor_energie", N_("Kompressor")),
    ("werkstatt_licht_energie", N_("Werkstattlicht")),
    ("werkstatt_ladestation_energie", N_("Ladestation")),
)
_GROUPS = {
    "geraete": N_("Haushaltsgeräte"),
    "mobilitaet": N_("Mobilität"),
    "freizeit": N_("Büro & Freizeit"),
    "heizung": N_("Heizung"),
}


def _energy_config(lid, tr) -> dict:
    verbraucher = [
        {"entity_id": lid(_sensor(eid)), "name": tr(name), "gruppe": tr(_GROUPS[group])}
        for eid, name, group in _CONSUMERS
    ]
    workshop_id = lid(_sensor(_WORKSHOP[0]))
    verbraucher.append({"entity_id": workshop_id, "name": tr(_WORKSHOP[1]), "gruppe": None})
    verbraucher.extend(
        {"entity_id": lid(_sensor(eid)), "name": tr(name), "gruppe": None, "parent": workshop_id}
        for eid, name in _WORKSHOP_CHILDREN
    )
    return {
        "hub_name": None,
        "netzbezug": lid(_sensor("stromzaehler_bezug")),
        "netzbezug_name": None,
        "einspeisung": lid(_sensor("stromzaehler_einspeisung")),
        "einspeisung_name": None,
        "erzeuger": [
            {"entity_id": lid(_sensor("pv_ertrag")), "name": tr(N_("PV-Anlage")), "kwp": 5.2},
            {"entity_id": lid(_sensor("balkonkraftwerk_ertrag_gesamt")), "name": tr(N_("Balkonkraftwerk")), "kwp": 0.8},
        ],
        "speicher": [
            {
                "name": tr(N_("Heimspeicher")),
                "laden_entity_id": lid(_sensor("heimspeicher_geladen_gesamt")),
                "entladen_entity_id": lid(_sensor("heimspeicher_entladen_gesamt")),
                "soc_entity_id": lid(_sensor("heimspeicher_speicher_soc")),
                "capacity_kwh": 10.0,
                "capacity_entity_id": lid(_sensor("heimspeicher_kapazitaet")),
            },
            {
                "name": tr(N_("Balkonspeicher")),
                "laden_entity_id": lid(_sensor("balkonkraftwerk_geladen_gesamt")),
                "entladen_entity_id": lid(_sensor("balkonkraftwerk_entladen_gesamt")),
                "soc_entity_id": lid(_sensor("balkonkraftwerk_speicher_soc")),
                "capacity_kwh": 2.0,
                "capacity_entity_id": lid(_sensor("balkonkraftwerk_speicher_kapazitaet")),
            },
        ],
        "verbraucher": verbraucher,
        "verbraucher_gruppen": [tr(label) for label in _GROUPS.values()],
        "kosten": {
            "preis_netzbezug": lid(_sensor("strompreis_dynamisch")),
            "preis_einspeisung": None,
            "preis_eigenverbrauch": None,
            "preis_netzbezug_fixed": None,
            "preis_einspeisung_fixed": 0.08,
            "preis_eigenverbrauch_fixed": None,
            "grundpreis": None,
            "grundpreis_fixed": 12.0,
        },
        "co2": {"faktor_netzbezug_entity": lid(_sensor("co2_intensitaet")), "faktor_netzbezug_fixed": None},
        "prognose": {
            "rest_heute_entity_id": lid(_sensor("pv_prognose_rest_heute")),
            "morgen_entity_id": lid(_sensor("pv_prognose_morgen")),
        },
    }


def _weather_config(lid, tr) -> dict:
    return {
        "temperature": lid(_sensor("aussentemperatur")),
        "precip_mm": lid(_sensor("niederschlag")),
        "precip_dur": lid("binary_sensor.demo_regensensor"),
        "humidity": lid(_sensor("luftfeuchte")),
        "pressure": lid(_sensor("luftdruck")),
        "wind": lid(_sensor("wind")),
        "gust": lid(_sensor("boe")),
        "radiation": lid(_sensor("strahlung")),
        "illuminance": lid(_sensor("helligkeit_sued")),
        "illuminance_dawn": lid(_sensor("helligkeit_daemmerung")),
        "wind_direction": lid(_sensor("windrichtung")),
        "sun_elevation": lid(_sensor("sonnenhoehe")),
        "pv_power": lid(_sensor("pv_leistung")),
        "extra_temperatures": [
            {"entity_id": lid(_sensor("aussentemperatur_nord")), "name": tr(N_("Nordseite"))},
            # Räume (ROADMAP 1.51): je Raum Temperatur und Feuchte, in „Zeiträume im Vergleich“ unter dem Raum zusammengefasst.
            {"entity_id": lid(_sensor("wohnzimmer_temperatur")), "name": tr(N_("Wohnzimmer Temperatur")), "room": tr(N_("Wohnzimmer"))},
            {"entity_id": lid(_sensor("wohnzimmer_luftfeuchte")), "name": tr(N_("Wohnzimmer Feuchte")), "room": tr(N_("Wohnzimmer"))},
            {"entity_id": lid(_sensor("schlafzimmer_temperatur")), "name": tr(N_("Schlafzimmer Temperatur")), "room": tr(N_("Schlafzimmer"))},
            {"entity_id": lid(_sensor("schlafzimmer_luftfeuchte")), "name": tr(N_("Schlafzimmer Feuchte")), "room": tr(N_("Schlafzimmer"))},
            {"entity_id": lid(_sensor("kueche_temperatur")), "name": tr(N_("Küche Temperatur")), "room": tr(N_("Küche"))},
            {"entity_id": lid(_sensor("kueche_luftfeuchte")), "name": tr(N_("Küche Feuchte")), "room": tr(N_("Küche"))},
            {"entity_id": lid(_sensor("bad_temperatur")), "name": tr(N_("Bad Temperatur")), "room": tr(N_("Bad"))},
            {"entity_id": lid(_sensor("bad_luftfeuchte")), "name": tr(N_("Bad Feuchte")), "room": tr(N_("Bad"))},
            {"entity_id": lid(_sensor("keller_temperatur")), "name": tr(N_("Keller Temperatur")), "room": tr(N_("Keller"))},
            {"entity_id": lid(_sensor("keller_luftfeuchte")), "name": tr(N_("Keller Feuchte")), "room": tr(N_("Keller"))},
        ],
        "heating": [
            {"entity_id": lid(_sensor("waermepumpe_energie")), "name": tr(N_("Wärmepumpe"))},
            {"entity_id": lid(_sensor("heizstab_energie")), "name": tr(N_("Heizstab"))},
            {"entity_id": lid(_sensor("konvektoren_energie")), "name": tr(N_("Konvektoren"))},
        ],
    }


# --------------------------------------------------------------------------------------------
# Charts und Tabellen
# --------------------------------------------------------------------------------------------

#: Schlüssel -> (Name, Entitäten, Zeitraum, Optionen für create_saved_chart)
_CHARTS = {
    "pv_verbrauch": (
        N_("PV-Leistung und Verbrauch"), ("pv_leistung", "gesamtwirkleistung"), "day", {},
    ),
    "netz": (
        N_("Netzbezug und Einspeisung"), ("stromzaehler_bezug", "stromzaehler_einspeisung"), "month",
        {"chart_type": "bar"},
    ),
    "pv_ertrag": (N_("PV-Ertrag je Monat"), ("pv_ertrag",), "year", {"chart_type": "bar", "show_values": True}),
    "speicher": (
        N_("Speicher-Ladestand"), ("heimspeicher_speicher_soc", "balkonkraftwerk_speicher_soc"), "week", {},
    ),
    "preis": (N_("Strompreis dynamisch"), ("strompreis_dynamisch",), "week", {}),
    "wallbox": (N_("Wallbox-Ladevorgänge"), ("wallbox_leistung", "wallbox2_leistung"), "week", {}),
    "anteile": (
        N_("Verbrauch nach Geräten"),
        ("waschmaschine_energie", "spuelmaschine_energie", "trockner_energie", "kuehlschrank_energie",
         "herd_energie", "boiler_energie", "wallbox_energie", "homeoffice_energie", "entertainment_energie",
         "waermepumpe_energie"),
        "year", {"chart_type": "donut"},
    ),
    "temperatur": (
        N_("Außentemperatur"), ("aussentemperatur", "aussentemperatur_nord"), "week", {"average_line": True},
    ),
    "regen": (N_("Niederschlag je Monat"), ("niederschlag",), "year", {"chart_type": "bar", "show_values": True}),
    "wind": (N_("Wind und Böen"), ("wind", "boe"), "week", {}),
    "heizen": (
        N_("Heizenergie je Monat"), ("waermepumpe_energie", "heizstab_energie", "konvektoren_energie"), "year",
        {"chart_type": "bar", "stacked": True},
    ),
    "druck": (N_("Luftdruck"), ("luftdruck",), "month", {}),
    "strahlung": (N_("Sonneneinstrahlung"), ("strahlung", "pv_leistung"), "day", {}),
    "saugroboter": (
        N_("Saugroboter-Status"), ("saugroboter_status",), "month", {"chart_type": "shares"},
    ),
    "heizung_betrieb": (
        N_("Heizung Betrieb"), ("heizung_betrieb",), "week", {"chart_type": "timeline"},
    ),
}

_SIX_COLUMNS = (
    (N_("Tag"), "day", 0), (N_("Vortag"), "day", -1), (N_("Monat"), "month", 0),
    (N_("Vormonat"), "month", -1), (N_("Jahr"), "year", 0), (N_("Vorjahr"), "year", -1),
)
_LONG_COLUMNS = (
    (N_("Monat"), "month", 0), (N_("Vormonat"), "month", -1), (N_("Jahr"), "year", 0), (N_("Vorjahr"), "year", -1),
)
_TABLE_STYLE = {
    "zebra": True, "borders": "horizontal", "density": "comfortable", "header_accent": True,
    "first_col_accent": False, "first_col_bold": True,
}

#: Zeilen: ("entity", Name, Entität) | ("formula", Name, Formel) | ("separator",)
_TABLES = {
    "bilanz": (
        N_("Energiebilanz"), _SIX_COLUMNS,
        (
            ("entity", N_("PV-Ertrag"), "pv_ertrag"),
            ("entity", N_("Einspeisung"), "stromzaehler_einspeisung"),
            ("entity", N_("Netzbezug"), "stromzaehler_bezug"),
            ("separator",),
            ("formula", N_("Eigenverbrauch"), "A-B"),
            ("formula", N_("Gesamtverbrauch"), "A-B+C"),
        ),
    ),
    "geraete": (
        N_("Verbrauch je Gerät"), _LONG_COLUMNS,
        tuple(("entity", name, eid) for eid, name, _group in _CONSUMERS)
        + (("separator",), ("entity", _WORKSHOP[1], _WORKSHOP[0])),
    ),
    "wetter": (
        N_("Wetter im Vergleich"), _SIX_COLUMNS,
        (
            ("entity", N_("Außentemperatur"), "aussentemperatur"),
            ("entity", N_("Niederschlag"), "niederschlag"),
            ("entity", N_("Wind"), "wind"),
            ("entity", N_("Luftdruck"), "luftdruck"),
            ("entity", N_("Sonneneinstrahlung"), "strahlung"),
        ),
    ),
}

#: Dashboards: (Name oder None für das Standard-Dashboard, [("section", Name) | ("chart"|"table", Schlüssel)])
_DASHBOARDS = (
    (None, (
        ("section", N_("Energie")),
        ("chart", "pv_verbrauch"), ("chart", "netz"), ("chart", "pv_ertrag"), ("table", "bilanz"),
        ("section", N_("Wetter")),
        ("chart", "temperatur"), ("chart", "regen"), ("table", "wetter"),
        ("section", N_("Zustände")),
        ("chart", "saugroboter"), ("chart", "heizung_betrieb"),
    )),
    (N_("Energie"), (
        ("section", N_("Erzeugung und Netz")),
        ("chart", "pv_verbrauch"), ("chart", "netz"), ("chart", "pv_ertrag"), ("table", "bilanz"),
        ("section", N_("Speicher und Preis")),
        ("chart", "speicher"), ("chart", "preis"),
        ("section", N_("Verbraucher")),
        ("chart", "anteile"), ("chart", "wallbox"), ("table", "geraete"),
    )),
    (N_("Wetter"), (
        ("section", N_("Verlauf")),
        ("chart", "temperatur"), ("chart", "wind"), ("chart", "druck"), ("chart", "strahlung"),
        ("section", N_("Monate und Jahre")),
        ("chart", "regen"), ("chart", "heizen"), ("table", "wetter"),
    )),
)


# --------------------------------------------------------------------------------------------
# Anlegen und Abräumen
# --------------------------------------------------------------------------------------------

def is_seeded(index: Index) -> bool:
    return bool(index.get_setting(SETTING_SEED, ""))


def remove_seed(index: Index) -> None:
    """Löscht genau die Objekte, die ein früherer Lauf angelegt hat (eigene bleiben stehen)."""
    try:
        record = json.loads(index.get_setting(SETTING_SEED, "") or "{}")
    except (TypeError, ValueError):
        record = {}
    for dashboard_id, section_id in record.get("sections", []):
        index.remove_dashboard_section(dashboard_id, section_id)
    for chart_id in record.get("charts", []):
        index.delete_saved_chart(chart_id)
    for table_id in record.get("tables", []):
        index.delete_saved_table(table_id)
    for dashboard_id in record.get("dashboards", []):
        index.delete_dashboard(dashboard_id)
    index.set_setting(SETTING_SEED, "")


def seed(index: Index, language: str, *, reset: bool) -> bool:
    """Legt die Demo-Oberfläche an. Bei ``reset`` wird zuvor Angelegtes abgeräumt; ohne ``reset``
    passiert nichts, wenn schon einmal angelegt wurde. Liefert, ob etwas angelegt wurde."""
    if not reset and is_seeded(index):
        return False
    remove_seed(index)

    def lid(entity_id: str) -> str:
        return localized_id(entity_id, language)

    def tr(text: str) -> str:
        return translate(text, language)

    def known(entity_id: str) -> bool:
        return index.get_entity(lid(entity_id)) is not None

    record: dict[str, list] = {"charts": [], "tables": [], "dashboards": [], "sections": []}
    charts: dict[str, int] = {}
    tables: dict[str, int] = {}

    for key, (name, entities, range_key, options) in _CHARTS.items():
        ids = [lid(_sensor(e) if "." not in e else e) for e in entities]
        if not all(index.get_entity(i) is not None for i in ids):
            logger.warning("Demo-Chart %s übersprungen: Entität fehlt", key)
            continue
        charts[key] = index.create_saved_chart(
            index.free_name_for("saved_charts", tr(name)), ids, range_key, False, **options,
        )
        record["charts"].append(charts[key])

    for key, (name, columns, rows) in _TABLES.items():
        table_columns = [
            {"label": tr(label), "range_key": range_key, "offset": offset, "year_over_year": False}
            for label, range_key, offset in columns
        ]
        table_rows = []
        for row in rows:
            if row[0] == "separator":
                table_rows.append({"label": "", "row_type": "separator", "entity_ids": [], "formula": "", "bold": False})
            elif row[0] == "formula":
                table_rows.append({
                    "label": tr(row[1]), "row_type": "formula", "entity_ids": [], "formula": row[2], "bold": True,
                })
            elif known(_sensor(row[2])):
                table_rows.append({
                    "label": tr(row[1]), "row_type": "entity", "entity_ids": [lid(_sensor(row[2]))],
                    "formula": "", "bold": False,
                })
        tables[key] = index.create_saved_table(
            index.free_name_for("saved_tables", tr(name)), table_columns, table_rows, _TABLE_STYLE,
        )
        record["tables"].append(tables[key])

    for name, items in _DASHBOARDS:
        if name is None:
            dashboard_id = index.get_default_dashboard_id()
        else:
            dashboard_id = index.create_dashboard(index.free_name_for("dashboards", tr(name)))
            record["dashboards"].append(dashboard_id)
        for kind, value in items:
            if kind == "section":
                section_id = index.add_dashboard_section(dashboard_id, tr(value))
                if section_id is not None:
                    record["sections"].append([dashboard_id, section_id])
                continue
            item_id = (charts if kind == "chart" else tables).get(value)
            if item_id is not None:
                index.pin_item_to_dashboard(dashboard_id, kind, item_id)

    from . import energiedashboard_routes, wetter_routes

    energy = _energy_config(lid, tr)
    energiedashboard_routes._save_config(index, energy)
    index.set_setting(energiedashboard_routes.SETTING_ENABLED, "1")
    role_ids = [energy["netzbezug"], energy["einspeisung"]]
    role_ids += [row["entity_id"] for row in energy["erzeuger"] + energy["verbraucher"]]
    for storage in energy["speicher"]:
        role_ids += [storage["laden_entity_id"], storage["entladen_entity_id"]]
    energiedashboard_routes.sync_hourly_rollup_flags(index, role_ids)
    index.invalidate_heatmap_weekday_snapshots()

    wetter_routes._save_config(index, _weather_config(lid, tr))
    index.set_setting(wetter_routes.SETTING_ENABLED, "1")

    index.set_setting(SETTING_SEED, json.dumps(record))
    return True
