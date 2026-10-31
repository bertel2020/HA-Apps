"""Vierte Nachkommastelle: Preise wie 0,0796 €/kWh brauchen sie, damit sie nicht auf 0,08 runden."""

from __future__ import annotations

from app.formatting import DECIMALS_LABELS, decimals_to_int, format_value


def test_four_decimals_are_offered_and_formatted() -> None:
    assert "4" in DECIMALS_LABELS
    assert decimals_to_int("4") == 4
    assert format_value(0.0796, 4).replace(",", ".") == "0.0796"


def test_the_entity_config_accepts_four_decimals(client) -> None:
    from app.main import index

    index.get_or_create_entity("sensor.dec4_preis", "sensor", "measurement", "€/kWh")
    antwort = client.post("/entities/sensor.dec4_preis/config", data={"decimals": "4"})
    assert antwort.status_code == 200
    assert index.get_entity("sensor.dec4_preis")["decimals"] == "4"


def test_the_chart_options_accept_four_decimals(client) -> None:
    from app.main import index

    index.get_or_create_entity("sensor.dec4_chart", "sensor", "measurement", "€/kWh")
    antwort = client.post("/entities/sensor.dec4_chart/chart-options", json={
        "continuous": False, "raw": False, "chart_type": "auto", "show_points": False,
        "show_values": False, "dynamic_y_axis": True, "chart_stats": True,
        "legend_metrics": ["sum"], "legend_style": "chips", "decimals": "4",
    })
    assert antwort.status_code == 200, antwort.text
